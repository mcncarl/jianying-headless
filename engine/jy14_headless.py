#!/usr/bin/env python3
"""Create new Jianying 11.4 drafts from local media, without UI preparation.

Media is copied into the new draft's Resources directory. Existing drafts and
global bookmark stores are never edited. Native UI acceptance is a separate
step. All times in the plan are integer microseconds.
"""
import argparse
from copy import deepcopy
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

import headless_runtime as nd
import native_motion as motion
import native_resources as resources
import native_effects as effects
import native_visual_effects as visual_effects
import native_fonts as fonts
from runtime_profiles import validate_timeline_schema

HERE = Path(__file__).resolve().parent
BLUEPRINT_SHA = '91f7eddad5bff9af23eb88b53713c180e3e3d4054edd469140cfa9aa56bc1dc9'
SCHEMA = 'jy14-headless-plan/v1'
MICROS = 1_000_000
STILL_CAPACITY_US = 10_800_000_000  # Native photo/GIF material capacity, not decoded GIF duration.
# Literal found in the pinned 11.4 libvideoeditor and confirmed on native save.
DRAFT_PATH_TOKEN = '##_draftpath_placeholder_0E685133-18CE-45ED-8CB8-2904A212EC80_##/'
CACHED_SOUNDS = {
    '啵1': {'relative': 'music/5bb4c18515e6059da16432af0db0f1dc.mp3',
           'sha256': '592be87899cdbb7ae5c4665ad9f5847d5a1060106e315b0222786a6131acb9a8', 'size': 5600},
}


def require(value, message):
    if not value:
        raise ValueError(message)


def identifier():
    return str(uuid.uuid4()).upper()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data if isinstance(data, bytes) else nd.packed(data))
        stream.flush()
        os.fsync(stream.fileno())


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, label + ' must be an integer >= ' + str(minimum))
    return value


def number(value, label, low, high):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            label + ' is out of range')
    return value


def keys(value, allowed, label):
    require(isinstance(value, dict) and not set(value).difference(allowed), label + ' has unsupported fields')


def regular_file(path):
    require(isinstance(path, (str, Path)) and str(path), 'A media path is required')
    path = Path(path)
    require(path.is_absolute(), 'Media paths must be absolute')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), 'Expected a regular file: ' + str(path))
    resolved = path.resolve(strict=True)
    require(resolved.stat().st_size > 0, 'Media is empty')
    return resolved


def probe(path):
    path = regular_file(path)
    before = path.stat()
    data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                              '-of', 'json', str(path)]))
    video = [s for s in data['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')]
    audio = [s for s in data['streams'] if s['codec_type'] == 'audio']
    require(len(video) <= 1 and len(audio) <= 1 and (video or audio), 'Only one video/audio stream per source is supported')
    stream = video[0] if video else audio[0]
    media_type = 'video' if video else 'music'
    duration = float(stream.get('duration', data['format'].get('duration', 0)))
    if video:
        codec = stream['codec_name']
        require(codec in {'h264', 'hevc', 'png', 'mjpeg', 'gif'},
                'Supported visual media: H.264, HEVC, PNG, JPEG and GIF; no silent transcode')
        if codec in {'png', 'mjpeg'}:
            counted = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
                                '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)]))
            require(len(counted['streams']) == 1 and counted['streams'][0].get('nb_read_frames') == '1',
                    'A photo source must contain exactly one frame')
            media_type, duration = 'photo', STILL_CAPACITY_US / MICROS
        elif codec == 'gif':
            media_type = 'gif'
        rotation = float(stream.get('tags', {}).get('rotate', 0))
        for side in stream.get('side_data_list', []):
            rotation = float(side.get('rotation', rotation))
        require(rotation % 360 == 0, 'Rotated source metadata needs an explicit normalization step')
        if media_type == 'video':
            require(stream.get('pix_fmt') in {'yuv420p', 'yuvj420p', 'yuv420p10le'}, 'Unsupported pixel format')
    require(math.isfinite(duration) and duration > 0, 'Media duration is unavailable')
    fingerprint = nd.digest(path)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'Media changed during probe')
    return {'source': str(path), 'sha256': fingerprint, 'size': before.st_size,
            'duration_us': round(duration * MICROS), 'kind': 'video' if video else 'audio',
            'duration_basis': 'native-still-capacity' if media_type == 'photo' else 'decoded-media-duration',
            'decoded_duration_us': None if media_type == 'photo' else round(duration * MICROS),
            'media_type': media_type,
            'width': stream.get('width', 0), 'height': stream.get('height', 0),
            'codec': stream['codec_name'], 'has_audio': bool(audio)}


def blueprint():
    require(nd.digest(HERE / 'blueprint.json') == BLUEPRINT_SHA, 'Native blueprint changed; review its provenance')
    result = read_json(HERE / 'blueprint.json')
    require(result['runtime_manifest'] == nd.MANIFEST_SHA, 'Blueprint differs from its captured provenance')
    return result


def validate_plan(plan):
    """Return media, duration and parsed fonts for reuse throughout this build."""
    keys(plan, {'schema', 'name', 'canvas', 'tracks'}, 'Plan')
    require(plan.get('schema') == SCHEMA, 'Unsupported plan schema')
    name = plan.get('name')
    require(isinstance(name, str) and name.strip() == name and name and len(name.encode()) < 200
            and name not in {'.', '..'} and not any(c in name for c in '/\\\x00\n\r') and not name.startswith('.'),
            'Draft name must be a visible single directory component')
    keys(plan.get('canvas'), {'width', 'height', 'fps'}, 'Canvas')
    for key in ('width', 'height'):
        require(16 <= integer(plan['canvas'].get(key), key) <= 8192, 'Canvas dimension outside 16..8192')
    require(plan['canvas'].get('fps') in {24, 25, 30, 50, 60}, 'Unsupported timeline frame rate')
    require(isinstance(plan.get('tracks'), list), 'Tracks must be a list')
    assets = {}
    seen_main = False
    duration = 0
    for ti, track in enumerate(plan['tracks']):
        keys(track, {'type', 'name', 'segments'}, 'Track')
        kind = track.get('type')
        require(kind in {'video', 'audio', 'text', 'filter', 'effect'}, 'Supported tracks: video, audio, text, filter, effect')
        if kind in {'filter', 'effect'}:
            require(sum(t.get('type') == kind for t in plan['tracks']) == 1,
                    'Stacked tracks of the same visual-effect kind need separate native acceptance')
        require(isinstance(track.get('name', kind), str) and track.get('name', kind).strip(), 'Track name must be text')
        require(isinstance(track.get('segments'), list) and track['segments'], 'Each track needs at least one segment')
        if kind == 'video' and not seen_main:
            require(ti == 0, 'The main video track must be the first track')
            seen_main = True
        prior_end = 0
        for seg in track['segments']:
            allowed = {'start_us', 'duration_us', 'source', 'source_start_us', 'source_duration_us', 'speed', 'volume'}
            allowed.add('keyframes')
            if kind == 'video':
                allowed |= {'scale', 'x', 'y', 'rotation', 'opacity', 'mask', 'transition_out'}
            elif kind == 'text':
                allowed = {'start_us', 'duration_us', 'text', 'size', 'x', 'y', 'color', 'border_color', 'border_width',
                           'keyframes', 'opacity', 'text_effect', 'font_path'}
            elif kind in {'filter', 'effect'}:
                allowed = {'start_us', 'duration_us', 'name', 'strength' if kind == 'filter' else 'params'}
            keys(seg, allowed, 'Segment')
            start = integer(seg.get('start_us', 0), 'start_us')
            span = integer(seg.get('duration_us'), 'duration_us', 1)
            motion.validate(seg, kind)
            visual_effects.validate(seg, kind)
            require(start >= prior_end, 'Segments on the same track must be ordered and nonoverlapping')
            if ti == 0 and kind == 'video':
                require(abs(start - prior_end) <= 1, 'Main video must be continuous; place gaps on overlay tracks only')
            prior_end = start + span
            duration = max(duration, prior_end)
            if kind == 'text':
                require(isinstance(seg.get('text'), str) and seg['text'].strip(), 'Text must be nonempty')
                number(seg.get('size', 6), 'Text size', 1, 100)
                number(seg.get('border_width', .05), 'Border width', 0, 1)
                for field in ('color', 'border_color'):
                    require(re.fullmatch(r'#[0-9a-fA-F]{6}', seg.get(field, '#FFFFFF')), 'Use #RRGGBB colors')
            elif kind in {'video', 'audio'}:
                source = str(regular_file(seg.get('source')))
                if source not in assets:
                    assets[source] = probe(source)
                asset = assets[source]
                require(asset['kind'] == kind, 'Track type and media type disagree')
                source_start = integer(seg.get('source_start_us', 0), 'source_start_us')
                source_span = integer(seg.get('source_duration_us', span), 'source_duration_us', 1)
                speed = number(seg.get('speed', 1), 'Speed', .1, 8)
                if asset['media_type'] == 'photo':
                    require(source_start == 0 and speed == 1, 'Photos do not support source offset or speed')
                require(abs(source_span / speed - span) <= 2, 'Source/target durations disagree with speed')
                require(source_start + source_span <= asset['duration_us'] + 1, 'Source trim exceeds the media duration')
                number(seg.get('volume', 1), 'Volume', 0, 4)
                number(seg.get('scale', 1), 'Scale', .01, 10)
                number(seg.get('rotation', 0), 'Rotation', -360, 360)
            for field in ('x', 'y'):
                number(seg.get(field, 0), field, -5, 5)
    if plan['tracks']:
        require(seen_main, 'A nonempty draft needs a main video track')
        main_end = max(s.get('start_us', 0) + s['duration_us'] for s in plan['tracks'][0]['segments'])
        require(duration == main_end, 'Overlay, audio and text must fit within the main video duration')
    font_assets = fonts.collect_plan(plan)
    effects.transition_audit(plan, assets)
    return assets, duration, font_assets


def remap(value, ids):
    if isinstance(value, dict):
        return {k: remap(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [remap(v, ids) for v in value]
    return ids.get(value, value) if isinstance(value, str) else value


def rgb(color):
    return [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]


def text_material(material, seg):
    content = json.loads(material['content'])
    content['text'] = seg['text']
    length = len(seg['text'].encode('utf-16-le')) // 2
    color, border = seg.get('color', '#FFFFFF'), seg.get('border_color', '#000000')
    size, width = seg.get('size', 6), seg.get('border_width', .05)
    for style in content['styles']:
        style['range'] = [0, length]
        style['size'] = size
        style['fill']['content']['solid']['color'] = rgb(color)
        for stroke in style.get('strokes', []):
            stroke['width'] = width
            stroke['content']['solid']['color'] = rgb(border)
    material.update(content=json.dumps(content, ensure_ascii=False, separators=(',', ':')),
                    font_size=size, text_color=color, border_color=border, border_width=width)


def timeline_for(plan, assets, target, tid, bp, font_assets=None):
    font_assets = fonts.collect_plan(plan) if font_assets is None else font_assets
    doc = deepcopy(bp['timeline'])
    doc.update(id=tid, tracks=[], materials={}, duration=0, color_space=0,
               canvas_config={k: plan['canvas'][k] for k in ('width', 'height')})
    doc['fps'] = float(plan['canvas']['fps'])
    registrations = {}
    for ti, track in enumerate(plan['tracks']):
        kind = track['type']
        if kind in {'filter', 'effect'}:
            newtrack = visual_effects.overlay_track(kind, track.get('name', kind), track['segments'][0])
            for spec in track['segments']:
                segment, material = visual_effects.overlay_segment(kind, spec, target, ti)
                doc['materials'].setdefault(visual_effects.BUCKETS[kind], []).append(material)
                newtrack['segments'].append(segment)
                doc['duration'] = max(doc['duration'], spec.get('start_us', 0) + spec['duration_us'])
            doc['tracks'].append(newtrack)
            continue
        sample = bp[kind]
        newtrack = deepcopy(sample['track'])
        newtrack.update(id=identifier(), segments=[], name=track.get('name', kind), is_default_name=False)
        if kind == 'video' and ti > 0:
            newtrack['flag'] = 2
        for si, spec in enumerate(track['segments']):
            ids = {sample['segment']['id']: identifier()}
            ids.update({mat['id']: identifier() for _, mat in sample['materials']})
            segment = remap(deepcopy(sample['segment']), ids)
            segment['target_timerange'] = {'start': spec.get('start_us', 0), 'duration': spec['duration_us']}
            segment['track_render_index'] = ti
            if kind == 'text':
                segment['render_index'] = 14000 + ti
            elif kind == 'video' and ti > 0:
                segment['render_index'] = ti
            else:
                segment.pop('render_index', None)
            if kind in {'video', 'text'}:
                segment['clip']['transform'] = {'x': spec.get('x', 0), 'y': spec.get('y', -.78 if kind == 'text' else 0)}
                segment['clip']['scale'] = {'x': spec.get('scale', 1), 'y': spec.get('scale', 1)}
                if spec.get('rotation'):
                    segment['clip']['rotation'] = spec['rotation']
            if kind != 'text':
                asset = assets[str(Path(spec['source']).resolve())]
                segment.update(source_timerange={'start': spec.get('source_start_us', 0),
                                                 'duration': spec.get('source_duration_us', spec['duration_us'])},
                               speed=spec.get('speed', 1), volume=spec.get('volume', 1))
            for bucket, original in sample['materials']:
                material = remap(deepcopy(original), ids)
                if bucket == 'speeds':
                    material['speed'] = spec.get('speed', 1)
                if bucket in {'videos', 'audios'}:
                    material.update(path=str(target / asset['relative']), duration=asset['duration_us'],
                                    local_material_id=asset['local_id'])
                    if bucket == 'videos':
                        media_type = asset.get('media_type', 'video')
                        material.update(width=asset['width'], height=asset['height'], material_name=Path(asset['source']).name,
                                        type=media_type, has_audio=asset['has_audio'])
                        if media_type in {'photo', 'gif'}:
                            material['duration'] = STILL_CAPACITY_US
                    else:
                        material.update(name=Path(asset['source']).name, music_id=asset['local_id'], resource_id=asset['local_id'])
                if bucket == 'texts':
                    text_material(material, spec)
                    if 'font_path' in spec:
                        fonts.bind(material, font_assets[str(Path(spec['font_path']))], target)
                doc['materials'].setdefault(bucket, []).append(material)
            if kind != 'text':
                reg = deepcopy(bp['local_registration'])
                reg.update(segmentId=segment['id'], materialId=asset['sha256'][:32],
                           materialName=Path(asset['source']).name, rank=str(si + 1))
                registrations[segment['id']] = reg
            motion.apply(segment, doc['materials'], spec, kind, target)
            effects.apply(segment, doc['materials'], spec, target)
            visual_effects.apply_text(segment, doc['materials'], spec, target)
            newtrack['segments'].append(segment)
            doc['duration'] = max(doc['duration'], spec.get('start_us', 0) + spec['duration_us'])
        doc['tracks'].append(newtrack)
    return doc, registrations


def library_record(asset, target, now):
    media_type = asset.get('media_type', 'video' if asset['kind'] == 'video' else 'music')
    duration = 5 * MICROS if media_type == 'photo' else asset['duration_us']
    return {'ai_group_type': '', 'create_time': now // MICROS, 'duration': duration, 'enter_from': 0,
            'extra_info': Path(asset['source']).name, 'file_Path': str(target / asset['relative']),
            'height': asset['height'], 'width': asset['width'], 'id': asset['local_id'],
            'import_time': now // MICROS, 'import_time_ms': now, 'item_source': 1, 'material_color_tag': '',
            'md5': '', 'metetype': media_type,
            'roughcut_time_range': {'duration': -1, 'start': -1} if media_type in {'photo', 'gif'} else
                                  {'duration': duration, 'start': 0},
            'sub_time_range': {'duration': -1, 'start': -1}, 'type': 0}


def files_manifest(folder):
    result = {}
    for path in sorted(folder.rglob('*')):
        require(not path.is_symlink(), 'Symlinks are not allowed in built drafts')
        if path.is_file():
            result[str(path.relative_to(folder))] = {'sha256': nd.digest(path), 'size': path.stat().st_size}
    return result


def build(plan_path, out):
    runtime = nd.doctor()
    plan = read_json(plan_path)
    assets, duration, font_assets = validate_plan(plan)
    bp = blueprint()
    target = nd.DRAFT_ROOT / plan['name']
    require(not target.exists(), 'Target already exists; choose a new draft name')
    out = nd.fresh_directory(out)
    folder = out / 'draft'
    folder.mkdir(mode=0o700)
    now = time.time_ns() // 1000
    did, tid, pid = identifier(), identifier(), identifier()
    for asset in assets.values():
        asset['local_id'] = str(uuid.uuid4())
        # Content hash plus extension avoids arbitrary path components and name collisions.
        asset['relative'] = 'Resources/headless-media/' + asset['sha256'] + Path(asset['source']).suffix.lower()
        dest = folder / asset['relative']
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with Path(asset['source']).open('rb') as src, dest.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            os.chmod(dest, 0o600)
        require(nd.digest(dest) == asset['sha256'] == nd.digest(asset['source']), 'Source changed while copying')
    native_resources = resources.prepare(plan, folder, runtime)
    fonts.copy_assets(font_assets.values(), folder)
    timeline, reg = timeline_for(plan, assets, target, tid, bp, font_assets)
    metadata = deepcopy(bp['metadata'])
    metadata.update(draft_id=did, draft_name=target.name, draft_fold_path=str(target), draft_root_path=str(nd.DRAFT_ROOT),
                    tm_draft_create=now, tm_draft_modified=now, tm_duration=duration,
                    draft_materials=[{'type': 0, 'value': [library_record(a, target, now) for a in assets.values()]}] +
                                    [{'type': t, 'value': []} for t in (1, 2, 3, 6, 7, 8)])
    project = deepcopy(bp['project'])
    project.update(id=pid, main_timeline_id=tid, create_time=now, update_time=now,
                   timelines=[{'id': tid, 'name': '时间线01', 'create_time': now, 'update_time': now, 'is_marked_delete': False}])
    legacy = deepcopy(bp['legacy'])
    legacy.update(id=identifier(), duration=0, tracks=[], materials={}, fps=float(plan['canvas']['fps']))
    timeline_dir = folder / 'Timelines' / tid
    timeline_dir.mkdir(parents=True, mode=0o700)
    h = nd.helper()
    h._encrypt_metadata_from_memory(nd.packed(timeline), folder / 'draft_info.json')
    require(h._decrypt_metadata_in_memory(folder / 'draft_info.json') == timeline, 'Timeline codec round-trip failed')
    cipher = (folder / 'draft_info.json').read_bytes()
    for dest in (folder / 'template-2.tmp', timeline_dir / 'draft_info.json', timeline_dir / 'template-2.tmp',
                 folder / 'draft_info.json.bak', timeline_dir / 'draft_info.json.bak'):
        write(dest, cipher)
    write(folder / 'key_value.json', reg)
    for name in ('project.json', 'project.json.bak'):
        write(folder / 'Timelines' / name, project)
    write(timeline_dir / 'template.tmp', legacy)
    write(folder / 'timeline_layout.json', {'activeTimeline': tid, 'dockItems': [{'dockIndex': 0, 'ratio': 1,
                                                                           'timelineIds': [tid], 'timelineNames': ['时间线01']}],
                                          'layoutOrientation': 1})
    write(folder / 'draft_virtual_store.json', {'draft_materials': [], 'draft_virtual_store': [
        {'type': 0, 'value': [{'creation_time': 0, 'display_name': '', 'filter_type': 0, 'id': '', 'import_time': 0,
                             'import_time_us': 0, 'material_color_tag': '', 'sort_sub_type': 0, 'sort_type': 0,
                             'subdraft_filter_type': 0}]},
        {'type': 1, 'value': [{'child_id': a['local_id'], 'parent_id': ''} for a in assets.values()]}, {'type': 2, 'value': []}]})
    write(folder / 'draft_settings', ('[General]\ncloud_last_modify_platform=mac\ndraft_create_time=%d\n'
                                     'draft_last_edit_time=%d\n' % (now // MICROS, now // MICROS)).encode())
    # Cover generation is local and creates only a JPEG, never an intermediate video.
    videos = [a for a in assets.values() if a['kind'] == 'video']
    if videos:
        command = ['ffmpeg', '-v', 'error', '-n', '-i', str(folder / videos[0]['relative']),
                   '-frames:v', '1', '-vf', 'scale=320:-2', str(folder / 'draft_cover.jpg')]
    else:
        command = ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=black:s=320x180',
                   '-frames:v', '1', str(folder / 'draft_cover.jpg')]
    subprocess.run(command, check=True, capture_output=True)
    write(timeline_dir / 'draft_cover.jpg', (folder / 'draft_cover.jpg').read_bytes())
    font_sizes = {a['relative']: a['size'] for a in font_assets.values()}
    metadata['draft_timeline_materials_size_'] = sum(a['size'] for a in assets.values()) + sum(font_sizes.values()) + len(cipher)
    h._encrypt_metadata_from_memory(nd.packed(metadata), folder / 'draft_meta_info.json')
    require(h._decrypt_metadata_in_memory(folder / 'draft_meta_info.json') == metadata, 'Metadata codec round-trip failed')
    write(out / 'plan.json', plan)
    record = {'schema': 'jy14-headless-build/v1', 'name': target.name, 'target': str(target), 'draft_id': did,
              'timeline_id': tid, 'project_id': pid, 'created_us': now, 'duration_us': duration,
              'blueprint_sha256': BLUEPRINT_SHA, 'runtime_manifest': nd.MANIFEST_SHA,
              'runtime_profile': runtime['runtime_profile'], 'runtime': runtime,
              'assets': list(assets.values()), 'native_resources': native_resources,
              'font_assets': list(font_assets.values()),
              'transition_audit': effects.transition_audit(plan, assets),
              'plan_sha256': nd.digest(out / 'plan.json'),
              'files': files_manifest(folder), 'media_copy_policy': 'draft-owned-resources',
              'ui_preparation_used': False, 'live_written': False, 'native_ui_acceptance': 'pending'}
    write(out / 'build.json', record)
    verify_build(out)
    return {'status': 'built', 'build': str(out), 'name': target.name, 'duration_us': duration,
            'tracks': len(plan['tracks']), 'media_files': len(assets), 'font_files': len(font_sizes), 'live_written': False,
            'native_resource_usage': [{'key': r['key'], **r['usage']} for r in native_resources if 'usage' in r],
            'ui_preparation_used': False, 'native_ui_acceptance': 'pending'}


def native_media_path(value, target):
    """Resolve only the native draft-relative spelling observed after 11.4 save."""
    prefix = DRAFT_PATH_TOKEN
    if value.startswith('##_draftpath_placeholder_'):
        require(value.startswith(prefix), 'Unknown native media placeholder')
        relative = Path(value[len(prefix):])
        require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe draft-relative media path')
        return (target / relative).resolve()
    path = Path(value)
    if not path.is_absolute():
        require('..' not in path.parts, 'Unsafe relative media path')
        return (target / path).resolve()
    return path.resolve()


def verify_structure(timeline, metadata, plan, assets, target, allow_native_resource_cache=False,
                     runtime_profile=None, font_assets=()):
    validate_timeline_schema(timeline, runtime_profile)
    require(all(timeline['canvas_config'][k] == plan['canvas'][k] for k in ('width', 'height')), 'Canvas changed')
    require(timeline.get('fps', 30) == plan['canvas']['fps'], 'Timeline frame rate changed')
    require(metadata['draft_fold_path'] == str(target) and metadata['draft_name'] == target.name, 'Draft identity mismatch')
    require(len(timeline.get('tracks', [])) == len(plan['tracks']), 'Track count changed')
    index = {}
    for bucket, mats in timeline['materials'].items():
        for mat in mats:
            if mat['id'] in index:
                require(index[mat['id']] == (bucket, mat), 'Conflicting duplicate material ID')
            index[mat['id']] = (bucket, mat)
    ids = set(index)
    max_end = 0
    native_resource_bindings = []
    font_index = {a['source']: a for a in font_assets}
    for track, wanted in zip(timeline.get('tracks', []), plan['tracks']):
        require(track['type'] == wanted['type'] and len(track['segments']) == len(wanted['segments']), 'Track kind/length changed')
        require(track['id'] not in ids, 'Duplicate track ID')
        ids.add(track['id'])
        for actual, spec in zip(track['segments'], wanted['segments']):
            require(actual['id'] not in ids, 'Duplicate segment ID')
            ids.add(actual['id'])
            for group in actual.get('common_keyframes', []):
                for node in [group] + group['keyframe_list']:
                    require(node['id'] not in ids, 'Duplicate keyframe ID')
                    ids.add(node['id'])
            for ref in [actual['material_id']] + actual.get('extra_material_refs', []):
                require(ref in index, 'Dangling material reference')
            timerange = actual['target_timerange']
            # Native save rounds microseconds to its frame grid. Bounds remain within one frame.
            tolerance = math.ceil(MICROS / plan['canvas']['fps'])
            require(abs(timerange.get('start', 0) - spec.get('start_us', 0)) <= tolerance
                    and abs(timerange['duration'] - spec['duration_us']) <= tolerance, 'Target timing changed')
            max_end = max(max_end, timerange.get('start', 0) + timerange['duration'])
            material = index[actual['material_id']][1]
            motion.verify(actual, index, spec, wanted['type'], tolerance)
            native_resource_bindings.extend(effects.verify(actual, index, spec, tolerance, target,
                                                           native_media_path, allow_native_resource_cache))
            native_resource_bindings.extend(visual_effects.verify(actual, index, spec, wanted['type'], target,
                                                                  native_media_path, allow_native_resource_cache))
            if 'mask' in spec:
                mask = next(index[r][1] for r in actual.get('extra_material_refs', []) if index[r][0] == 'common_mask')
                native_resource_bindings.append(resources.verify_binding('mask/' + spec['mask']['shape'], mask['path'],
                    target, native_media_path, allow_native_cache=allow_native_resource_cache))
            animated = set(spec.get('keyframes', {}))
            if wanted['type'] in {'video', 'text'}:
                clip = actual['clip']
                for axis in ('x', 'y'):
                    default = -.78 if wanted['type'] == 'text' and axis == 'y' else 0
                    if axis not in animated:
                        require(abs(clip['transform'].get(axis, 0) - spec.get(axis, default)) < 1e-5, 'Position changed')
                    if 'scale' not in animated:
                        require(abs(clip['scale'].get(axis, 1) - spec.get('scale', 1)) < 1e-5, 'Scale changed')
                if 'rotation' not in animated:
                    require(abs(clip.get('rotation', 0) - spec.get('rotation', 0)) < 1e-5, 'Rotation changed')
            if wanted['type'] == 'text':
                if 'font_path' in spec:
                    font_asset = font_index.get(str(Path(spec['font_path'])))
                    require(font_asset is not None, 'Planned font has no verified dependency')
                    fonts.verify_binding(material, font_asset, target)
                content = json.loads(material['content'])
                require(content['text'] == spec['text'], 'Subtitle text changed')
                expected_length = len(spec['text'].encode('utf-16-le')) // 2
                require(content['styles'], 'Text style disappeared')
                for style in content['styles']:
                    require(style['range'] == [0, expected_length], 'Text style range changed')
                    require(abs(style['size'] - spec.get('size', 6)) < 1e-5, 'Text size changed')
                    if 'text_effect' in spec:
                        # Color and stroke are owned by the captured native effect,
                        # whose identity and per-style path were checked above.
                        continue
                    require(all(abs(a - b) < 1e-5 for a, b in zip(style['fill']['content']['solid']['color'],
                                                               rgb(spec.get('color', '#FFFFFF')))), 'Text color changed')
                    strokes = style.get('strokes', [])
                    require(strokes, 'Text stroke disappeared')
                    for stroke in strokes:
                        require(abs(stroke.get('width', 0) - spec.get('border_width', .05)) < 1e-5, 'Text stroke width changed')
                        require(all(abs(a - b) < 1e-5 for a, b in zip(stroke['content']['solid']['color'],
                                                                   rgb(spec.get('border_color', '#000000')))), 'Text stroke color changed')
            elif wanted['type'] in {'video', 'audio'}:
                asset = next(a for a in assets if a['source'] == str(Path(spec['source']).resolve()))
                if wanted['type'] == 'video':
                    require(material['type'] == asset.get('media_type', 'video'), 'Visual media type changed')
                require(native_media_path(material['path'], target) ==
                        (target / asset['relative']).resolve(), 'Media binding changed')
                require(abs(actual.get('speed', 1) - spec.get('speed', 1)) < 1e-6, 'Speed changed')
                if 'volume' not in animated:
                    require(abs(actual.get('volume', 1) - spec.get('volume', 1)) < 1e-5, 'Volume changed')
                require(abs(actual['source_timerange'].get('start', 0) - spec.get('source_start_us', 0)) <= tolerance,
                        'Source trim changed')
                require(abs(actual['source_timerange']['duration'] - spec.get('source_duration_us', spec['duration_us']))
                        <= tolerance, 'Source duration changed')
    require(abs(timeline.get('duration', 0) - max_end) <= 1, 'Timeline duration mismatch')
    library = {v['id']: v for g in metadata['draft_materials'] if g['type'] == 0 for v in g['value']}
    for asset in assets:
        require(asset['local_id'] in library, 'Media-library registration is missing')
        if 'media_type' in asset:
            require(library[asset['local_id']]['metetype'] == asset['media_type'], 'Media-library type changed')
        require(native_media_path(library[asset['local_id']]['file_Path'], target) ==
                (target / asset['relative']).resolve(),
                'Media-library path differs')
    return native_resource_bindings


def verify_build(out):
    out = Path(out).resolve(strict=True)
    record = read_json(out / 'build.json')
    require(record['schema'] == 'jy14-headless-build/v1' and record['blueprint_sha256'] == BLUEPRINT_SHA
            and record['runtime_manifest'] == nd.MANIFEST_SHA, 'Build version or provenance differs')
    require(nd.digest(out / 'plan.json') == record['plan_sha256'], 'Plan changed after build')
    require(files_manifest(out / 'draft') == record['files'], 'Built draft changed')
    plan = read_json(out / 'plan.json')
    target = nd.DRAFT_ROOT / plan['name']
    require(record['target'] == str(target), 'Build target is not the planned direct-child draft')
    h = nd.helper()
    timeline = h._decrypt_metadata_in_memory(out / 'draft/draft_info.json')
    metadata = h._decrypt_metadata_in_memory(out / 'draft/draft_meta_info.json')
    font_assets = fonts.recorded_assets(record, plan)
    verify_structure(timeline, metadata, plan, record['assets'], target, font_assets=font_assets or ())
    if font_assets is not None:
        fonts.verify_assets(font_assets, timeline, target, out / 'draft')
    resources.verify_files(record.get('native_resources', []), out / 'draft', plan)
    return record


def exclusive_rename(src, dst):
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.renamex_np
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
    if fn(os.fsencode(src), os.fsencode(dst), 0x4):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(dst))


def index_entry(metadata, target):
    entry = {k: deepcopy(v) for k, v in metadata.items() if k.startswith(('draft_', 'tm_', 'cloud_', 'pippit_'))
             and k not in {'draft_materials', 'draft_materials_copied_info', 'draft_segment_extra_info',
                          'draft_enterprise_info', 'draft_timeline_materials_size_'}}
    entry.update(draft_cover=str(target / 'draft_cover.jpg'), draft_json_file=str(target / 'draft_info.json'),
                 draft_timeline_materials_size=metadata['draft_timeline_materials_size_'], streaming_edit_draft_ready=True)
    return entry


def read_xattrs(path):
    names = subprocess.check_output(['/usr/bin/xattr', str(path)], text=True).splitlines()
    return {name: bytes.fromhex(subprocess.check_output(['/usr/bin/xattr', '-px', name, str(path)], text=True))
            for name in names}


def copy_xattrs(source_attrs, destination, audit):
    """Preserve all user/security attributes; record the OS-owned per-file provenance separately."""
    write(audit / 'index-xattrs-before.json', {k: v.hex() for k, v in source_attrs.items()})
    for key, value in source_attrs.items():
        subprocess.run(['/usr/bin/xattr', '-wx', key, value.hex(), str(destination)], check=True, capture_output=True)
    copied = read_xattrs(destination)
    write(audit / 'index-xattrs-staged.json', {k: v.hex() for k, v in copied.items()})
    # A bounded copy experiment on this Mac showed xattr -w returns success but the
    # OS assigns a different provenance value to the new inode. Never strip it,
    # quarantine, or any other attribute to force an equality result.
    changed = sorted(k for k in set(source_attrs) | set(copied) if source_attrs.get(k) != copied.get(k))
    if 'com.apple.macl' in changed:
        # On this TCC-protected draft root macOS attaches a SIP-owned 72-byte
        # access-control label to the new inode even when the source index had
        # none.  User space cannot remove or replace it.  Accept only this
        # observed one-way OS addition; removal and mutation remain blocked.
        require('com.apple.macl' not in source_attrs
                and isinstance(copied.get('com.apple.macl'), bytes)
                and len(copied['com.apple.macl']) == 72,
                'OS access-control attribute changed outside the reviewed addition case')
    require(not set(changed) - {'com.apple.provenance', 'com.apple.macl'},
            'Extended attributes could not be preserved before commit: ' + ', '.join(changed)
            + '. No security attribute was stripped. If com.apple.macl differs, this environment '
              'needs a reviewed permission-preservation adapter; do not disable SIP or TCC.')
    require(('com.apple.provenance' in source_attrs) == ('com.apple.provenance' in copied),
            'OS provenance attribute disappeared or unexpectedly appeared')
    return copied, changed


def publish(out, audit, resume=False, verify_build_fn=None, verify_live_fn=None):
    verify_build_fn = verify_build_fn or verify_build
    verify_live_fn = verify_live_fn or verify_live
    out = Path(out).resolve(strict=True)
    record = verify_build_fn(out)
    h = nd.helper()
    runtime = h._validate_runtime_environment()
    require(record.get('runtime_profile') == runtime['runtime_profile'],
            'Build runtime differs; create a fresh isolated build with the current profile')
    h._ensure_editor_closed(True)
    target = Path(record['target'])
    require(target.parent == nd.DRAFT_ROOT and not target.is_symlink(), 'Invalid new-draft target')
    require(target.exists() == resume, 'Use resume-publish only for an existing unchanged build; publish requires a new target')
    if resume:
        require(target.is_dir() and files_manifest(target) == record['files'],
                'Resume refused: target differs from the exact unpublished build')
    audit = nd.fresh_directory(audit)
    lock, identity = h._acquire_directory_transaction_lock(nd.DRAFT_ROOT, 'headless draft root')
    phase = 'locked'
    temporary = stage = None
    try:
        root = h._snapshot_file(nd.DRAFT_ROOT / 'root_meta_info.json', 'home index')
        original = h._parse_strict_json(root.content, 'home index')
        require(original['root_path'] == str(nd.DRAFT_ROOT), 'Home index root path mismatch')
        entries = [e for e in original['all_draft_store'] if e.get('draft_fold_path') == str(target)
                   or e.get('draft_id') == record['draft_id']]
        if entries:
            require(resume and len(entries) == 1 and entries[0].get('draft_id') == record['draft_id']
                    and entries[0].get('draft_fold_path') == str(target), 'Draft registration conflicts')
            phase = 'index_already_registered'
            result = dict(verify_live_fn(out), status='already_registered', index_written=False, audit=str(audit))
            write(audit / 'result.json', result)
            return result
        write(audit / 'root_meta_info.original.json', root.content)
        xattrs = read_xattrs(root.path)
        # Prepare and validate the index BEFORE placing a new draft at its final
        # path. A permission failure must not strand an unregistered draft.
        meta = h._decrypt_metadata_in_memory(out / 'draft/draft_meta_info.json')
        updated = deepcopy(original)
        updated['all_draft_store'].insert(0, index_entry(meta, target))
        updated['draft_ids'] = integer(original['draft_ids'], 'draft_ids') + 1
        payload = nd.packed(updated)
        temporary = root.path.parent / ('.root_meta_info.headless-' + uuid.uuid4().hex + '.tmp')
        write(temporary, payload)
        os.chmod(temporary, root.mode)
        staged_xattrs, os_attribute_changes = copy_xattrs(xattrs, temporary, audit)
        phase = 'index_prepared'
        write(audit / 'prepared.json', {'temporary_index': str(temporary), 'target': str(target),
                                      'index_sha256': nd.digest(temporary), 'resumed': resume})
        if not resume:
            stage = nd.DRAFT_ROOT / ('.jy14-headless-' + uuid.uuid4().hex)
            shutil.copytree(out / 'draft', stage, copy_function=shutil.copy2)
            require(files_manifest(stage) == record['files'], 'Staged file copy differs')
            h._ensure_editor_closed(True)
            h._revalidate_snapshot(root, 'before registering new draft')
            exclusive_rename(stage, target)
        phase = 'draft_placed'
        write(audit / 'published.json', {'target': str(target), 'draft_id': record['draft_id'], 'registered': False,
                                        'resumed': resume})
        h._ensure_editor_closed(True)
        h._revalidate_snapshot(root, 'immediately before home index replacement')
        require(files_manifest(target) == record['files'], 'Published draft changed before registration')
        require(read_xattrs(root.path) == xattrs, 'Home index extended attributes changed')
        require(temporary.read_bytes() == payload and read_xattrs(temporary) == staged_xattrs,
                'Prepared home index or its attributes changed before commit')
        os.replace(temporary, root.path)
        phase = 'index_replaced'
        os.fsync(lock)
        after = read_json(root.path)
        require(after == updated and after['all_draft_store'][1:] == original['all_draft_store'], 'Unrelated home entries changed')
        final_xattrs = read_xattrs(root.path)
        write(audit / 'index-xattrs-after.json', {k: v.hex() for k, v in final_xattrs.items()})
        require(final_xattrs == staged_xattrs, 'Home index attributes changed unexpectedly during commit')
        result = verify_live_fn(out)
        result.update(status='created', audit=str(audit), ui_preparation_used=False, bookmarks_written=False,
                      original_index_sha256=root.sha256, current_index_sha256=nd.digest(root.path),
                      os_managed_attribute_changes=os_attribute_changes, security_attributes_preserved=True)
        write(audit / 'result.json', result)
        return result
    except Exception as error:
        # Retain evidence rather than deleting or overwriting a possibly edited
        # draft. A failed post-commit verification is not a rolled-back write.
        recovery = 'inspect-index-and-run-verify' if phase in {'index_replaced', 'index_already_registered'} else (
            'resume-publish-after-fixing-cause' if phase == 'draft_placed' or resume else
            'publish-after-fixing-cause')
        failure = {'status': 'failed', 'phase': phase, 'resumed': resume,
                   'index_replaced': phase == 'index_replaced', 'target': str(target),
                   'index_already_registered': phase == 'index_already_registered',
                   'temporary_index': str(temporary) if temporary else None,
                   'staged_draft': str(stage) if stage else None,
                   'recovery': recovery, 'error': str(error), 'files_deleted': False}
        try:
            write(audit / 'failure.json', failure)
        except OSError:
            pass  # Keep the original failure; do not mask it with audit IO.
        raise ValueError('Publish failed at ' + phase + ': ' + str(error)
                         + '. Retained audit: ' + str(audit) + '; next: ' + recovery) from error
    finally:
        h._release_directory_transaction_lock(lock)


def verify_live(out):
    out = Path(out).resolve(strict=True)
    record = read_json(out / 'build.json')
    require(record.get('runtime_manifest') == nd.MANIFEST_SHA and record.get('blueprint_sha256') == BLUEPRINT_SHA,
            'Build provenance changed')
    plan = read_json(out / 'plan.json')
    require(nd.digest(out / 'plan.json') == record['plan_sha256'], 'Plan changed')
    font_assets = fonts.recorded_assets(record, plan)
    target = nd.DRAFT_ROOT / plan['name']
    require(str(target) == record['target'] and target.is_dir() and not target.is_symlink(), 'Invalid target directory')
    h = nd.helper()
    timeline = h._decrypt_metadata_in_memory(target / 'draft_info.json')
    metadata = h._decrypt_metadata_in_memory(target / 'draft_meta_info.json')
    require(timeline['id'] == record['timeline_id'] and metadata['draft_id'] == record['draft_id'], 'Draft identity changed')
    native_resource_bindings = verify_structure(timeline, metadata, plan, record['assets'], target,
                                               allow_native_resource_cache=True,
                                               runtime_profile=nd.doctor()['runtime_profile'],
                                               font_assets=font_assets or ())
    project = read_json(target / 'Timelines/project.json')
    require(project['main_timeline_id'] == timeline['id'], 'Project/timeline reference changed')
    mirrors = [target / 'draft_info.json', target / 'template-2.tmp',
               target / 'Timelines' / timeline['id'] / 'draft_info.json',
               target / 'Timelines' / timeline['id'] / 'template-2.tmp']
    require(len({nd.digest(p) for p in mirrors}) == 1 and len({(p.stat().st_dev, p.stat().st_ino) for p in mirrors}) == 4,
            'Four active mirrors must be equal and physically independent')
    for asset in record['assets']:
        require(nd.digest(target / asset['relative']) == asset['sha256'], 'Draft-owned media changed')
    resources.verify_files(record.get('native_resources', []), target, plan)
    font_files = fonts.verify_assets(font_assets, timeline, target, target) if font_assets is not None else 0
    root = read_json(nd.DRAFT_ROOT / 'root_meta_info.json')
    entries = [e for e in root['all_draft_store'] if e.get('draft_id') == record['draft_id']]
    require(len(entries) == 1 and entries[0]['draft_fold_path'] == str(target), 'Home registration missing or ambiguous')
    return {'status': 'verified', 'draft': str(target), 'name': target.name, 'duration_us': timeline.get('duration', 0),
            'native_timeline_schema': timeline['new_version'],
            'tracks': [{'type': t['type'], 'segments': len(t['segments'])} for t in timeline.get('tracks', [])],
            'media_files': len(record['assets']), 'native_resources': len(record.get('native_resources', [])),
            'font_files': font_files,
            'native_resource_bindings': native_resource_bindings,
            'native_cache_dependency': any(b['location'] == 'native-cache' for b in native_resource_bindings),
            'four_mirrors_equal': True, 'source_files_unchanged':
            all(Path(a['source']).is_file() and nd.digest(a['source']) == a['sha256'] for a in record['assets']),
            'native_ui_acceptance': 'requires separate open/play/save/cold-reopen evidence', 'video_exported': False}


def cached_sound(name):
    require(name in CACHED_SOUNDS, 'Unknown cached sound; provide a known local audio file instead')
    expected = CACHED_SOUNDS[name]
    path = Path.home() / 'Movies/JianyingPro/User Data/Cache' / expected['relative']
    asset = probe(path)
    require(asset['sha256'] == expected['sha256'] and asset['size'] == expected['size'] and asset['kind'] == 'audio',
            'Cached sound identity changed; no downloads or resource-registration guesses are permitted')
    return asset


def from_compiled(compiled, name, out, cached_sounds_as_local=False):
    source = read_json(compiled)
    nd.validate_compiled(source)
    require(not source['sfx'] or cached_sounds_as_local,
            'Use --cached-sounds-as-local to explicitly import known cached sounds as local audio copies')
    media = probe(source['source'])
    plan = {'schema': SCHEMA, 'name': name, 'canvas': {'width': media['width'], 'height': media['height'], 'fps': source['fps']},
            'tracks': [{'type': 'video', 'name': '口播', 'segments': [
                {'source': source['source'], 'start_us': r['target_start_us'], 'duration_us': r['target_duration_us'],
                 'source_start_us': r['source_start_us'], 'source_duration_us': r['source_duration_us'],
                 'speed': source['speed'], 'volume': source['voice_volume']} for r in source['ranges']]},
                {'type': 'text', 'name': '字幕', 'segments': [{'start_us': s['start_us'], 'duration_us': s['end_us'] - s['start_us'],
                                                           'text': s['text']} for s in source['subtitles']]}]}
    adjustments = []
    sound_tracks = []
    for cue in sorted(source['sfx'], key=lambda c: c['start_us']):
        asset = cached_sound(cue['name'])
        # Native library display duration can include padding beyond MP3 decoded duration.
        # Preserve the decoded sound, never reference nonexistent source samples.
        span = min(cue['duration_us'], asset['duration_us'])
        spec = {'source': asset['source'], 'start_us': cue['start_us'], 'duration_us': span, 'volume': cue['volume']}
        available = next((t for t in sound_tracks if t['segments'][-1]['start_us'] + t['segments'][-1]['duration_us']
                          <= spec['start_us']), None)
        if available is None:
            available = {'type': 'audio', 'name': '缓存音效 · 本地副本 ' + str(len(sound_tracks) + 1), 'segments': []}
            sound_tracks.append(available)
        available['segments'].append(spec)
        if span != cue['duration_us']:
            adjustments.append({'name': cue['name'], 'start_us': cue['start_us'],
                                'native_display_duration_us': cue['duration_us'], 'decoded_audio_duration_us': span})
    plan['tracks'].extend(sound_tracks)
    validate_plan(plan)
    write(out, plan)
    return {'status': 'converted', 'plan': str(out), 'live_written': False,
            'cached_sounds_as_local': bool(sound_tracks), 'native_library_registration_created': False,
            'sound_display_duration_adjustments': adjustments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    p = sub.add_parser('build')
    p.add_argument('--plan', required=True); p.add_argument('--out', required=True)
    for name in ('publish', 'resume-publish'):
        p = sub.add_parser(name)
        p.add_argument('--build', required=True); p.add_argument('--audit', required=True)
    p = sub.add_parser('create')
    p.add_argument('--plan', required=True); p.add_argument('--work', required=True)
    for name in ('verify-build', 'verify'):
        p = sub.add_parser(name); p.add_argument('--build', required=True)
        p.add_argument('--report')
    p = sub.add_parser('from-compiled')
    p.add_argument('--compiled', required=True); p.add_argument('--name', required=True); p.add_argument('--out', required=True)
    p.add_argument('--cached-sounds-as-local', action='store_true')
    p = sub.add_parser('cached-sound')
    p.add_argument('--name', required=True)
    args = parser.parse_args()
    if args.command == 'doctor':
        blueprint(); result = nd.doctor()
    elif args.command == 'build':
        result = build(args.plan, args.out)
    elif args.command in ('publish', 'resume-publish'):
        result = publish(args.build, args.audit, resume=args.command == 'resume-publish')
    elif args.command == 'create':
        work = nd.fresh_directory(args.work)
        build(args.plan, work / 'build')
        result = publish(work / 'build', work / 'audit')
    elif args.command == 'verify':
        result = verify_live(args.build)
    elif args.command == 'verify-build':
        record = verify_build(args.build)
        result = {'status': 'verified', 'name': record['name'], 'live_written': False}
    elif args.command == 'cached-sound':
        result = dict(cached_sound(args.name), native_library_registration_created=False)
    else:
        result = from_compiled(args.compiled, args.name, args.out, args.cached_sounds_as_local)
    if getattr(args, 'report', None):
        write(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
