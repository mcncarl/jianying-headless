"""Bounded native codec, snapshot and lock primitives.

Mechanically extracted from the project's 11.4 IO helper; registered-sound
operations, bookmarks, account-specific constants and the legacy CLI are not
included. The official engine remains an external local dependency.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - native mutation is macOS-only
    class _WindowsFcntl:
        LOCK_EX = LOCK_NB = LOCK_UN = 0

        @staticmethod
        def flock(descriptor, operation):
            raise RuntimeError('The native Jianying codec/locking bridge is not available on Windows')

    fcntl = _WindowsFcntl()

import hashlib

import json

import os

import stat

import subprocess

import threading

from dataclasses import dataclass

from pathlib import Path

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REQUIRED_CODEC_SHA256 = (
    "b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d"
)

APP_BUNDLE = Path("/Applications/VideoFusion-macOS.app")

WORK_DIR = Path(__file__).resolve().parent

CODEC_PATH = WORK_DIR / "jy14_codec_hardened_11_4"

MAIN_EXECUTABLE_RELATIVE = Path("Contents/MacOS/VideoFusion-macOS")

READ_CHUNK_SIZE = 1024 * 1024

MAX_REGULAR_FILE_BYTES = 256 * 1024 * 1024

COMMAND_TIMEOUT_SECONDS = 120

MAX_METADATA_PLAINTEXT_BYTES = 16 * 1024 * 1024

class ApplyError(RuntimeError):
    """The requested live mutation could not be proven safe."""

@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    content: bytes
    sha256: str
    mode: int
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    parent_device: int
    parent_inode: int

JsonObject = Dict[str, Any]

def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))

def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags

def _open_directory_secure(path: Path, label: str) -> int:
    """Open an absolute directory component-by-component without symlinks."""

    absolute = _absolute_lexical(path)
    try:
        descriptor = os.open(absolute.anchor, _directory_open_flags())
    except OSError as exc:
        raise ApplyError("cannot open filesystem root for %s" % label) from exc
    try:
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(
                    component, _directory_open_flags(), dir_fd=descriptor
                )
            except OSError as exc:
                raise ApplyError(
                    "%s contains an unavailable or symlinked directory component"
                    % label
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ApplyError("%s is not a directory" % label)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

def _directory_identity(path: Path, label: str) -> Tuple[int, int]:
    if os.name == 'nt':
        try:
            metadata = Path(path).stat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise ApplyError('%s is not a directory' % label)
            return metadata.st_dev, metadata.st_ino
        except OSError as exc:
            raise ApplyError('%s could not be opened' % label) from exc
    descriptor = _open_directory_secure(path, label)
    try:
        metadata = os.fstat(descriptor)
        return metadata.st_dev, metadata.st_ino
    finally:
        os.close(descriptor)

def _open_regular_readonly(
    path: Path, label: str, *, max_bytes: Optional[int] = MAX_REGULAR_FILE_BYTES
) -> Tuple[int, os.stat_result, Tuple[int, int]]:
    path = _absolute_lexical(path)
    if os.name == 'nt':
        # The descriptor-relative POSIX walk below is intentionally retained
        # for macOS. Windows has no equivalent dir_fd/openat contract in the
        # standard library, so use the strongest available lstat/read-only
        # check and keep the native codec bridge unavailable on this platform.
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or path.is_symlink():
                raise ApplyError('%s must be a non-symlink regular file' % label)
            if max_bytes is not None and before.st_size > max_bytes:
                raise ApplyError('%s exceeds the safety size limit' % label)
            descriptor = os.open(str(path), os.O_RDONLY)
            opened = os.fstat(descriptor)
            parent = path.parent.stat()
            return descriptor, opened, (parent.st_dev, parent.st_ino)
        except OSError as exc:
            raise ApplyError('%s could not be opened' % label) from exc
    parent_fd = _open_directory_secure(path.parent, "%s parent" % label)
    parent_meta = os.fstat(parent_fd)
    parent_identity = (parent_meta.st_dev, parent_meta.st_ino)
    try:
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ApplyError("%s must be a non-symlink regular file" % label)
        if max_bytes is not None and before.st_size > max_bytes:
            raise ApplyError("%s exceeds the safety size limit" % label)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, dir_fd=parent_fd)
    except BaseException:
        os.close(parent_fd)
        raise
    os.close(parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ApplyError("%s changed to a non-regular file" % label)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ApplyError("%s changed while opening" % label)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, opened, parent_identity

def _metadata_identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )

def _read_regular_bytes(
    path: Path, label: str, *, max_bytes: Optional[int] = MAX_REGULAR_FILE_BYTES
) -> Tuple[bytes, os.stat_result, Tuple[int, int]]:
    descriptor, opened, parent_identity = _open_regular_readonly(
        path, label, max_bytes=max_bytes
    )
    chunks: List[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, READ_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ApplyError("%s grew beyond the safety size limit" % label)
        after = os.fstat(descriptor)
        if _metadata_identity(after) != _metadata_identity(opened):
            raise ApplyError("%s changed while reading" % label)
    finally:
        os.close(descriptor)
    return b"".join(chunks), after, parent_identity

def _snapshot_file(
    path: Path, label: str, *, max_bytes: Optional[int] = MAX_REGULAR_FILE_BYTES
) -> FileSnapshot:
    path = _absolute_lexical(path)
    content, metadata, parent_identity = _read_regular_bytes(
        path, label, max_bytes=max_bytes
    )
    if _directory_identity(path.parent, "%s parent recheck" % label) != parent_identity:
        raise ApplyError("%s parent changed while snapshotting" % label)
    return FileSnapshot(
        path=path,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        mode=stat.S_IMODE(metadata.st_mode),
        size=metadata.st_size,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
        parent_device=parent_identity[0],
        parent_inode=parent_identity[1],
    )

def _revalidate_snapshot(snapshot: FileSnapshot, phase: str) -> None:
    if _directory_identity(snapshot.path.parent, "%s parent" % phase) != (
        snapshot.parent_device,
        snapshot.parent_inode,
    ):
        raise ApplyError("%s parent identity changed" % phase)
    current = _snapshot_file(snapshot.path, phase)
    if (
        current.sha256 != snapshot.sha256
        or current.device != snapshot.device
        or current.inode != snapshot.inode
        or current.size != snapshot.size
        or current.mtime_ns != snapshot.mtime_ns
        or current.ctime_ns != snapshot.ctime_ns
    ):
        raise ApplyError("%s changed after its transaction snapshot" % phase)

def _parse_strict_json(payload: bytes, label: str) -> JsonObject:
    def object_without_duplicates(pairs: Iterable[Tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise ApplyError("%s contains a duplicate object key" % label)
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ApplyError("%s contains a non-finite number" % label)

    try:
        value = json.loads(
            payload.decode("utf-8-sig"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_nonfinite,
        )
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except ApplyError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ApplyError("%s is not strict UTF-8 JSON" % label) from exc
    if not isinstance(value, dict):
        raise ApplyError("%s root must be an object" % label)
    return value

def _write_all(descriptor: int, content: bytes, destination: Path) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short write to %s" % destination)
        offset += written

def _hash_file(path: Path, label: str) -> str:
    return _snapshot_file(path, label, max_bytes=None).sha256

def _tool_pin(path: Path, expected_sha256: str, label: str) -> None:
    actual = _hash_file(path, label)
    if actual != expected_sha256:
        raise ApplyError(
            "%s SHA256 mismatch: expected %s, found %s"
            % (label, expected_sha256, actual)
        )
    if not os.access(path, os.X_OK) and label == "codec":
        raise ApplyError("codec is not executable")

def _process_rows() -> List[Tuple[int, str]]:
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApplyError("cannot verify whether JianYing is closed") from exc
    if result.returncode != 0:
        raise ApplyError("cannot verify whether JianYing is closed")
    rows: List[Tuple[int, str]] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        pid_text, separator, command = line.partition(" ")
        if separator and pid_text.isdigit():
            rows.append((int(pid_text), command.lstrip()))
    return rows

def _ensure_editor_closed(confirm_editor_closed: bool) -> Dict[str, Any]:
    if confirm_editor_closed is not True:
        raise ApplyError("--confirm-editor-closed is required")
    executable = str(_absolute_lexical(APP_BUNDLE / MAIN_EXECUTABLE_RELATIVE))
    main_pids = [
        pid
        for pid, command in _process_rows()
        if command == executable or command.startswith(executable + " ")
    ]
    if main_pids:
        raise ApplyError("JianYing main editor process is still running")
    return {"confirmed_by_user": True, "main_process_closed": True}

def _safe_environment() -> Dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DYLD_") and key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment

def _decrypt_metadata_in_memory(encrypted_path: Path) -> JsonObject:
    """Decrypt metadata through a pipe; plaintext never receives a pathname."""

    _tool_pin(CODEC_PATH, REQUIRED_CODEC_SHA256, "codec")
    read_fd, write_fd = os.pipe()
    chunks: List[bytes] = []
    read_error: List[BaseException] = []

    def reader() -> None:
        total = 0
        try:
            while True:
                chunk = os.read(read_fd, READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_METADATA_PLAINTEXT_BYTES:
                    raise ApplyError("decrypted metadata exceeds the in-memory limit")
                chunks.append(chunk)
        except BaseException as exc:  # retained for the main transaction thread
            read_error.append(exc)
        finally:
            os.close(read_fd)

    thread = threading.Thread(target=reader, name="jy14-meta-pipe-reader", daemon=True)
    thread.start()
    process: Optional[subprocess.Popen] = None
    try:
        process = subprocess.Popen(
            [str(CODEC_PATH), "decrypt-fd", str(encrypted_path), str(write_fd)],
            cwd=WORK_DIR,
            env=_safe_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(write_fd,),
        )
    except OSError as exc:
        os.close(write_fd)
        thread.join(timeout=5)
        raise ApplyError("metadata in-memory codec could not start") from exc
    os.close(write_fd)
    try:
        _stdout, _stderr = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ApplyError("metadata in-memory codec timed out") from exc
    finally:
        thread.join(timeout=10)
    if thread.is_alive():
        raise ApplyError("metadata in-memory pipe did not close")
    if read_error:
        raise ApplyError("metadata in-memory pipe read failed") from read_error[0]
    if process.returncode != 0:
        raise ApplyError("metadata in-memory codec failed")
    plaintext = b"".join(chunks)
    if not plaintext:
        raise ApplyError("metadata in-memory codec returned empty plaintext")
    return _parse_strict_json(plaintext, "decrypted draft metadata")

def _encrypt_metadata_from_memory(plaintext: bytes, encrypted_path: Path) -> None:
    """Feed metadata plaintext through an anonymous pipe to the pinned codec."""

    if not plaintext or len(plaintext) > MAX_METADATA_PLAINTEXT_BYTES:
        raise ApplyError("metadata plaintext size is invalid")
    _tool_pin(CODEC_PATH, REQUIRED_CODEC_SHA256, "codec")
    read_fd, write_fd = os.pipe()
    write_error: List[BaseException] = []

    def writer() -> None:
        try:
            _write_all(write_fd, plaintext, Path("<anonymous metadata pipe>"))
        except BaseException as exc:
            write_error.append(exc)
        finally:
            os.close(write_fd)

    thread = threading.Thread(target=writer, name="jy14-meta-pipe-writer", daemon=True)
    thread.start()
    try:
        process = subprocess.Popen(
            [str(CODEC_PATH), "encrypt-fd", str(read_fd), str(encrypted_path)],
            cwd=WORK_DIR,
            env=_safe_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(read_fd,),
        )
    except OSError as exc:
        os.close(read_fd)
        thread.join(timeout=5)
        raise ApplyError("metadata in-memory encrypt codec could not start") from exc
    os.close(read_fd)
    try:
        _stdout, _stderr = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ApplyError("metadata in-memory encrypt codec timed out") from exc
    finally:
        thread.join(timeout=10)
    if thread.is_alive() or write_error:
        raise ApplyError("metadata in-memory pipe write failed")
    if process.returncode != 0:
        raise ApplyError("metadata in-memory encrypt codec failed")
    os.chmod(encrypted_path, 0o600)

def _acquire_directory_transaction_lock(
    directory: Path, label: str
) -> Tuple[int, Tuple[int, int]]:
    initial = _directory_identity(directory, "%s before locking" % label)
    descriptor = _open_directory_secure(directory, "%s transaction lock" % label)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        os.close(descriptor)
        raise ApplyError("another audited transaction holds the %s lock" % label) from exc
    metadata = os.fstat(descriptor)
    identity = (metadata.st_dev, metadata.st_ino)
    if identity != initial:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise ApplyError("%s changed while acquiring its transaction lock" % label)
    return descriptor, identity

def _release_directory_transaction_lock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)
