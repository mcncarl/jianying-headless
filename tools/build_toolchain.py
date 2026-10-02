"""Select an exact reviewed Apple toolchain without changing xcode-select."""
import json
import os
from pathlib import Path
import subprocess

# Independently read from the reviewed binary's LC_BUILD_VERSION and local ld.
# Compiler and SDK identities remain in the source manifest.
DEFAULT_REVIEWED_LINKER = '1267'


def run(command, env):
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('Toolchain command failed: ' + command[0] + '\n' + result.stderr[-2000:])
    return result.stdout.strip()


def clean_environment(developer_dir):
    return {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C',
            'DEVELOPER_DIR': str(developer_dir)}


def candidates(explicit=None):
    # An explicit selection must never silently fall back to something else.
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            raise ValueError('--developer-dir must be an absolute path')
        return [path]
    paths = []
    if os.environ.get('DEVELOPER_DIR'):
        paths.append(Path(os.environ['DEVELOPER_DIR']))
    selected = subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True,
                              text=True, timeout=30, env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
    if selected.returncode == 0 and selected.stdout.strip():
        paths.append(Path(selected.stdout.strip()))
    paths += [p / 'Contents/Developer' for p in sorted(Path('/Applications').glob('Xcode*.app'))]
    paths.append(Path('/Library/Developer/CommandLineTools'))
    return list(dict.fromkeys(p.resolve() for p in paths if p.is_absolute()))


def inspect(developer_dir, reproduction):
    if not developer_dir.is_dir():
        raise ValueError('Developer directory does not exist')
    env = clean_environment(developer_dir)
    compiler = run(['/usr/bin/xcrun', 'clang++', '--version'], env).splitlines()[0]
    sdk_name = 'macosx' + reproduction['macos_sdk']
    sdk_path = run(['/usr/bin/xcrun', '--sdk', sdk_name, '--show-sdk-path'], env)
    sdk_version = run(['/usr/bin/xcrun', '--sdk', sdk_name, '--show-sdk-version'], env)
    linker = json.loads(run(['/usr/bin/xcrun', 'ld', '-version_details'], env))['version']
    identity = {'developer_dir': str(developer_dir), 'compiler': compiler,
                'sdk_path': sdk_path, 'sdk_version': sdk_version, 'linker': linker,
                'deployment_target': reproduction['binary_minimum_macos']}
    normalized_compiler = compiler.replace('Apple clang version ', 'Apple clang ', 1)
    expected_linker = reproduction.get('linker', DEFAULT_REVIEWED_LINKER)
    if (normalized_compiler != reproduction['compiler'] or sdk_version != reproduction['macos_sdk']
            or linker != expected_linker or not Path(sdk_path).is_dir()):
        raise ValueError('Not the reviewed toolchain: ' + json.dumps(identity, ensure_ascii=False))
    return env, identity


def select_toolchain(reproduction, explicit=None):
    failures = []
    for path in candidates(explicit):
        try:
            return inspect(path, reproduction)
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
            failures.append(str(path) + ': ' + str(error))
    expected_linker = reproduction.get('linker', DEFAULT_REVIEWED_LINKER)
    raise ValueError('No exact reviewed toolchain found. Required: ' + reproduction['compiler']
                     + ', SDK ' + reproduction['macos_sdk'] + ', linker ' + expected_linker
                     + '. Install a matching official Xcode/CLT, or select it with --developer-dir. '
                     'No system selection or runtime pin was changed.\n' + '\n'.join(failures))


def compile_command(source, frameworks, output, identity):
    return ['/usr/bin/xcrun', 'clang++', '-std=c++17', '-arch', 'arm64', '-O2',
            '-isysroot', identity['sdk_path'],
            '-mmacosx-version-min=' + identity['deployment_target'],
            str(source), '-L' + str(frameworks), '-lvideoeditor',
            '-Wl,-rpath,' + str(frameworks), '-o', str(output)]
