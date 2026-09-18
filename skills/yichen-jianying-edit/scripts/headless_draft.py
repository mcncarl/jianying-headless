#!/usr/bin/env python3
"""Pinned Skill entrypoint for a separately checked-out Jianying Headless project."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

def project_root():
    configured = os.environ.get('JIANYING_HEADLESS_ROOT')
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise SystemExit('JIANYING_HEADLESS_ROOT must be an absolute checkout path')
        candidates = [path.resolve()]
    else:
        candidates = list(Path(__file__).resolve().parents)
    for path in candidates:
        marker = path / 'project.json'
        if marker.is_file() and not marker.is_symlink():
            try:
                identity = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if identity.get('id') == 'jianying-headless' and identity.get('schema') == 'jianying-headless-project/v1':
                return path
    raise SystemExit('Jianying Headless checkout unavailable. Clone the project with authorized GitHub access, '
                     'then set JIANYING_HEADLESS_ROOT to its absolute path. The Skill alone does not include the engine.')


PROJECT_ROOT = project_root()
BACKEND = PROJECT_ROOT / 'engine'
PINS = {
    'jy14_headless.py': 'ae96f9349ab207928d5cd7534bf1c92c05bb2d8a627b948bac1e40f88f6cd366',
    'native_motion.py': '23575ddcd11bf0be10c9f371749f121d3de593255f0b6cabcda1f79d9f714780',
    'native_effects.py': '93c13600e98b5f820bf3324d6126e30c334ea7073407d02c2c98bc90b70a189d',
    'native_resources.py': '6e78d6d205850dc5e929b77310167fb722040ed23d80764edd02fe4dc3286584',
    'native_visual_effects.py': '6a4ced9547b82a369a58bb8a9540ce6a6a4efe0dfa94434c0b3c4567808e808b',
    'native-resource-catalog.json': '7043ebf1a3de79b6f9857e8387d937c4f9c7be35b25468b21b2ebe75664a2e54',
    'native_compound.py': '6eb64118e1e3bdff1473b93128b8225f61030e5d6af71ea0286b83c3a330f155',
    'compound-blueprint.json': 'b897ae725cf8ac1104d35bba90a14313eaf5afb9bed4cc5ff04a765031767f02',
    'native_edit.py': '4e3a59be7592e977609632d85530737c0f9b5663b0cd946d33ead45b1fea1675',
    'native_export.py': 'af3c142a571cfcd6b4edd08384c020b38b05f53967ac13e8329705a5b5bccdae',
    'native_export.cpp': 'acfb55a9204cbf4048ef2bf14ecdbd2c42914a0e77bff50c8b421b3347f3ee11',
    'headless_runtime.py': '05bd16ab1fdb40f09e236f262835863577c4e891db01af655a251703e129caf4',
    'blueprint.json': 'a8e75a70f576c0dfd5854d4e2e9aac0190d17b3fa13813039ef14c15cdccec41',
}

for name, expected in PINS.items():
    path = BACKEND / name
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('Headless component changed or unavailable; reverify before updating the pin: ' + name)

sys.path.insert(0, str(BACKEND))
entrypoint = 'jy14_headless.py'
if len(sys.argv) > 1 and sys.argv[1] == 'edit':
    entrypoint = 'native_edit.py'
    del sys.argv[1]
elif len(sys.argv) > 1 and sys.argv[1] == 'export':
    del sys.argv[1]
    requested = None
    if '--backend' in sys.argv:
        index = sys.argv.index('--backend')
        if index + 1 >= len(sys.argv):
            raise SystemExit('--backend requires a value')
        requested = sys.argv[index + 1]
        del sys.argv[index:index + 2]
    requested = requested or os.environ.get('JIANYING_EXPORT_BACKEND')
    if requested and requested not in {'native', 'windows-ffmpeg'}:
        raise SystemExit('Unsupported export backend: ' + requested)
    if requested == 'windows-ffmpeg' or (requested is None and sys.platform == 'win32'):
        entrypoint = 'windows_export.py'
    else:
        entrypoint = 'native_export.py'
runpy.run_path(str(BACKEND / entrypoint), run_name='__main__')
