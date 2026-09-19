"""Bounded local sfnt font copying for ordinary native text materials.

This module never edits online resource identities, license fields, runtime
profiles or rendering code. Font files are dependencies, not media-library items.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil

import headless_runtime as rt


def require(value, message):
    if not value:
        raise ValueError(message)


def native_path(value, target):
    # Imported at use time so the new-plan builder can also import this module.
    from jy14_headless import native_media_path
    return native_media_path(value, target)


def inspect_font(value):
    require(isinstance(value, (str, Path)) and str(value), 'Font source must be a local file path')
    path = Path(value)
    # Directory aliases such as macOS /tmp are valid; the font itself must be regular.
    require(path.is_absolute() and not path.is_symlink() and path.is_file(),
              'Font source must be an absolute regular file, not a symlink')
    require(path.suffix.lower() in {'.otf', '.ttf'}, 'Only standalone OTF/TTF fonts are supported')
    require(12 <= path.stat().st_size <= 100 * 1024 * 1024, 'Font size is invalid')
    data = path.read_bytes()
    require(12 <= len(data) <= 100 * 1024 * 1024, 'Font size changed while reading')
    signature = data[:4]
    require(signature in {b'OTTO', b'\x00\x01\x00\x00'}, 'Unsupported sfnt signature or font collection')
    # Only explicit font assignment needs the optional parser. Existing builds
    # and ordinary copy edits verify their recorded bytes without importing it.
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise ValueError('Local font assignment requires fontTools; install requirements-fonts.txt '
                         'in this Python environment (see docs/LOCAL-FONTS.md)') from exc
    try:
        # System fonts can carry stale internal checksums/search parameters.
        # Parse their structures; snapshot integrity is enforced by our SHA-256.
        with TTFont(BytesIO(data), lazy=False, checkChecksums=0, ignoreDecompileErrors=False) as font:
            require(not {'fvar', 'gvar', 'CFF2'} & set(font.keys()), 'Variable fonts need a separate adapter')
            for tag in ('head', 'hhea', 'maxp', 'hmtx', 'cmap', 'name'):
                font[tag]  # Force decoding; opening the directory alone is not validation.
            if signature == b'OTTO':
                cff = font['CFF '].cff
                require(cff.major == 1, 'Only static CFF OTF fonts are supported')
                # Decode the CFF outline container/index, leaving execution of
                # every glyph's drawing program to the native font engine.
                require(len(cff.topDictIndex[0].CharStrings) == font['maxp'].numGlyphs,
                        'Invalid CFF glyph count')
            else:
                font['loca']
                font['glyf']  # lazy=False also decodes the TrueType glyph outlines.
            glyphs = font['maxp'].numGlyphs
            require(glyphs > 0 and len(font.getGlyphOrder()) == glyphs, 'Invalid font glyph count')
            names = font['name']
            postscript = names.getDebugName(6)
            require(postscript, 'Font has no PostScript name')
            family, style = names.getBestFamilyName() or '', names.getBestSubFamilyName() or ''
    except Exception as exc:
        raise ValueError('Cannot parse local font ' + str(path) + ': ' + (str(exc) or type(exc).__name__)) from exc
    return {'source': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'format': 'otf' if signature == b'OTTO' else 'ttf', 'postscript_name': postscript,
            'family': family, 'style': style, 'glyph_count': glyphs}


def content(material):
    obj = json.loads(material.get('content', '{}'))
    require(isinstance(obj, dict) and isinstance(obj.get('styles', []), list), 'Invalid text font content')
    require(all(isinstance(s, dict) for s in obj.get('styles', [])), 'Invalid text font style')
    return obj


def ordinary_font(material, obj, target):
    require(obj.get('styles'), 'Font operation needs explicit text styles')
    # These captured identity fields may be absent/empty for ordinary local text.
    # Reject identities; never clear them to force a local font replacement.
    for key in ('font_id', 'font_resource_id', 'font_resource', 'resource_id', 'effect_id',
                'font_team_id', 'font_source_platform', 'font_source', 'font_url'):
        require(material.get(key) in (None, '', 0, False), 'Online/registered font identity cannot be replaced: ' + key)
    for key, value in material.items():
        if 'font' in key.lower() and any(token in key.lower() for token in ('pay', 'vip', 'license', 'rights')):
            require(value in (None, '', 0, False, [], {}), 'Licensed font metadata requires a dedicated adapter')
    raw = material.get('font_path')
    require(isinstance(raw, str) and raw, 'Text material font_path is missing')
    old_path = native_path(raw, target)
    for style in obj['styles']:
        font = style.get('font')
        require(isinstance(font, dict) and set(font) <= {'id', 'path'} and font.get('id') in ('', None),
                  'Online/mixed font identity cannot be replaced')
        require(isinstance(font.get('path'), str) and font['path']
                  and native_path(font['path'], target) == old_path,
                  'Text material and rich-text font paths disagree')


def asset_for(source):
    asset = inspect_font(source)
    asset['relative'] = 'Resources/headless-fonts/' + asset['sha256'] + '.' + asset['format']
    return asset


def collect_plan(plan):
    """Inspect each supplied font once, without registering it as video/audio."""
    assets = {}
    for track in plan['tracks']:
        if track['type'] != 'text':
            continue
        for spec in track['segments']:
            if 'font_path' in spec:
                source = spec['font_path']
                require(isinstance(source, str) and source, 'font_path must be an absolute local font file')
                key = str(Path(source))
                if key not in assets:
                    assets[key] = asset_for(source)
    return assets


def copy_assets(assets, folder):
    for asset in assets:
        dest = folder / asset['relative']
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with Path(asset['source']).open('rb') as src, dest.open('xb') as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            os.chmod(dest, 0o600)
        require(rt.digest(dest) == asset['sha256'] == rt.digest(asset['source']), 'Font changed while copying')


def bind(material, asset, target):
    obj = content(material)
    ordinary_font(material, obj, target)
    new_path = str(target / asset['relative'])
    for style in obj['styles']:
        style['font']['path'] = new_path
        style['font']['id'] = ''
    # Commit only after every validation succeeds.
    encoded = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    material.update(font_path=new_path, content=encoded)


def set_text_font(material, source, target, sources=None):
    """Reuse parsed sources within one edit plan; validate each material's binding."""
    sources = {} if sources is None else sources
    require(isinstance(source, (str, Path)) and str(source), 'Font source must be a local file path')
    key = str(Path(source))
    if key not in sources:
        sources[key] = asset_for(source)
    asset = sources[key]
    bind(material, asset, target)
    return asset


def verify_binding(material, asset, target):
    ordinary_font(material, content(material), target)
    require(native_path(material['font_path'], target) == (target / asset['relative']).resolve(),
            'Planned font binding changed')


def rebase_existing(timeline, source, target):
    """Rebase references before editing; collect surviving dependencies afterwards."""
    import native_compound as compound
    for _, child in compound.graph(timeline):
        for material in child.get('materials', {}).get('texts', []):
            obj = content(material)
            owners = [(material, 'font_path')]
            owners += [(style['font'], 'path') for style in obj.get('styles', []) if isinstance(style.get('font'), dict)]
            changed = False
            for owner, key in owners:
                raw = owner.get(key)
                if not isinstance(raw, str) or not raw:
                    continue
                path = native_path(raw, source)
                if target in path.parents:
                    path = source / path.relative_to(target)
                if source not in path.parents:
                    continue
                relative = path.relative_to(source)
                owner[key] = str(target / relative)
                changed |= owner is not material
            if changed:
                material['content'] = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def collect_edit_assets(timeline, source, target, assigned=()):
    """Collect only the local fonts referenced by the final edited timeline."""
    import native_compound as compound
    replacements = {a['relative']: a for a in assigned}
    assets = {}
    for _, child in compound.graph(timeline):
        for material in child.get('materials', {}).get('texts', []):
            obj = content(material)
            paths = [material.get('font_path')] + [s['font'].get('path') for s in obj.get('styles', [])
                                                   if isinstance(s.get('font'), dict)]
            for raw in paths:
                if not isinstance(raw, str) or not raw:
                    continue
                path = native_path(raw, target)
                if target not in path.parents:
                    continue
                relative = str(path.relative_to(target))
                if relative in assets:
                    continue
                if relative in replacements:
                    assets[relative] = replacements[relative]
                else:
                    # Existing resources remain byte-preserving snapshots, without
                    # applying the format restrictions for newly assigned fonts.
                    original = source / relative
                    require(original.is_file() and not original.is_symlink(), 'Existing font file is missing or nonregular')
                    assets[relative] = {'source': str(original), 'relative': relative,
                                        'sha256': rt.digest(original), 'size': original.stat().st_size}
    return list(assets.values())


def normalize_paths(timeline, target):
    """Normalize native font path spellings inside string-encoded rich text."""
    import native_compound as compound
    result = compound.normalize_paths(deepcopy(timeline), target)
    for _, child in compound.graph(result):
        for material in child.get('materials', {}).get('texts', []):
            raw = material.get('font_path')
            if isinstance(raw, str) and raw:
                material['font_path'] = str(native_path(raw, target))
            obj = content(material)
            for style in obj.get('styles', []):
                font = style.get('font')
                if isinstance(font, dict) and isinstance(font.get('path'), str) and font['path']:
                    font['path'] = str(native_path(font['path'], target))
            if 'content' in material:
                material['content'] = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return result


def recorded_assets(record, plan=None):
    """A missing inventory denotes legacy evidence; an empty list is explicit."""
    if 'font_assets' not in record:
        plan = record if plan is None else plan
        explicit = any(op.get('op') == 'set_text_font' for op in plan.get('operations', []))
        explicit |= any('font_path' in spec for track in plan.get('tracks', [])
                        if track.get('type') == 'text' for spec in track.get('segments', []))
        require(not explicit, 'Font operations require a font asset inventory')
        return None
    assets = record['font_assets']
    require(isinstance(assets, list) and all(
        isinstance(asset, dict) and isinstance(asset.get('relative'), str)
        and isinstance(asset.get('sha256'), str) and type(asset.get('size')) is int
        and asset['size'] >= 0 for asset in assets), 'Invalid font asset inventory')
    return assets


def verify_assets(assets, timeline, target, folder):
    import native_compound as compound
    expected = set()
    for asset in assets:
        relative = Path(asset['relative'])
        require(not relative.is_absolute() and '..' not in relative.parts and relative.parts, 'Unsafe copied font location')
        path = folder / relative
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()),
                'Copied font file is missing or outside the build')
        require(path.stat().st_size == asset['size'] and rt.digest(path) == asset['sha256'], 'Copied font bytes changed')
        expected.add(str((target / relative).resolve()))
    graph = compound.graph(timeline) if timeline.get('materials', {}).get('drafts') else [(None, timeline)]
    for _, child in graph:
        for material in child.get('materials', {}).get('texts', []):
            raw = material.get('font_path')
            obj = content(material)
            paths = [raw] + [s['font'].get('path') for s in obj.get('styles', [])
                             if isinstance(s.get('font'), dict)]
            for raw in paths:
                if isinstance(raw, str) and raw:
                    path = native_path(raw, target)
                    if path.is_relative_to(target.resolve()):
                        require(str(path) in expected, 'Draft-local font reference has no verified dependency')
            # New-plan bindings and edited-timeline preservation are checked by
            # their callers; existing styles may legitimately use different fonts.
    return len(expected)
