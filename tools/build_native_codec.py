#!/usr/bin/env python3
"""Rebuild the source-pinned local codec; never download or patch Jianying."""
from __future__ import annotations

import hashlib
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
from runtime_profiles import PROFILES, validate_identity
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


def developer_dir():
    tools = Path('/Library/Developer/CommandLineTools')
    if (tools / 'usr/bin/clang').is_file():
        return tools
    selected = subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True, text=True)
    require(selected.returncode == 0, 'No usable toolchain: install the Xcode Command Line Tools')
    return Path(selected.stdout.strip())


def build_identity(path, env):
    loaded = subprocess.run(['/usr/bin/xcrun', 'otool', '-l', str(path)],
                            env=env, capture_output=True, text=True)
    fields = {}
    if loaded.returncode == 0:
        section = loaded.stdout.partition('LC_BUILD_VERSION')[2].splitlines()[:8]
        for line in section:
            parts = line.split()
            if len(parts) == 2 and parts[0] in {'minos', 'sdk', 'version'}:
                fields.setdefault('linker' if parts[0] == 'version' else parts[0], parts[1])
    return fields


def main():
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'This native bridge profile requires Apple Silicon macOS')
    require(sys.version_info >= (3, 9), 'Python 3.9 or later is required')
    manifest = json.loads((BRIDGE / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    require(manifest['schema'] == 'jianying-headless-bridge-source/v1', 'Unexpected bridge source manifest')
    for name, expected in manifest['source_files'].items():
        require(Path(name).name == name, 'Bridge source names must be simple file names')
        require(digest(BRIDGE / name) == expected, 'Bridge source changed: ' + name)
    require(manifest['expected_codec_sha256'] == EXPECTED_CODEC_SHA, 'Codec fingerprint changed')

    info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    library = APP / 'Contents/Frameworks/libvideoeditor.dylib'
    version = validate_identity(info, digest(library))
    toolchain = developer_dir()
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C',
           'HOME': os.environ.get('HOME', '/'), 'DEVELOPER_DIR': str(toolchain)}
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP)], env=env)
    signature = run(['/usr/bin/codesign', '-dv', '--verbose=4', str(APP)], env=env)
    require('TeamIdentifier=X2JNK7LY8J' in signature.stderr.splitlines(), 'Unexpected Jianying signing identity')

    destination = BRIDGE / 'jy14_codec_hardened_11_4'
    if destination.exists() or destination.is_symlink():
        require(digest(destination) == EXPECTED_CODEC_SHA and os.access(destination, os.X_OK),
                'An unexpected codec file already exists; it was not overwritten')
        print(json.dumps({'status': 'already-valid', 'codec_sha256': EXPECTED_CODEC_SHA,
                          'app_version': version, 'network_called': False}))
        return

    reproduction = manifest['reproduction_environment']
    sdk_version = reproduction['macos_sdk']
    deployment_target = reproduction['binary_minimum_macos']
    located = subprocess.run(['/usr/bin/xcrun', '--show-sdk-path', '--sdk', 'macosx' + sdk_version],
                             env=env, capture_output=True, text=True)
    require(located.returncode == 0 and located.stdout.strip(),
            'macOS SDK ' + sdk_version + ' is required for the reviewed build but was not found in '
            + str(toolchain) + '; install the matching Command Line Tools or Xcode.\n' + located.stderr[-2000:])
    sdk_path = Path(located.stdout.strip())
    require(sdk_path.is_dir(), 'Resolved SDK path is not a directory: ' + str(sdk_path))

    work = ROOT / 'work'
    work.mkdir(mode=0o700, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='codec-build-', dir=work))
    compiler_temp = job / 'compiler-tmp'
    compiler_temp.mkdir(mode=0o700)
    env.update(TMPDIR=str(compiler_temp) + '/', CLANG_MODULE_CACHE_PATH=str(compiler_temp / 'modules'))
    built = job / destination.name
    frameworks = APP / 'Contents/Frameworks'
    command = ['/usr/bin/xcrun', 'clang++', '-std=c++17', '-arch', 'arm64', '-O2',
               '-isysroot', str(sdk_path), '-mmacosx-version-min=' + deployment_target,
               str(BRIDGE / 'jy14_codec.cpp'), '-L' + str(frameworks), '-lvideoeditor',
               '-Wl,-rpath,' + str(frameworks), '-o', str(built)]
    compiler = run(['/usr/bin/xcrun', 'clang++', '--version'], env=env).stdout
    result = run(command, env=env)
    actual = digest(built)
    identity = build_identity(built, env)
    report = {'schema': 'jianying-headless-codec-build/v1', 'status': 'built', 'app_version': version,
              'compiler': compiler, 'command': command, 'codec_sha256': actual,
              'expected_codec_sha256': EXPECTED_CODEC_SHA, 'stderr': result.stderr,
              'developer_dir': str(toolchain), 'sdk_path': str(sdk_path),
              'deployment_target': deployment_target, 'binary_identity': identity,
              'reviewed_environment': reproduction,
              'network_called': False, 'official_library_copied': False, 'app_modified': False}
    with (job / 'build-report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    require(actual == EXPECTED_CODEC_SHA,
            'Compiler output differs from the reviewed codec. No runtime pin was changed.\n'
            '  built    ' + actual + '\n'
            '  expected ' + EXPECTED_CODEC_SHA + '\n'
            '  compiler ' + compiler.splitlines()[0] + '\n'
            '  sdk      ' + str(sdk_path) + '\n'
            '  binary   minos=' + identity.get('minos', '?') + ' sdk=' + identity.get('sdk', '?')
            + ' linker=' + identity.get('linker', '?') + '\n'
            '  reviewed ' + reproduction['compiler'] + ', SDK ' + sdk_version + '\n'
            'The reviewed codec was compiled by the toolchain above; a different compiler or SDK '
            'generation produces different machine code. Report the values above so the build can be '
            'reviewed for this toolchain. Audit directory: ' + str(job))
    require(digest(library) == PROFILES[version], 'The native library changed while compiling')
    for name, expected in manifest['source_files'].items():
        require(digest(BRIDGE / name) == expected, 'Bridge source changed while compiling: ' + name)
    run(['/usr/bin/codesign', '--verify', '--strict', str(built)], env=env)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o700)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(built.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    require(digest(destination) == EXPECTED_CODEC_SHA, 'Installed local codec fingerprint differs')
    print(json.dumps({'status': 'built-and-verified', 'codec_sha256': actual, 'app_version': version,
                      'audit_directory': str(job), 'network_called': False, 'app_modified': False}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
