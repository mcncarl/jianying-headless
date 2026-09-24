"""Windows application discovery and runtime identity.

The macOS runtime hard-codes ``/Applications/VideoFusion-macOS.app`` and reads
its identity from ``Contents/Info.plist``. Windows installs are per-user and
version-addressed::

    %LOCALAPPDATA%\\JianyingPro\\Apps\\<version>.<build>\\   the engine
    %LOCALAPPDATA%\\JianyingPro\\User Data\\...              user data

There is no plist and no bundle identifier, so the reviewed identity of an
installation is the pair *(version directory name, sha256 of the engine
library)*. A version that is not in :data:`REVIEWED` is refused rather than
guessed at, mirroring the macOS policy of never inferring compatibility from a
version prefix.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

#: Reviewed Windows runtime profiles. ``library`` is the sha256 of
#: ``videoeditor.dll`` for that exact build; it is what pins the codec ABI.
REVIEWED: Dict[str, Dict[str, object]] = {
    # Observed on Windows 11 (10.0.22631). Capture a new entry with
    # tools/runtime_report_windows.py before touching a different build.
    "11.5.0.14471": {
        "version": "11.5.0",
        "build": "14471",
        "platform": "windows",
        "app_id": "3704",
        "app_source": "lv",
        "library_sha256": "37cadef37daff82e2ecdcd65080b9cbd7f6f9436f5e56c1ffb90c52c408eb7fb",
        "timeline_filename": "draft_content.json",
    },
}

#: The engine library that carries the reviewed codec symbols.
ENGINE_LIBRARY = "videoeditor.dll"

#: Main editor image name, used for the "editor is closed" check.
EDITOR_IMAGE = "JianyingPro.exe"

_VERSION_DIRECTORY = re.compile(r"^(\d+\.\d+\.\d+)\.(\d+)$")

USER_DATA_RELATIVE = Path("User Data")
DRAFT_ROOT_RELATIVE = USER_DATA_RELATIVE / "Projects" / "com.lveditor.draft"


class Installation(NamedTuple):
    directory: Path
    version: str
    build: str
    library_sha256: str


def local_app_data() -> Path:
    override = os.environ.get("JY14_LOCALAPPDATA")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))


def jianying_home() -> Path:
    override = os.environ.get("JY14_HOME")
    if override:
        return Path(override)
    return local_app_data() / "JianyingPro"


def applications_directory() -> Path:
    return jianying_home() / "Apps"


def draft_root() -> Path:
    """Return the draft store, honouring an explicit override."""

    override = os.environ.get("JY14_DRAFT_ROOT")
    if override:
        return Path(override)
    return jianying_home() / DRAFT_ROOT_RELATIVE


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def candidate_directories() -> List[Path]:
    """Every installed engine directory, newest build first."""

    applications = applications_directory()
    if not applications.is_dir():
        return []
    found = []
    for entry in applications.iterdir():
        if not entry.is_dir():
            continue
        match = _VERSION_DIRECTORY.match(entry.name)
        if match and (entry / ENGINE_LIBRARY).is_file():
            found.append((match.group(1), int(match.group(2)), entry))

    def sort_key(item):
        version = tuple(int(part) for part in item[0].split("."))
        return (version, item[1])

    return [entry for _version, _build, entry in sorted(found, key=sort_key,
                                                        reverse=True)]


def inspect(directory: Path) -> Installation:
    """Describe an installation directory without trusting its name alone."""

    directory = Path(directory)
    library = directory / ENGINE_LIBRARY
    if not library.is_file():
        raise ValueError("not an engine directory: %s" % directory)
    match = _VERSION_DIRECTORY.match(directory.name)
    if match is None:
        raise ValueError("unrecognised installation directory name: %s"
                         % directory.name)
    return Installation(directory=directory, version=match.group(1),
                        build=match.group(2), library_sha256=digest(library))


def discover_app_bundle() -> Optional[Path]:
    """Newest installation whose engine directory carries the codec."""

    for candidate in candidate_directories():
        return candidate
    return None


def discover() -> Installation:
    bundle = discover_app_bundle()
    if bundle is None:
        raise ValueError(
            "no JianYing installation found under %s" % applications_directory()
        )
    return inspect(bundle)


def review_status(installation: Installation) -> Dict[str, object]:
    """Report whether an installation matches a reviewed profile exactly."""

    key = "%s.%s" % (installation.version, installation.build)
    profile = REVIEWED.get(key)
    result: Dict[str, object] = {
        "key": key,
        "version": installation.version,
        "build": installation.build,
        "directory": str(installation.directory),
        "library_sha256": installation.library_sha256,
        "reviewed": profile is not None,
    }
    if profile is None:
        result["reason"] = "version/build is not in the reviewed Windows profile table"
        return result
    # The descriptive fields are usable even before the library hash is pinned,
    # so surface them first and let the hash decide only the ``reviewed`` flag.
    result["platform"] = profile.get("platform", "windows")
    result["app_id"] = profile.get("app_id")
    result["app_source"] = profile.get("app_source", "lv")
    result["timeline_filename"] = profile.get("timeline_filename")
    expected = profile.get("library_sha256")
    if expected is None:
        result["reviewed"] = False
        result["reason"] = ("profile %s carries no library hash yet; run "
                            "tools/runtime_report_windows.py and record it" % key)
        return result
    if expected != installation.library_sha256:
        result["reviewed"] = False
        result["reason"] = ("engine library differs from the reviewed profile; "
                            "expected %s" % expected)
        return result
    return result


def platform_block(installation: Installation) -> Dict[str, str]:
    """The ``platform``/``last_modified_platform`` block a draft must carry."""

    status = review_status(installation)
    return {
        "os": str(status.get("platform", "windows")),
        "os_version": os_version(),
        "app_id": str(status.get("app_id", "")),
        "app_version": installation.version,
        "app_source": str(status.get("app_source", "lv")),
    }


def timeline_filename(installation: Installation) -> str:
    """Name of the encrypted per-timeline file for this installation.

    Windows 11.5.0 writes ``draft_content.json`` where the macOS build of the
    same version writes ``draft_info.json``; the payload is identical.
    """

    status = review_status(installation)
    name = status.get("timeline_filename")
    if not name:
        raise ValueError("no reviewed timeline filename for %s.%s"
                         % (installation.version, installation.build))
    return str(name)


def os_version() -> str:
    """Windows release as ``major.minor.build``, matching what the editor writes."""

    try:
        import platform
        release = platform.release()
        version = platform.version()
    except Exception:  # pragma: no cover - platform is always importable
        return ""
    match = re.search(r"(\d+\.\d+\.\d+)", version)
    if match:
        return match.group(1)
    return release
