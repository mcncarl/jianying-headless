"""Windows implementation of the bounded IO/codec primitives.

:mod:`runtime_io` is the macOS implementation: it opens directories through
``dir_fd`` and ``O_NOFOLLOW``, pipes plaintext to a pinned helper executable
with ``pass_fds``, and takes an exclusive lock with :func:`fcntl.flock`. None of
those exist on Windows, so this module provides the same eight-entry interface
with native equivalents:

===============================  =======================  =====================
macOS primitive                  Windows equivalent       note
===============================  =======================  =====================
``fcntl.flock``                  named mutex              release is explicit
``/bin/ps``                      Toolhelp32 snapshot      matches image basename
``dir_fd`` traversal             reparse-point checks     weaker, see below
pinned ``jy14_codec`` executable ``lvve::EncryptUtils``    in-process ctypes
``pass_fds`` pipe                direct byte buffers      no fd inheritance
===============================  =======================  =====================

Two guarantees are deliberately weaker than macOS, and callers must not treat
them as equivalent:

* Windows has no ``dir_fd``, so a path component can in principle be swapped
  between the check and the open. Every component is still rejected if it is a
  reparse point, and the file identity is re-verified afterwards, which closes
  the practical window but not the race itself.
* Windows has no metadata-change timestamp, so the snapshot compares creation
  time where macOS compared ``st_ctime_ns``.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_codec  # noqa: E402  (sibling module, loaded by path)

APP_BUNDLE = Path(os.environ.get("JY14_APP_BUNDLE", "")) if os.environ.get(
    "JY14_APP_BUNDLE") else None
MAIN_EXECUTABLE_NAME = "JianyingPro.exe"

READ_CHUNK_SIZE = 1024 * 1024
MAX_REGULAR_FILE_BYTES = 256 * 1024 * 1024
MAX_METADATA_PLAINTEXT_BYTES = 16 * 1024 * 1024

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_NO_MORE_FILES = 18


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
    attributes: int


JsonObject = Dict[str, Any]


# --------------------------------------------------------------------------
# paths and identity
# --------------------------------------------------------------------------

def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _attributes(path: Path) -> int:
    try:
        return os.lstat(path).st_file_attributes
    except (OSError, AttributeError):
        return 0


def is_reparse_point(path: Path) -> bool:
    """True for symlinks, junctions and other reparse points."""

    return bool(_attributes(path) & FILE_ATTRIBUTE_REPARSE_POINT)


def require_no_reparse_point(path: Path, label: str) -> None:
    path = _absolute_lexical(path)
    parts = [path, *path.parents]
    for candidate in parts:
        if candidate == candidate.parent:
            break
        if candidate.exists() and is_reparse_point(candidate):
            raise ApplyError("%s traverses a reparse point: %s" % (label, candidate))


def _identity(metadata: os.stat_result) -> Tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _metadata_identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


# --------------------------------------------------------------------------
# bounded regular-file access
# --------------------------------------------------------------------------

def _read_regular_bytes(
    path: Path, label: str, *, max_bytes: Optional[int] = MAX_REGULAR_FILE_BYTES
) -> Tuple[bytes, os.stat_result]:
    path = _absolute_lexical(path)
    require_no_reparse_point(path, "%s parent" % label)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ApplyError("%s is unavailable" % label) from exc
    if is_reparse_point(path):
        raise ApplyError("%s must not be a reparse point" % label)
    if not stat.S_ISREG(before.st_mode):
        raise ApplyError("%s must be a regular file" % label)
    if max_bytes is not None and before.st_size > max_bytes:
        raise ApplyError("%s exceeds the safety size limit" % label)

    chunks: List[bytes] = []
    total = 0
    try:
        with open(path, "rb", buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(before):
                raise ApplyError("%s changed while opening" % label)
            while True:
                chunk = stream.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ApplyError("%s grew beyond the safety size limit" % label)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ApplyError("%s could not be read" % label) from exc
    if _metadata_identity(after) != _metadata_identity(opened):
        raise ApplyError("%s changed while reading" % label)
    return b"".join(chunks), after


def _snapshot_file(
    path: Path, label: str, *, max_bytes: Optional[int] = MAX_REGULAR_FILE_BYTES
) -> FileSnapshot:
    path = _absolute_lexical(path)
    content, metadata = _read_regular_bytes(path, label, max_bytes=max_bytes)
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
        attributes=metadata.st_file_attributes,
    )


def _revalidate_snapshot(snapshot: FileSnapshot, phase: str) -> None:
    current = _snapshot_file(snapshot.path, phase)
    if (current.sha256 != snapshot.sha256
            or current.device != snapshot.device
            or current.inode != snapshot.inode
            or current.size != snapshot.size):
        raise ApplyError("%s changed after its transaction snapshot" % phase)


def _hash_file(path: Path, label: str) -> str:
    return _snapshot_file(path, label, max_bytes=None).sha256


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


def _write_atomic(path: Path, content: bytes) -> None:
    """Replace ``path`` with ``content`` through a sibling temporary file."""

    path = _absolute_lexical(path)
    temporary = path.parent / (".%s.headless-%d.tmp" % (path.name, os.getpid()))
    try:
        with open(temporary, "wb", buffering=0) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ApplyError("cannot write %s" % path) from exc


# --------------------------------------------------------------------------
# codec (in-process, no compiled helper and no inherited file descriptors)
# --------------------------------------------------------------------------

def _resolve_app_bundle() -> Path:
    try:
        installation = windows_platform.discover()
    except ValueError as exc:
        raise ApplyError("no JianYing installation was found") from exc
    # Doctor chooses the newest installation. An override must not redirect
    # native loading to a different (possibly unreviewed) engine afterward.
    if APP_BUNDLE is not None and APP_BUNDLE.resolve() != installation.directory.resolve():
        raise ApplyError("JY14_APP_BUNDLE differs from the installation selected by doctor")
    review = windows_platform.review_status(installation)
    if not review["reviewed"]:
        raise ApplyError("JianYing installation is not reviewed: %s" % review.get("reason"))
    return installation.directory


def _codec():
    return windows_codec.codec_for(_resolve_app_bundle())


def _decrypt_metadata_in_memory(encrypted_path: Path) -> JsonObject:
    """Decrypt draft metadata entirely in memory; plaintext never hits disk."""

    ciphertext, _metadata = _read_regular_bytes(
        encrypted_path, "encrypted draft metadata", max_bytes=MAX_REGULAR_FILE_BYTES
    )
    try:
        plaintext = _codec().decrypt(ciphertext)
    except windows_codec.CodecUnavailable as exc:
        raise ApplyError("metadata codec unavailable: %s" % exc) from exc
    if len(plaintext) > MAX_METADATA_PLAINTEXT_BYTES:
        raise ApplyError("decrypted metadata exceeds the in-memory limit")
    return _parse_strict_json(plaintext, "decrypted draft metadata")


def _encrypt_metadata_from_memory(plaintext: bytes, encrypted_path: Path) -> None:
    if not plaintext or len(plaintext) > MAX_METADATA_PLAINTEXT_BYTES:
        raise ApplyError("metadata plaintext size is invalid")
    try:
        ciphertext = _codec().encrypt(plaintext)
    except windows_codec.CodecUnavailable as exc:
        raise ApplyError("metadata codec unavailable: %s" % exc) from exc
    _write_atomic(encrypted_path, ciphertext)


# --------------------------------------------------------------------------
# editor detection
# --------------------------------------------------------------------------

class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _running_image_names() -> List[str]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(_ProcessEntry32W)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, INVALID_HANDLE_VALUE):
        raise ApplyError("cannot verify whether JianYing is closed")
    names: List[str] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        ctypes.set_last_error(0)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            if ctypes.get_last_error() == ERROR_NO_MORE_FILES:
                return names
            raise ApplyError("cannot enumerate running processes")
        while True:
            names.append(entry.szExeFile)
            ctypes.set_last_error(0)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                    raise ApplyError("cannot enumerate running processes")
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def _ensure_editor_closed(confirm_editor_closed: bool) -> Dict[str, Any]:
    if confirm_editor_closed is not True:
        raise ApplyError("--confirm-editor-closed is required")
    # Windows cannot prove which installation a bare image name belongs to, so
    # every process running the editor image is treated as a blocker.
    matches = [name for name in _running_image_names()
               if name.lower() == MAIN_EXECUTABLE_NAME.lower()]
    if matches:
        raise ApplyError("JianYing editor process is still running")
    return {"confirmed_by_user": True, "main_process_closed": True,
            "detection": "image-name"}


# --------------------------------------------------------------------------
# transaction lock (named mutex; the closest analogue to flock)
# --------------------------------------------------------------------------

_LOCK_PREFIX = "Local\\jy14-headless-"
_OPENED_MUTEXES: Dict[int, int] = {}


def _acquire_directory_transaction_lock(
    directory: Path, label: str
) -> Tuple[int, Tuple[int, int]]:
    directory = _absolute_lexical(directory)
    require_no_reparse_point(directory, label)
    if not directory.is_dir():
        raise ApplyError("%s is not a directory" % label)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                      ctypes.c_wchar_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    digest = hashlib.sha256(str(directory).casefold().encode("utf-8")).hexdigest()
    handle = kernel32.CreateMutexW(None, False, _LOCK_PREFIX + digest[:32])
    if not handle:
        raise ApplyError("cannot create the %s transaction lock" % label)
    # WAIT_OBJECT_0 == acquired, WAIT_ABANDONED == previous holder died.
    # WAIT_TIMEOUT == another transaction is running.
    status = kernel32.WaitForSingleObject(handle, 0)
    if status not in (0x00000000, 0x00000080):
        kernel32.CloseHandle(handle)
        raise ApplyError("another audited transaction holds the %s lock" % label)
    metadata = os.stat(directory)
    _OPENED_MUTEXES[handle] = handle
    return handle, _identity(metadata)


def _release_directory_transaction_lock(descriptor: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    if _OPENED_MUTEXES.pop(descriptor, None) is not None:
        kernel32.ReleaseMutex(descriptor)
    kernel32.CloseHandle(descriptor)


def _safe_environment() -> Dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("DYLD_", "LD_")) and key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


# Imported late: windows_platform imports this module for its own IO needs.
import windows_platform  # noqa: E402
