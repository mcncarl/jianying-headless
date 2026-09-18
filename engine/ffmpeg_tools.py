"""Small, platform-neutral FFmpeg process and executable helpers."""
from pathlib import Path
import os
import shutil
import subprocess


def resolve_tool(name, explicit=None, env_name=None):
    """Resolve an executable without assuming it is installed in PATH."""
    value = explicit or (os.environ.get(env_name) if env_name else None)
    candidates = [value] if value else []
    if value and Path(value).name.lower() != name.lower():
        candidates.append(str(Path(value) / name))
    found = next((Path(item).expanduser().resolve() for item in candidates
                  if item and Path(item).is_file()), None)
    if found is None:
        which = shutil.which(name)
        found = Path(which).resolve() if which else None
    if found is None or not found.is_file():
        raise FileNotFoundError(
            f'{name}.exe is required. Set --{name} or {env_name or "add FFmpeg to PATH"}.')
    return found


def version(executable):
    result = subprocess.run([str(executable), '-version'], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=30)
    if result.returncode:
        raise RuntimeError(f'Unable to read {executable} version: {result.stderr.strip()}')
    return result.stdout.splitlines()[0].strip() if result.stdout else ''


def run(executable, args, stdout_path, stderr_path, timeout):
    command = [str(executable), *map(str, args)]
    with Path(stdout_path).open('wb') as stdout, Path(stderr_path).open('wb') as stderr:
        try:
            result = subprocess.run(command, stdout=stdout, stderr=stderr,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError('FFmpeg process timed out; partial output and logs retained') from error
    return command, result.returncode
