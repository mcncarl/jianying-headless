#!/usr/bin/env python3
"""Rebuild the source-pinned local codec; never download or patch Jianying."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
from runtime_profiles import CODEC_PROFILES, LEGACY_CODEC, resolve_identity
from build_toolchain import select_toolchain, compile_command
BRIDGE = ROOT / 'bridge'
APP = Path('/Applications/VideoFusion-macOS.app')
EXPECTED_CODEC_SHA = 'b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Expected a regular, non-symlink file: ' + str(path))
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def run(command, *, env=None, timeout=180):
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'Command failed: ' + command[0] + '\n' + result.stderr[-4000:])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--developer-dir', help='Use this exact Xcode/CLT developer directory')
    parser.add_argument('--check-toolchain', action='store_true', help='Read-only exact toolchain check')
    parser.add_argument('--rebuild-check', action='store_true',
                        help='Rebuild into retained work/ even when the installed codec is already valid')
    args = parser.parse_args(argv)
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'This native bridge profile requires Apple Silicon macOS')
    require(sys.version_info >= (3, 9), 'Python 3.9 or later is required')
    manifest = json.loads((BRIDGE / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    require(manifest['schema'] == 'jianying-headless-bridge-source/v1', 'Unexpected bridge source manifest')
    for name, expected in manifest['source_files'].items():
        require(Path(name).name == name, 'Bridge source names must be simple file names')
        require(digest(BRIDGE / name) == expected, 'Bridge source changed: ' + name)
    require(manifest['expected_codec_sha256'] == EXPECTED_CODEC_SHA == LEGACY_CODEC['sha256'],
            'Legacy codec fingerprint changed')

    info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    library = APP / 'Contents/Frameworks/libvideoeditor.dylib'
    identity = resolve_identity(info, digest(library))
    profile = CODEC_PROFILES[identity['profile_id']]
    expected_codec_sha = profile['sha256']

    if args.check_toolchain:
        _, toolchain = select_toolchain(profile['toolchain'], args.developer_dir)
        print(json.dumps(dict(toolchain, status='toolchain-matched', compiled=False,
                              runtime_profile=identity['runtime_profile'],
                              runtime_compatibility_verified=False), ensure_ascii=False))
        return

    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'}
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP)], env=env)
    signature = run(['/usr/bin/codesign', '-dv', '--verbose=4', str(APP)], env=env)
    require('TeamIdentifier=X2JNK7LY8J' in signature.stderr.splitlines(), 'Unexpected Jianying signing identity')

    destination = BRIDGE / profile['filename']
    already_installed = destination.exists() or destination.is_symlink()
    if already_installed:
        require(digest(destination) == expected_codec_sha and os.access(destination, os.X_OK),
                'An unexpected codec file already exists; it was not overwritten')
        if not args.rebuild_check:
            print(json.dumps({'status': 'already-valid', 'codec_sha256': expected_codec_sha,
                              'app_version': identity['app_version'], 'app_build': identity['app_build'],
                              'network_called': False}))
            return

    env, toolchain = select_toolchain(profile['toolchain'], args.developer_dir)

    work = ROOT / 'work'
    work.mkdir(mode=0o700, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='codec-build-', dir=work))
    compiler_temp = job / 'compiler-tmp'
    compiler_temp.mkdir(mode=0o700)
    env.update(TMPDIR=str(compiler_temp) + '/', CLANG_MODULE_CACHE_PATH=str(compiler_temp / 'modules'))
    built = job / destination.name
    frameworks = APP / 'Contents/Frameworks'
    command = compile_command(BRIDGE / 'jy14_codec.cpp', frameworks, built, toolchain)
    compiler = run(['/usr/bin/xcrun', 'clang++', '--version'], env=env).stdout
    result = run(command, env=env)
    actual = digest(built)
    report = {'schema': 'jianying-headless-codec-build/v1', 'status': 'built',
              'app_version': identity['app_version'], 'app_build': identity['app_build'],
              'runtime_profile': identity['runtime_profile'],
              'compiler': compiler, 'toolchain': toolchain, 'command': command, 'codec_sha256': actual,
              'expected_codec_sha256': expected_codec_sha, 'stderr': result.stderr,
              'network_called': False, 'official_library_copied': False, 'app_modified': False}
    with (job / 'build-report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    require(actual == expected_codec_sha,
            'Compiler output differs from the reviewed codec. No runtime pin was changed; inspect ' + str(job))
    require(digest(library) == identity['library_sha256'], 'The native library changed while compiling')
    for name, expected in manifest['source_files'].items():
        require(digest(BRIDGE / name) == expected, 'Bridge source changed while compiling: ' + name)
    run(['/usr/bin/codesign', '--verify', '--strict', str(built)], env=env)
    if args.rebuild_check:
        print(json.dumps({'status': 'reproduced-and-verified', 'codec_sha256': actual,
                          'audit_directory': str(job), 'installed': False, 'network_called': False}))
        return
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o700)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(built.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    require(digest(destination) == expected_codec_sha, 'Installed local codec fingerprint differs')
    print(json.dumps({'status': 'built-and-verified', 'codec_sha256': actual,
                      'app_version': identity['app_version'], 'app_build': identity['app_build'],
                      'audit_directory': str(job), 'network_called': False, 'app_modified': False}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
