"""Platform dispatch for the headless builder.

Everything the builder needs that is genuinely OS-bound lives behind this
module, so :mod:`jy14_headless` and the ``headless_draft.py`` entry point stay
platform-neutral:

===========================  ==========================  =====================
concern                      macOS                       Windows
===========================  ==========================  =====================
draft store                  ``~/Movies/JianyingPro/...`` ``%LOCALAPPDATA%\\...``
application identity         ``Info.plist`` + codesign   version dir + DLL hash
codec                        compiled ``jy14_codec``     ``videoeditor.dll``
timeline file name           ``draft_info.json``         ``draft_content.json``
exclusive placement          ``renamex_np(RENAME_EXCL)`` ``os.rename``
extended attributes          ``/usr/bin/xattr``          not applicable
directory lock sync          ``os.fsync(fd)``            mutex handle
===========================  ==========================  =====================

The macOS behaviour is deliberately preserved bit-for-bit; Windows support is
additive. Anything the Windows column cannot express faithfully is reported as
an explicit limitation rather than silently approximated — see
:func:`capability_notes`.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

IS_WINDOWS = os.name == 'nt'
IS_MACOS = sys.platform == 'darwin'

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BRIDGE = PROJECT_ROOT / 'bridge'
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode()


def normalized(path):
    """Comparable spelling of a draft path.

    The editor is inconsistent about separators: ``root_meta_info.json`` records
    ``root_path`` with forward slashes on Windows while ``draft_meta_info.json``
    records ``draft_root_path`` with backslashes. Comparisons must not depend on
    which one a caller happened to read.
    """

    return os.path.normcase(os.path.normpath(str(path))).replace('\\', '/')


CAPABILITY_NOTES = {
    'darwin': [
        'extended attributes are preserved and verified on the home index',
        'placement uses renamex_np(RENAME_EXCL) for an atomic exclusive move',
        'the codec runs in an isolated process and plaintext is passed over a pipe',
    ],
    'win32': [
        'NTFS alternate data streams are not used by the editor for drafts, so '
        'attribute preservation is a recorded no-op rather than a verification',
        'placement uses os.rename, which already fails when the destination '
        'exists, giving the same exclusive semantics without renameat2 flags',
        'the codec runs in-process through ctypes; plaintext still never '
        'receives a pathname',
        'path components are checked for reparse points, but Windows has no '
        'dir_fd, so the check-then-open race cannot be fully closed',
    ],
}


def capability_notes():
    return list(CAPABILITY_NOTES.get(sys.platform, []))


# ---------------------------------------------------------------------------
# macOS backend (unchanged behaviour, re-exported for the shared call sites)
# ---------------------------------------------------------------------------

def _macos_exclusive_rename(src, dst):
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.renamex_np
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
    if fn(os.fsencode(src), os.fsencode(dst), 0x4):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(dst))


def _macos_read_xattrs(path):
    names = subprocess.check_output(['/usr/bin/xattr', str(path)], text=True).splitlines()
    return {name: bytes.fromhex(subprocess.check_output(
        ['/usr/bin/xattr', '-px', name, str(path)], text=True)) for name in names}


def _macos_copy_xattrs(source_attrs, destination, audit, write):
    """Preserve all user/security attributes; record the OS-owned per-file provenance separately."""
    write(audit / 'index-xattrs-before.json', {k: v.hex() for k, v in source_attrs.items()})
    for key, value in source_attrs.items():
        subprocess.run(['/usr/bin/xattr', '-wx', key, value.hex(), str(destination)],
                       check=True, capture_output=True)
    copied = _macos_read_xattrs(destination)
    write(audit / 'index-xattrs-staged.json', {k: v.hex() for k, v in copied.items()})
    # A bounded copy experiment on this Mac showed xattr -w returns success but the
    # OS assigns a different provenance value to the new inode. Never strip it,
    # quarantine, or any other attribute to force an equality result.
    changed = sorted(k for k in set(source_attrs) | set(copied)
                     if source_attrs.get(k) != copied.get(k))
    if set(changed) - {'com.apple.provenance'}:
        raise ValueError(
            'Extended attributes could not be preserved before commit: '
            + ', '.join(changed)
            + '. No security attribute was stripped. If com.apple.macl differs, this environment '
              'needs a reviewed permission-preservation adapter; do not disable SIP or TCC.')
    if ('com.apple.provenance' in source_attrs) != ('com.apple.provenance' in copied):
        raise ValueError('OS provenance attribute disappeared or unexpectedly appeared')
    return copied, changed


def _macos_sync_lock(lock):
    os.fsync(lock)


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------

def _windows_exclusive_rename(src, dst):
    """Move ``src`` to ``dst`` only when ``dst`` does not exist.

    ``os.rename`` on Windows raises ``FileExistsError`` instead of replacing,
    which is the same guarantee ``RENAME_EXCL`` provides on macOS.
    """
    os.rename(str(src), str(dst))


def _windows_read_xattrs(path):
    """Windows has no xattr equivalent the editor relies on; report none."""
    return {}


def _windows_copy_xattrs(source_attrs, destination, audit, write):
    write(audit / 'index-xattrs-before.json', dict(source_attrs))
    write(audit / 'index-xattrs-staged.json', {})
    if source_attrs:
        raise ValueError('unexpected attributes recorded on Windows')
    return {}, []


def _windows_sync_lock(lock):
    """The Windows transaction lock is a mutex handle, not a file descriptor."""
    return None


# ---------------------------------------------------------------------------
# public platform surface
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import runtime_io_windows as _io
    import windows_platform as _app

    DRAFT_ROOT = _app.draft_root()
    TIMELINE_FILENAME = 'draft_content.json'  # resolved per installation below
    EXCLUSIVE_RENAME = _windows_exclusive_rename
    READ_XATTRS = _windows_read_xattrs
    COPY_XATTRS = _windows_copy_xattrs
    SYNC_LOCK = _windows_sync_lock
else:
    import runtime_io as _io
    from runtime_io import APP_BUNDLE as _APP_BUNDLE

    DRAFT_ROOT = Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
    TIMELINE_FILENAME = 'draft_info.json'
    EXCLUSIVE_RENAME = _macos_exclusive_rename
    READ_XATTRS = _macos_read_xattrs
    COPY_XATTRS = _macos_copy_xattrs
    SYNC_LOCK = _macos_sync_lock


# The raw per-platform IO module, for callers that need the primitives rather
# than the neutral helpers above. Kept public so tests exercise the same
# implementation the runtime dispatches to instead of importing a fixed one.
RUNTIME_IO = _io


def app_bundle():
    """The installed editor directory the runtime is pinned to."""

    if IS_WINDOWS:
        bundle = _app.discover_app_bundle()
        if bundle is None:
            raise ValueError('no JianYing installation found under %s'
                             % _app.applications_directory())
        return bundle
    return _APP_BUNDLE


def timeline_filename():
    """Name of the encrypted per-timeline file for the active installation."""

    if IS_WINDOWS:
        return _app.timeline_filename(_app.inspect(app_bundle()))
    return 'draft_info.json'


def platform_block():
    """``platform``/``last_modified_platform`` block written into a new draft.

    Returns ``None`` on macOS, where the blueprint already carries the reviewed
    identity and rewriting it would change existing output.
    """

    if not IS_WINDOWS:
        return None
    return _app.platform_block(_app.inspect(app_bundle()))


def draft_root_text():
    """Canonical spelling used for comparisons against the home index."""

    return normalized(DRAFT_ROOT)


def draft_path_text(path):
    """Spelling the editor stores in a *folder* path field.

    Observed on Windows 11.5.0: ``draft_fold_path`` and the home index's
    ``root_path``/``draft_fold_path`` use forward slashes, while
    ``draft_root_path`` uses backslashes. macOS uses the native separator
    throughout, so this is a Windows-only transformation.
    """

    text = str(path)
    return text.replace('\\', '/') if IS_WINDOWS else text


def draft_child_text(folder, name):
    """Spelling the editor stores in a *file* path field inside a draft."""

    if IS_WINDOWS:
        return draft_path_text(folder) + '\\' + name
    return str(Path(folder) / name)


def same_draft_path(left, right):
    """Compare two draft paths recorded by the editor in either spelling."""

    if left is None or right is None:
        return False
    return normalized(left) == normalized(right)


def editor_settings_block(now_micros):
    """Body of the ``draft_settings`` INI file."""

    if IS_WINDOWS:
        # Windows omits cloud_last_modify_platform and uses CRLF line endings.
        return ('[General]\r\ndraft_create_time=%d\r\ndraft_last_edit_time=%d\r\n'
                'real_edit_seconds=0\r\nreal_edit_keys=0\r\n' % (now_micros, now_micros))
    return ('[General]\ncloud_last_modify_platform=mac\ndraft_create_time=%d\n'
            'draft_last_edit_time=%d\n' % (now_micros, now_micros))


def helper():
    """The pinned IO namespace consumed by :func:`jy14_headless.build`."""

    if IS_WINDOWS:
        names = ('_decrypt_metadata_in_memory', '_encrypt_metadata_from_memory',
                 '_ensure_editor_closed', '_snapshot_file', '_parse_strict_json',
                 '_revalidate_snapshot', '_acquire_directory_transaction_lock',
                 '_release_directory_transaction_lock')
        return SimpleNamespace(**{n: getattr(_io, n) for n in names},
                               _validate_runtime_environment=validate_runtime)
    import headless_runtime as _macos
    return _macos.helper()


def doctor():
    """Verify the runtime is a reviewed installation before any live write."""

    if IS_WINDOWS:
        installation = _app.discover()
        if (_io.APP_BUNDLE is not None
                and _io.APP_BUNDLE.resolve() != installation.directory.resolve()):
            raise ValueError('JY14_APP_BUNDLE differs from the installation selected by doctor')
        status = _app.review_status(installation)
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise ValueError('ffmpeg and ffprobe are required')
        if not status['reviewed']:
            raise ValueError(
                'unsupported JianYing installation; stop native writes. '
                + str(status.get('reason'))
                + '. Collect a report with tools/runtime_report_windows.py; '
                  'do not replace the reviewed hash by hand.')
        if not DRAFT_ROOT.is_dir():
            raise ValueError('draft store not found: %s' % DRAFT_ROOT)
        bridge = validate_bridge_sources()
        return {'status': 'ok', 'app_version': installation.version,
                'app_build': installation.build,
                'primary_version': installation.version,
                'compatibility_mode': False,
                'install_directory': str(installation.directory),
                'engine_library': _app.ENGINE_LIBRARY,
                'engine_library_sha256': installation.library_sha256,
                'bridge_sources_verified': bridge['bridge_sources'],
                'bridge_manifest_sha256': bridge['bridge_manifest_sha256'],
                'draft_root': str(DRAFT_ROOT),
                'timeline_filename': timeline_filename(),
                'runtime_profile': 'jy14-headless-win-' + installation.version,
                'runtime_hashes_verified': True, 'network_called': False,
                'full_signature_check': 'not applicable on windows',
                'capability_notes': capability_notes()}
    import headless_runtime as _macos
    return _macos.doctor()


def validate_runtime():
    """Full environment check, run immediately before a live mutation."""

    if IS_WINDOWS:
        before = doctor()
        # Windows has no Authenticode gate equivalent to codesign --deep that
        # applies to the engine library, so the reviewed hash is the whole
        # identity check. It is re-read here to catch a mid-run upgrade.
        if doctor() != before:
            raise ValueError('native runtime changed while validating it')
        return dict(before, identity_check='engine-library-sha256')
    import headless_runtime as _macos
    return _macos.validate_runtime()


def manifest_sha():
    """Provenance hash recorded in build records for this platform."""

    if IS_WINDOWS:
        manifest = BRIDGE / WINDOWS_MANIFEST_NAME
        if not manifest.is_file():
            raise ValueError('Windows bridge manifest missing; run '
                             'tools/runtime_report_windows.py')
        return digest(manifest)
    import headless_runtime as _macos
    return _macos.MANIFEST_SHA


#: Name of the Windows bridge source manifest.
WINDOWS_MANIFEST_NAME = 'SOURCE_MANIFEST-windows.json'

#: sha256 of ``bridge/SOURCE_MANIFEST-windows.json``. This is the Windows
#: counterpart of ``headless_runtime.IO_MANIFEST_SHA``: macOS pins a compiled
#: codec helper that must never be redistributed, whereas Windows pins the three
#: bridge modules that drive the installed engine library. Refresh it only after
#: reviewing the bridge diff, with
#: ``tools/runtime_report_windows.py --write-manifest``.
WINDOWS_IO_MANIFEST_SHA = '5c0f6ae0a174262cb09c094a0bb67ec80cee724debeb2bacacaec4591665f4dd'


def validate_bridge_sources():
    """Verify the pinned bridge manifest and every source hash it records.

    A Windows host has no compiled helper to hash, so the equivalent guarantee
    is that the codec binding and the draft IO primitives are the reviewed
    bytes. This re-reads both the manifest pin and each recorded file, and is
    called from :func:`doctor` before any live mutation.
    """

    manifest_path = BRIDGE / WINDOWS_MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError('Windows bridge manifest missing; run '
                         'tools/runtime_report_windows.py --write-manifest')
    actual = digest(manifest_path)
    if actual != WINDOWS_IO_MANIFEST_SHA:
        raise ValueError(
            'Windows bridge manifest changed; reverify the bridge diff before '
            'updating WINDOWS_IO_MANIFEST_SHA in engine/platform_support.py '
            '(expected ' + WINDOWS_IO_MANIFEST_SHA + ', actual ' + actual + ')')
    recorded = json.loads(manifest_path.read_bytes()).get('source_files')
    if not isinstance(recorded, dict) or not recorded:
        raise ValueError('Windows bridge manifest records no source files')
    for name, expected in sorted(recorded.items()):
        path = BRIDGE / name
        if Path(name).name != name or not path.is_file() or path.is_symlink():
            raise ValueError('Bridge source unavailable: ' + name)
        if digest(path) != expected:
            raise ValueError('Bridge source changed: ' + name)
    return {'bridge_sources': len(recorded), 'bridge_manifest_sha256': actual}


#: Provenance hash recorded inside engine/blueprint.json when the draft
#: structure was captured. It describes the artifact, not the host, so it is the
#: same value everywhere and stays valid when the blueprint is reused.
CAPTURE_PROVENANCE = '2fea820b26d503940526c345ce8e9bd87c25f0c1c8b1c4a02aa8edba317e33dd'


def runtime_provenance():
    """Provenance hash of the reviewed runtime this host validated against.

    On macOS this is the same value as the blueprint capture, so existing build
    records are unchanged. On Windows it is the digest of
    ``bridge/SOURCE_MANIFEST-windows.json``.
    """

    return manifest_sha()
