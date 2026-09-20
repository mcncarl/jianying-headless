"""Explicit runtime profiles for the headless builder, independent of legacy apply.

Only the version-neutral, hash-pinned IO/codec primitives are included. The
legacy registered-sound CLI and account-specific constants are not distributed.
Live creation is guarded by this module's own exact application profile.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from types import SimpleNamespace
from runtime_profiles import CODEC_PROFILES, PRIMARY_VERSION, resolve_identity

APP = Path('/Applications/VideoFusion-macOS.app')
DRAFT_ROOT = Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / 'bridge'
# Historical blueprint/codec provenance, not a statement of the current app version.
MANIFEST_SHA = '2fea820b26d503940526c345ce8e9bd87c25f0c1c8b1c4a02aa8edba317e33dd'
IO_MANIFEST_SHA = '473b05370d77f0835e11f0fde4f3cec2eec0b00777aa0018441bc9e7b53bb300'
PINS = {
    'runtime_io.py': '487cf8c65bddad2ecdb351b16996d923bd6bcc6861dba12e801f679821b4ef70',
}
BUNDLE_ID = 'com.lemon.lvpro'
TEAM = 'X2JNK7LY8J'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def fresh_directory(path):
    path = Path(path).resolve()
    if path == DRAFT_ROOT or DRAFT_ROOT in path.parents:
        raise ValueError('Build/audit directories must be outside the live draft tree')
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    return path


def doctor():
    if digest(BACKEND / 'SOURCE_MANIFEST.json') != IO_MANIFEST_SHA:
        raise ValueError('Packaged IO/codec source manifest changed')
    for name, expected in PINS.items():
        path = BACKEND / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('IO/codec component unavailable: ' + name + '; build with tools/build_native_codec.py')
        if digest(path) != expected:
            raise ValueError('IO/codec component changed: ' + name)
    info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    library = APP / 'Contents/Frameworks/libvideoeditor.dylib'
    fingerprint = digest(library)
    identity = resolve_identity(info, fingerprint)
    profile_id = identity['profile_id']
    codec_profile = CODEC_PROFILES[profile_id]
    codec_path = BACKEND / codec_profile['filename']
    if not codec_path.is_file() or codec_path.is_symlink():
        raise ValueError('IO/codec component unavailable: ' + codec_profile['filename']
                         + '; build with tools/build_native_codec.py')
    if digest(codec_path) != codec_profile['sha256']:
        raise ValueError('IO/codec component changed: ' + codec_profile['filename'])
    if not all(shutil.which(name) for name in ('ffmpeg', 'ffprobe')):
        raise ValueError('ffmpeg and ffprobe are required')
    return {'status': 'ok', 'app_version': identity['app_version'],
            'app_build': identity['app_build'], 'profile_id': profile_id,
            'primary_version': PRIMARY_VERSION, 'compatibility_mode': profile_id != PRIMARY_VERSION,
            'bundle_id': BUNDLE_ID, 'libvideoeditor_sha256': fingerprint,
            'runtime_profile': identity['runtime_profile'],
            'codec_filename': codec_profile['filename'],
            'codec_sha256': codec_profile['sha256'],
            'runtime_hashes_verified': True, 'network_called': False,
            'full_signature_check': 'required before live creation'}


def validate_runtime():
    before = doctor()
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'}
    result = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP)],
                            capture_output=True, timeout=180, env=env)
    details = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', str(APP)],
                             capture_output=True, text=True, timeout=30, env=env)
    if result.returncode or details.returncode or ('TeamIdentifier=' + TEAM) not in details.stderr.splitlines():
        raise ValueError('Native application signature or signing identity verification failed')
    if doctor() != before:
        raise ValueError('Native runtime changed while checking its signature')
    return dict(before, team_identifier=TEAM, full_signature_check='passed')


def helper():
    runtime = doctor()
    name = '_jy14_headless_pinned_io_' + hashlib.sha256(str(BACKEND).encode()).hexdigest()[:16]
    h = sys.modules.get(name)
    if h is None:
        spec = importlib.util.spec_from_file_location(name, BACKEND / 'runtime_io.py')
        h = importlib.util.module_from_spec(spec)
        sys.modules[name] = h
        spec.loader.exec_module(h)
        codec_profile = CODEC_PROFILES[runtime['profile_id']]
        h.CODEC_PATH = BACKEND / codec_profile['filename']
        h.REQUIRED_CODEC_SHA256 = codec_profile['sha256']
    names = ('_decrypt_metadata_in_memory', '_encrypt_metadata_from_memory',
             '_ensure_editor_closed', '_snapshot_file', '_parse_strict_json',
             '_revalidate_snapshot', '_acquire_directory_transaction_lock',
             '_release_directory_transaction_lock')
    return SimpleNamespace(**{n: getattr(h, n) for n in names}, _validate_runtime_environment=validate_runtime)


def validate_compiled(value):
    # This is the speech-plan validator, not the legacy native runtime wrapper.
    scripts = PROJECT_ROOT / 'skills/yichen-jianying-edit/scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from edit_plan import validate_compiled as validate
    return validate(value)
