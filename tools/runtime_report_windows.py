#!/usr/bin/env python3
"""Report the Windows runtime identity, and optionally pin it.

    python tools/runtime_report_windows.py
    python tools/runtime_report_windows.py --verify-codec
    python tools/runtime_report_windows.py --write-manifest

The default report only inspects files and installation identity; it never
loads the native engine. An explicit ``--verify-codec`` may load the engine
only after its version and library hash match a reviewed profile.
``--write-manifest`` refreshes
:file:`bridge/SOURCE_MANIFEST-windows.json`, which is the provenance hash that
build records carry.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for relative in ('engine', 'bridge'):
    candidate = str(PROJECT_ROOT / relative)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import windows_codec  # noqa: E402
import windows_platform  # noqa: E402

MANIFEST_PATH = PROJECT_ROOT / 'bridge' / 'SOURCE_MANIFEST-windows.json'

REQUIRED_SYMBOLS = {
    'enable': windows_codec.SYMBOL_ENABLE,
    'isEnable': windows_codec.SYMBOL_IS_ENABLE,
    'encrypt': windows_codec.SYMBOL_ENCRYPT,
    'decrypt': windows_codec.SYMBOL_DECRYPT,
}


def requirement(name: str, ok: bool, detail: str = '') -> dict:
    return {'check': name, 'ok': bool(ok), 'detail': detail}


def collect(verify_codec: bool) -> dict:
    checks = []
    report: dict = {'schema': 'jy14-headless-runtime-report-windows/v1'}

    candidates = windows_platform.candidate_directories()
    checks.append(requirement('installation found', bool(candidates),
                              ', '.join(c.name for c in candidates)))
    if not candidates:
        report['checks'] = checks
        return report

    installation = windows_platform.inspect(candidates[0])
    report['installation'] = {
        'directory': str(installation.directory),
        'version': installation.version,
        'build': installation.build,
        'engine_library': windows_platform.ENGINE_LIBRARY,
        'engine_library_sha256': installation.library_sha256,
    }
    report['review'] = windows_platform.review_status(installation)
    checks.append(requirement('installation reviewed', report['review']['reviewed'],
                              str(report['review'].get('reason', 'matches reviewed profile'))))

    report['draft_root'] = str(windows_platform.draft_root())
    checks.append(requirement('draft store exists', windows_platform.draft_root().is_dir(),
                              report['draft_root']))

    missing = [name for name in ('ffmpeg', 'ffprobe') if not shutil.which(name)]
    checks.append(requirement('ffmpeg and ffprobe available', not missing,
                              'missing: ' + ', '.join(missing) if missing else 'both on PATH'))

    if verify_codec:
        if not report['review']['reviewed']:
            checks.append(requirement('codec verification', False,
                                      'skipped: installation is not reviewed'))
        else:
            try:
                codec = windows_codec.codec_for(installation.directory)
                checks.append(requirement('codec symbols resolve', True,
                                          'enabled=%s' % bool(codec)))
                checks.append(_codec_round_trip(codec, windows_platform.draft_root()))
            except windows_codec.CodecUnavailable as exc:
                checks.append(requirement('codec symbols resolve', False, str(exc)))

    report['checks'] = checks
    report['capability_notes'] = _capability_notes()
    return report


def _codec_round_trip(codec, draft_root: Path) -> dict:
    """Decrypt and re-encrypt one existing draft, without writing anything."""

    for draft in sorted(draft_root.iterdir()):
        payload = draft / 'draft_content.json'
        if not draft.is_dir() or not payload.is_file():
            continue
        try:
            plaintext = codec.decrypt(payload.read_bytes())
            json.loads(plaintext.decode('utf-8'))
            again = codec.decrypt(codec.encrypt(plaintext))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return requirement('codec round-trip on a real draft', False, str(exc))
        return requirement('codec round-trip on a real draft', again == plaintext,
                           '%s (%d plaintext bytes)' % (draft.name, len(plaintext)))
    return requirement('codec round-trip on a real draft', False,
                       'no existing draft available to test against')


def _capability_notes() -> list:
    return [
        'the codec is driven in-process through ctypes against the installed '
        'engine library; nothing from the editor is redistributed',
        'bridge module hashes are pinned by SOURCE_MANIFEST-windows.json, which '
        'platform_support.validate_bridge_sources re-reads on every doctor call; '
        'engine modules are pinned separately by the Skill entry point',
        'the trust anchor for the installation itself is the engine library '
        'sha256, because no Authenticode check is performed',
        'reparse points are refused on every path component, but Windows has no '
        'dir_fd and therefore no atomic check-then-open',
    ]


def write_manifest(report: dict) -> Path:
    installation = report.get('installation')
    if installation is None:
        raise SystemExit('cannot write a manifest without an installation')
    if not report.get('review', {}).get('reviewed'):
        raise SystemExit('cannot pin an unreviewed installation')
    # Only bridge/ modules are recorded here. platform_support.py lives in
    # engine/ and is pinned by the Skill entry point instead, which keeps the
    # manifest from having to hash a file that holds the manifest's own pin.
    sources = {}
    for name in ('windows_codec.py', 'windows_platform.py', 'runtime_io_windows.py'):
        location = PROJECT_ROOT / 'bridge' / name
        if not location.is_file():
            raise SystemExit('bridge module is missing: ' + name)
        sources[name] = windows_platform.digest(location)
    manifest = {
        'schema': 'jianying-headless-bridge-source-windows/v1',
        'platform': 'windows',
        'trust_anchor': 'engine-library-sha256',
        'installed_engine_library': windows_platform.ENGINE_LIBRARY,
        'reviewed_installation': {
            '%s.%s' % (installation['version'], installation['build']):
                installation['engine_library_sha256'],
        },
        'reviewed_timeline_filename':
            windows_platform.timeline_filename(windows_platform.inspect(
                Path(installation['directory']))),
        'codec_symbols': dict(REQUIRED_SYMBOLS),
        'official_library_distributed': False,
        'source_files': sources,
    }
    # Written as bytes, never through text mode: Windows would translate LF to
    # CRLF, and this file's own sha256 is pinned in engine/platform_support.py.
    # A checkout would then normalize it to LF and the pin would no longer match.
    MANIFEST_PATH.write_bytes(
        (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
    return MANIFEST_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-codec', action='store_true',
                        help='decrypt and re-encrypt one existing draft in memory')
    parser.add_argument('--write-manifest', action='store_true',
                        help='refresh bridge/SOURCE_MANIFEST-windows.json')
    parser.add_argument('--json', action='store_true', help='emit raw JSON')
    args = parser.parse_args()

    report = collect(args.verify_codec)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for check in report.get('checks', []):
            print('%-32s %s  %s' % (check['check'], 'ok' if check['ok'] else 'FAIL',
                                    check['detail']))
        if report.get('installation'):
            print('\ninstallation : %s' % report['installation']['directory'])
            print('version      : %s.%s' % (report['installation']['version'],
                                            report['installation']['build']))
            print('engine sha256: %s' % report['installation']['engine_library_sha256'])
            print('draft root   : %s' % report['draft_root'])

    if args.write_manifest:
        print('\nmanifest     : %s' % write_manifest(report))

    failed = [c for c in report.get('checks', []) if not c['ok']]
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
