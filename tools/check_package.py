#!/usr/bin/env python3
"""Read-only source/pin checks; not a complete secret or legal audit."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIRS = {'.git', 'work', '__pycache__', '.pytest_cache', '.venv'}
LOCAL_CODECS = {'bridge/jy14_codec_hardened_11_4',
                'bridge/jy14_codec_hardened_11_4_b481'}
SUFFIXES = {'.py', '.cpp', '.h', '.json', '.md', '.yaml', '.txt'}
SPECIAL = {'.gitignore', '.gitattributes', 'NOTICE', 'LICENSE'}
WORKFLOWS = {'.github/workflows/windows-ffmpeg.yaml'}
# User-approved public IG case derivatives. Never turn this into a general
# media extension allowance: exact bytes, size and path are release-reviewed.
PUBLIC_MEDIA = {
    'docs/media/hypit-original-frames.png': ('0e7b55e8e1a7a759d6500d29a5d57ec8e93ac89e509504c8e9ed0daadd06d2cf', 947936),
    'docs/media/jianying-import-frames.png': ('64d1a63d6bfaf2c3c2c75de1c58b2beab58ad36499752a6855add08aa43f81dd', 884490),
    'docs/media/hypit-original-preview.mp4': ('0ac79c184ce093a71e73e4ad72f49e44bdf01134154834c983fd52d656095b83', 4421052),
    'docs/media/jianying-import-preview.mp4': ('ccaf385b0683b9e6b155e3c3422c3e3b2d27a8bb82b2f196344f2baa64e586cc', 4223556),
}
PATTERNS = {
    'personal-home-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    'signed-url': re.compile(r'https?://[^\s<>"\x27]+[?&](?:Signature|X-Amz-Signature|auth_key)=', re.I),
}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_public_media(path, relative):
    require(relative in PUBLIC_MEDIA, 'Unapproved public media: ' + relative)
    expected_hash, expected_size = PUBLIC_MEDIA[relative]
    require(path.is_file() and not path.is_symlink(), 'Nonregular public media: ' + relative)
    require(path.stat().st_size == expected_size and digest(path) == expected_hash,
            'Public media bytes differ: ' + relative)
    return expected_hash


def literal(path, name):
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing literal: ' + name)


def source_files():
    files = []
    for folder, directories, names in os.walk(ROOT, followlinks=False):
        for name in directories:
            require(not (Path(folder) / name).is_symlink(), 'Symlinked directory: ' + name)
        directories[:] = [name for name in directories if name not in LOCAL_DIRS]
        for name in names:
            path = Path(folder) / name
            relative = path.relative_to(ROOT).as_posix()
            if relative == '.git':  # Git worktrees store metadata in a file.
                continue
            if relative in LOCAL_CODECS:
                continue
            if relative.startswith('.github/'):
                require(relative in WORKFLOWS, 'Unreviewed GitHub automation: ' + relative)
            require(path.is_file() and not path.is_symlink(), 'Nonregular source: ' + relative)
            require(path.suffix in SUFFIXES or name in SPECIAL or relative in PUBLIC_MEDIA,
                    'Unexpected source type: ' + relative)
            files.append(path)
    return sorted(files)


def main():
    files = source_files()
    inventory = {}
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if relative in PUBLIC_MEDIA:
            inventory[relative] = verify_public_media(path, relative)
            continue
        raw = path.read_bytes()
        require(b'\x00' not in raw, 'Binary content in source: ' + relative)
        content = raw.decode('utf-8-sig')
        if relative in WORKFLOWS:
            require('pull_request_target' not in content and 'pull_request:' in content,
                    'Workflow must use normal pull_request execution')
            require(re.search(r'(?m)^permissions:\s*\n\s+contents:\s*read\s*$', content),
                    'Workflow permissions must be read-only')
            require('secrets.' not in content and 'timeout-minutes:' in content
                    and 'retention-days:' in content,
                    'Workflow secret, timeout or retention policy is invalid')
            for action in re.findall(r'(?m)^\s*-?\s*uses:\s*([^\s#]+)', content):
                require(re.fullmatch(r'[^@]+@[0-9a-f]{40}', action),
                        'Workflow action is not pinned to a reviewed commit: ' + action)
        for label, pattern in PATTERNS.items():
            require(not pattern.search(content), label + ' in ' + relative)
        if path.suffix == '.py':
            ast.parse(content, filename=relative)
        elif path.suffix == '.json':
            json.loads(content)
        elif path.suffix == '.md':
            for target in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', content):
                if '://' in target or target.startswith('#'):
                    continue
                target = target.split('#', 1)[0]
                require((path.parent / target).is_file(), 'Broken relative link in ' + relative + ': ' + target)
        inventory[relative] = hashlib.sha256(raw).hexdigest()

    wrapper = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'
    for name, expected in literal(wrapper, 'PINS').items():
        require(digest(ROOT / 'engine' / name) == expected, 'Skill pin differs: ' + name)
    runtime = ROOT / 'engine/headless_runtime.py'
    manifest_path = ROOT / 'bridge/SOURCE_MANIFEST.json'
    require(digest(manifest_path) == literal(runtime, 'IO_MANIFEST_SHA'), 'IO manifest pin differs')
    manifest = json.loads(manifest_path.read_bytes())
    for name, expected in manifest['source_files'].items():
        require(Path(name).name == name and digest(ROOT / 'bridge' / name) == expected, 'Bridge source pin differs: ' + name)
    for name, expected in literal(runtime, 'PINS').items():
        if name == 'jy14_codec_hardened_11_4' and not (ROOT / 'bridge' / name).exists():
            continue
        require(digest(ROOT / 'bridge' / name) == expected, 'Runtime IO/codec pin differs: ' + name)
    require(digest(ROOT / 'engine/native-resource-catalog.json') ==
            literal(ROOT / 'engine/native_resources.py', 'CATALOG_SHA'), 'Resource catalog pin differs')

    if (ROOT / '.git').is_dir():
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True).stdout
        names = {name.decode() for name in tracked.split(b'\x00') if name}
        require(names <= set(inventory), 'Git includes a file outside the checked source inventory')
    fingerprint = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    print(json.dumps({'status': 'source-checks-passed', 'files': len(files), 'inventory_sha256': fingerprint,
                      'official_binaries_in_source_inventory': False, 'network_called': False,
                      'approved_public_media': len(set(inventory).intersection(PUBLIC_MEDIA)),
                      'scope': 'syntax, known privacy patterns, relative links, source pins and tracked-file boundary'}))


if __name__ == '__main__':
    main()
