#!/usr/bin/env python3
"""Read-only, bounded runtime report: no account data, media paths or program uploads."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
from runtime_profiles import identity_for_info, resolve_identity


def report(app):
    result = {'schema': 'jianying-headless-runtime-report/v1', 'system': platform.system(),
              'macos': platform.mac_ver()[0], 'architecture': platform.machine(),
              'python': platform.python_version(), 'read_only': True, 'network_called': False}
    try:
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        if not isinstance(info, dict) or any(
                info.get(key) is not None and not isinstance(info[key], str)
                for key in ('CFBundleShortVersionString', 'CFBundleVersion', 'CFBundleIdentifier')):
            raise ValueError('Malformed application identity')
        result.update(app_version=info.get('CFBundleShortVersionString'),
                      app_build=info.get('CFBundleVersion'), bundle_id=info.get('CFBundleIdentifier'))
        library = app / 'Contents/Frameworks/libvideoeditor.dylib'
        sha = hashlib.sha256()
        with library.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(block)
        result['library_sha256'] = sha.hexdigest()
        try:
            expected = identity_for_info(info)
            result['profile_id'] = expected['profile_id']
            result['expected_library_sha256'] = expected['library_sha256']
        except ValueError:
            result['profile_id'] = None
            result['expected_library_sha256'] = None
    except (OSError, ValueError) as error:
        result.update(status='unavailable', reason=type(error).__name__,
                      next_step='Check the official Jianying installation; no personal paths are included.')
        return result
    try:
        resolve_identity(info, result['library_sha256'])
        result['identity_matched'] = True
    except ValueError:
        result['identity_matched'] = False
    try:
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'}
        verified = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                                  env=env, capture_output=True, timeout=180)
        details = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', str(app)],
                                 env=env, capture_output=True, text=True, timeout=30)
        team = [line.split('=', 1)[1] for line in details.stderr.splitlines()
                if line.startswith('TeamIdentifier=')]
        result['team_identifier'] = team[0] if len(team) == 1 else None
        result['official_signature_verified'] = (verified.returncode == details.returncode == 0
                                                 and team == ['X2JNK7LY8J'])
    except (OSError, subprocess.SubprocessError):
        result['official_signature_verified'] = False
    result['status'] = ('identity-verified' if result['identity_matched']
                        and result['official_signature_verified'] else 'review-required')
    result['next_step'] = ('Run doctor and the documented first-draft workflow.'
                           if result['status'] == 'identity-verified' else
                           'Share this report and the official download source, not application binaries. '
                           'Native writes remain blocked until this exact build is reviewed.')
    result['codec_or_native_workflow_verified'] = False
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=Path('/Applications/VideoFusion-macOS.app'))
    args = parser.parse_args()
    value = report(args.app)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    raise SystemExit(0 if value['status'] == 'identity-verified' else 1)
