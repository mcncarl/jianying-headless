#!/usr/bin/env python3
"""Guarded, private copy editing of existing native 11.4 timelines.

All source files remain untouched. Unknown timeline fields and unused material
nodes are preserved in the copy, rather than reconstructed from a small plan.
"""
import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import time

import jy14_headless as j
import native_compound as compound
import native_fonts as fonts
from runtime_profiles import EDIT_PROFILES, validate_timeline_schema, saved_schema_upgrade

SCHEMA = 'jy14-edit-plan/v1'
BUILD_SCHEMA = 'jy14-edit-build/v1'


def source_directory(value):
    path = Path(value)
    j.require(path.is_absolute() and not path.is_symlink() and path.is_dir(), 'Expected an absolute native draft directory')
    path = path.resolve(strict=True)
    j.require(path.parent == j.nd.DRAFT_ROOT, 'Source must be a direct-child draft in the current native draft root')
    return path


def material_index(timeline):
    result = {}
    for bucket, nodes in timeline.get('materials', {}).items():
        j.require(isinstance(nodes, list), 'Unexpected materials container')
        for node in nodes:
            j.require(node['id'] not in result, 'Ambiguous material IDs')
            result[node['id']] = (bucket, node)
    return result


def segments(timeline):
    return {s['id']: (t, s) for t in timeline.get('tracks', []) for s in t.get('segments', [])}


def basic_validation(timeline):
    validate_timeline_schema(timeline)
    index = material_index(timeline)
    identifiers = set(index)
    end = 0
    for track in timeline.get('tracks', []):
        j.require(track['id'] not in identifiers, 'Duplicate track ID')
        identifiers.add(track['id'])
        for segment in track.get('segments', []):
            j.require(segment['id'] not in identifiers, 'Duplicate segment ID')
            identifiers.add(segment['id'])
            for ref in [segment['material_id']] + segment.get('extra_material_refs', []):
                j.require(ref in index, 'Dangling material reference')
            for group in segment.get('common_keyframes', []):
                for node in [group] + group.get('keyframe_list', []):
                    j.require(node['id'] not in identifiers, 'Duplicate keyframe ID')
                    identifiers.add(node['id'])
            target = segment['target_timerange']
            start = j.integer(target.get('start', 0), 'Native target start')
            span = j.integer(target.get('duration', 0), 'Native target duration')
            end = max(end, start + span)
    return end


def mirrors(folder, timeline_id):
    return [folder / 'draft_info.json', folder / 'template-2.tmp',
            folder / 'Timelines' / timeline_id / 'draft_info.json',
            folder / 'Timelines' / timeline_id / 'template-2.tmp']


def load_source(value):
    runtime = j.nd.doctor()
    j.require(runtime['runtime_profile'] in EDIT_PROFILES,
              'Existing-draft editing is unavailable for this exact runtime profile')
    helper = j.nd.helper()
    helper._ensure_editor_closed(True)
    source = source_directory(value)
    before = j.files_manifest(source)
    timeline = helper._decrypt_metadata_in_memory(source / 'draft_info.json')
    metadata = helper._decrypt_metadata_in_memory(source / 'draft_meta_info.json')
    project = j.read_json(source / 'Timelines/project.json')
    compound.validate(timeline, basic_validation)
    for _, node in compound.graph(timeline):
        validate_timeline_schema(node, runtime['runtime_profile'])
    j.require(metadata['draft_fold_path'] == str(source), 'Source metadata path mismatch')
    j.require(project['main_timeline_id'] == timeline['id'], 'Source main timeline mismatch')
    j.require(len(project['timelines']) == 1 and project['timelines'][0]['id'] == timeline['id'],
              'Multi-timeline copies need a dedicated compatibility profile')
    if timeline.get('materials', {}).get('drafts'):
        j.require(runtime['runtime_profile'] in compound.SUPPORTED_PROFILES, 'Compound copies require a reviewed runtime profile')
        compound.check_sidecars(timeline, source, source, preserved)
    # Never duplicate a cloud identity or alter rights information to make a copy.
    j.require(not metadata.get('cloud_draft_sync') and not metadata.get('draft_is_cloud_temp_draft')
              and not metadata.get('draft_is_pippit_draft'), 'Cloud-linked drafts require an explicit local-copy workflow')
    for key in ('tm_draft_cloud_entry_id', 'tm_draft_cloud_parent_entry_id', 'tm_draft_cloud_space_id'):
        j.require(metadata.get(key, -1) in (-1, 0, '', None), 'A cloud identity cannot be copied by this local editor')
    j.require(len({j.nd.digest(p) for p in mirrors(source, timeline['id'])}) == 1, 'Source mirrors disagree')
    j.require(j.files_manifest(source) == before, 'Source changed while reading')
    return source, before, timeline, metadata, project


def inspect(value, out):
    source, files, timeline, metadata, project = load_source(value)
    index = material_index(timeline)
    result = {'schema': 'jy14-edit-inspection/v1', 'source': {'draft_path': str(source), 'draft_id': metadata['draft_id'],
              'timeline_sha256': files['draft_info.json']['sha256']},
              'timeline_id': timeline['id'], 'duration_us': timeline.get('duration', 0), 'tracks': []}
    for track in timeline.get('tracks', []):
        row = {'id': track['id'], 'type': track['type'], 'name': track.get('name', ''), 'segments': []}
        for segment in track.get('segments', []):
            bucket, material = index[segment['material_id']]
            entry = {k: deepcopy(segment[k]) for k in ('id', 'material_id', 'target_timerange', 'source_timerange') if k in segment}
            entry.update(volume=segment.get('volume', 1), speed=segment.get('speed', 1),
                         material_type=material.get('type'), extra_refs=len(segment.get('extra_material_refs', [])),
                         keyframe_channels=len(segment.get('common_keyframes', [])))
            if bucket == 'texts':
                entry['text'] = json.loads(material['content']).get('text', '')
            row['segments'].append(entry)
        result['tracks'].append(row)
    result['nested_timelines'] = []
    for owner, child in compound.graph(timeline):
        if owner is None:
            continue
        child_index = material_index(child)
        row = {'id': child['id'], 'name': child.get('name'), 'owner_material_id': owner['id'],
               'duration_us': child.get('duration'), 'tracks': []}
        for track in child.get('tracks', []):
            entries = []
            for segment in track.get('segments', []):
                bucket, material = child_index[segment['material_id']]
                entry = {key: deepcopy(segment[key]) for key in
                         ('id', 'material_id', 'target_timerange', 'source_timerange') if key in segment}
                entry.update(volume=segment.get('volume', 1), speed=segment.get('speed', 1),
                             material_type=material.get('type'))
                if bucket == 'texts':
                    entry['text'] = json.loads(material['content']).get('text', '')
                entries.append(entry)
            row['tracks'].append({'id': track['id'], 'type': track['type'], 'name': track.get('name', ''), 'segments': entries})
        result['nested_timelines'].append(row)
    j.write(out, result)
    return {'status': 'inspected', 'inspection': str(out), 'tracks': len(result['tracks']), 'source_written': False}


def rebase(value, source, target):
    if isinstance(value, dict):
        return {key: rebase(item, source, target) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase(item, source, target) for item in value]
    if isinstance(value, str):
        if value == str(source):
            return str(target)
        if value.startswith(str(source) + '/'):
            return str(target) + value[len(str(source)):]
    return value


def set_segment(timeline, segment, kind, values):
    allowed = {'volume', 'speed', 'start_us', 'duration_us', 'source_start_us', 'source_duration_us', 'visible'}
    if kind in {'video', 'text'}:
        allowed |= {'x', 'y', 'scale', 'rotation', 'opacity'}
    if kind == 'text':
        allowed -= {'volume', 'speed', 'source_start_us', 'source_duration_us'}
    j.keys(values, allowed, 'Segment edit')
    j.require(values, 'Segment edit is empty')
    animated = {g['property_type'] for g in segment.get('common_keyframes', [])}
    for field in values:
        if field in j.motion.KEYFRAMES:
            j.require(j.motion.KEYFRAMES[field][0] not in animated, 'Edit the keyframe curve, not its animated base value')
    j.require(not animated or not set(values).intersection({'speed', 'duration_us', 'source_start_us', 'source_duration_us'}),
              'Animated source timing needs explicit keyframe retiming')
    for field, value in values.items():
        if field == 'visible':
            j.require(type(value) is bool, 'visible must be boolean')
            segment[field] = value
        elif field in {'volume', 'speed'}:
            j.number(value, field, 0 if field == 'volume' else .1, 4 if field == 'volume' else 8)
            segment[field] = value
            if field == 'speed':
                index = material_index(timeline)
                refs = [r for r in segment.get('extra_material_refs', []) if index[r][0] == 'speeds']
                j.require(len(refs) == 1, 'Native speed material is missing or ambiguous')
                old = refs[0]
                users = sum(old in s.get('extra_material_refs', []) for _, s in segments(timeline).values())
                if users > 1:
                    copied = deepcopy(index[old][1])
                    copied.update(id=j.identifier(), speed=value)
                    timeline['materials']['speeds'].append(copied)
                    segment['extra_material_refs'] = [copied['id'] if r == old else r for r in segment['extra_material_refs']]
                else:
                    index[old][1]['speed'] = value
        elif field in {'start_us', 'duration_us', 'source_start_us', 'source_duration_us'}:
            is_source = field.startswith('source_')
            is_span = 'duration' in field
            j.integer(value, field, 1 if is_span else 0)
            segment.setdefault('source_timerange' if is_source else 'target_timerange', {})['duration' if is_span else 'start'] = value
        else:
            j.number(value, field, .01 if field == 'scale' else 0 if field == 'opacity' else -360 if field == 'rotation' else -5,
                     10 if field == 'scale' else 1 if field == 'opacity' else 360 if field == 'rotation' else 5)
            clip = segment.setdefault('clip', {})
            if field in {'x', 'y'}:
                clip.setdefault('transform', {})[field] = value
            elif field == 'scale':
                clip['scale'] = {'x': value, 'y': value}
            else:
                clip['alpha' if field == 'opacity' else field] = value
    if kind in {'video', 'audio'} and set(values).intersection({'speed', 'duration_us', 'source_duration_us'}):
        source_range = segment.get('source_timerange', {})
        j.require(abs(source_range.get('duration', 0) / segment.get('speed', 1)
                      - segment['target_timerange'].get('duration', 0)) <= 2, 'Edited speed and durations disagree')


def replace_text(material, text):
    j.require(isinstance(text, str) and text.strip(), 'Replacement text must be nonempty')
    content = json.loads(material['content'])
    old_length = len(content['text'].encode('utf-16-le')) // 2
    new_length = len(text.encode('utf-16-le')) // 2
    j.require(content.get('styles') and all(style.get('range') == [0, old_length] for style in content['styles']),
              'Partial/mixed text style ranges need an explicit style-range mapping')
    content['text'] = text
    for style in content['styles']:
        style['range'] = [0, new_length]
    material['content'] = json.dumps(content, ensure_ascii=False, separators=(',', ':'))


def overlaps(timeline):
    found = set()
    for track in timeline.get('tracks', []):
        ordered = sorted(track.get('segments', []), key=lambda s: s['target_timerange'].get('start', 0))
        for i, left in enumerate(ordered):
            left_end = left['target_timerange'].get('start', 0) + left['target_timerange']['duration']
            for right in ordered[i + 1:]:
                if right['target_timerange'].get('start', 0) >= left_end:
                    break
                found.add(tuple(sorted((left['id'], right['id']))))
    return found


def apply_local_operations(timeline, metadata, operations, target, font_sources=None):
    j.require(isinstance(operations, list) and operations, 'Edit operations are required')
    font_sources = {} if font_sources is None else font_sources
    old_overlaps = overlaps(timeline)
    copied_media, applied = [], []
    for operation in operations:
        j.keys(operation, {'op', 'id', 'set', 'text', 'source', 'name', 'start_us', 'track_id'}, 'Edit operation')
        op = operation.get('op')
        index = material_index(timeline)
        segs = segments(timeline)
        identifier = operation.get('id')
        event = deepcopy(operation)
        if op == 'set_segment':
            j.require(set(operation) == {'op', 'id', 'set'} and identifier in segs, 'Invalid segment edit target/fields')
            track, segment = segs[identifier]
            j.require(track['type'] in {'video', 'audio', 'text'}, 'Editing this track type requires a dedicated adapter')
            set_segment(timeline, segment, track['type'], operation['set'])
        elif op == 'replace_text':
            j.require(set(operation) == {'op', 'id', 'text'} and identifier in index and index[identifier][0] == 'texts',
                      'Replacement text must target an existing text material ID')
            replace_text(index[identifier][1], operation['text'])
        elif op == 'set_text_font':
            j.require(set(operation) == {'op', 'id', 'source'} and identifier in index and index[identifier][0] == 'texts',
                      'Font replacement must target an existing text material ID')
            event['font_asset'] = fonts.set_text_font(index[identifier][1], operation['source'], target, font_sources)
        elif op == 'rename_track':
            j.require(set(operation) == {'op', 'id', 'name'}, 'Invalid track rename fields')
            tracks = [t for t in timeline['tracks'] if t['id'] == identifier]
            j.require(len(tracks) == 1 and isinstance(operation['name'], str) and operation['name'].strip(), 'Invalid track rename')
            tracks[0].update(name=operation['name'], is_default_name=False)
        elif op == 'duplicate_segment':
            j.require(set(operation) <= {'op', 'id', 'start_us', 'track_id'} and identifier in segs, 'Invalid duplicate target')
            source_track, original = segs[identifier]
            j.require(not any(index[r][0] in {'transitions', 'drafts'} for r in original.get('extra_material_refs', [])),
                      'Duplicating linked transitions/compound segments needs a dedicated boundary adapter')
            target_track = next((t for t in timeline['tracks'] if t['id'] == operation.get('track_id', source_track['id'])), None)
            j.require(target_track and target_track['type'] == source_track['type'], 'Duplicate track type mismatch')
            refs = [original['material_id']] + original.get('extra_material_refs', [])
            ids = {r: j.identifier() for r in refs}
            ids[original['id']] = j.identifier()
            for group in original.get('common_keyframes', []):
                for node in [group] + group['keyframe_list']:
                    ids[node['id']] = j.identifier()
            copied = j.remap(deepcopy(original), ids)
            copied['target_timerange']['start'] = j.integer(operation.get('start_us'), 'Duplicate start')
            for ref in dict.fromkeys(refs):
                bucket, material = index[ref]
                timeline['materials'][bucket].append(j.remap(deepcopy(material), ids))
            target_track['segments'].append(copied)
            target_track['segments'].sort(key=lambda s: s['target_timerange'].get('start', 0))
            event['created_segment_id'] = copied['id']
        elif op == 'remove_segment':
            j.require(set(operation) == {'op', 'id'} and identifier in segs, 'Invalid remove target')
            track, segment = segs[identifier]
            j.require(not any(index[r][0] in {'transitions', 'drafts'} for r in segment.get('extra_material_refs', [])),
                      'Removing linked transitions/compound segments needs a dedicated boundary adapter')
            track['segments'].remove(segment)
        elif op == 'replace_media':
            j.require(set(operation) == {'op', 'id', 'source'} and identifier in index, 'Invalid media replacement target')
            bucket, material = index[identifier]
            j.require(bucket in {'videos', 'audios'}, 'Only existing local video/photo/GIF/audio materials can be replaced')
            j.require(material.get('path') and material.get('local_material_id'), 'Replacement requires an existing local material')
            asset = j.probe(operation['source'])
            expected_type = 'extract_music' if asset['kind'] == 'audio' else asset['media_type']
            j.require(material.get('type') == expected_type, 'Replacement media type must match the original')
            asset.update(relative='Resources/headless-edited-media/' + asset['sha256'] + Path(asset['source']).suffix.lower(),
                         local_id=j.identifier())
            material.update(path=str(target / asset['relative']), local_material_id=asset['local_id'],
                            duration=j.STILL_CAPACITY_US if asset['media_type'] in {'photo', 'gif'} else asset['duration_us'])
            if bucket == 'videos':
                material.update(width=asset['width'], height=asset['height'], has_audio=asset['has_audio'],
                                material_name=Path(asset['source']).name)
            else:
                material.update(name=Path(asset['source']).name, music_id=asset['local_id'], resource_id=asset['local_id'])
            group = next(g for g in metadata['draft_materials'] if g['type'] == 0)
            group['value'].append(j.library_record(asset, target, time.time_ns() // 1000))
            copied_media.append(asset)
        elif op == 'set_filter_intensity':
            j.require(set(operation) == {'op', 'id', 'set'} and isinstance(operation['set'], dict)
                      and operation['set'].keys() == {'value'} and identifier in index,
                      'Invalid filter intensity operation')
            bucket, material = index[identifier]
            j.require(bucket == 'effects' and material.get('type') == 'filter' and 'value' in material,
                      'Filter edit must target an already-registered native filter')
            material['value'] = j.number(operation['set']['value'], 'Filter intensity', 0, 1)
        else:
            raise ValueError('Unsupported edit operation: ' + str(op))
        applied.append(event)
    j.require(not overlaps(timeline) - old_overlaps, 'Edit introduces overlapping segments on the same track')
    timeline['duration'] = basic_validation(timeline)
    metadata['tm_duration'] = timeline['duration']
    return copied_media, applied


def apply_operations(timeline, metadata, operations, target):
    j.require(isinstance(operations, list) and operations, 'Edit operations are required')
    assets, applied = [], []
    font_sources = {}
    for operation in operations:
        j.require(isinstance(operation, dict), 'Edit operation must be an object')
        local = deepcopy(operation)
        timeline_id = local.pop('timeline_id', None)
        selected = compound.select(timeline, timeline_id)
        old_duration = selected.get('duration', 0)
        if local.get('op') == 'create_compound':
            j.require(set(local) == {'op', 'name'}, 'Invalid compound creation fields')
            j.require(j.nd.doctor()['runtime_profile'] in compound.SUPPORTED_PROFILES, 'Compound creation requires a reviewed runtime profile')
            event = compound.wrap_all(selected, local['name'], target)
            events, created = [event], []
        else:
            created, events = apply_local_operations(selected, metadata, [local], target, font_sources)
        if selected is not timeline:
            j.require(selected['duration'] == old_duration,
                      'Nested edits cannot change duration without an explicit parent-range policy')
        for event in events:
            if timeline_id is not None:
                event['timeline_id'] = timeline_id
        assets.extend(created)
        applied.extend(events)
    compound.validate(timeline, basic_validation)
    metadata['tm_duration'] = timeline['duration']
    return assets, applied


def write_owned(path, payload):
    """Replace only a file in the newly created, task-owned build directory."""
    j.require(not path.is_symlink(), 'Symlink in copied draft')
    data = payload if isinstance(payload, bytes) else j.nd.packed(payload)
    with path.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def validate_media_interval(segment, info, original, replacement=False, duplicate_of=None):
    source_range = segment.get('source_timerange', {})
    source_end = source_range.get('start', 0) + source_range.get('duration', 0)
    padding = max(0, source_end - info['duration_us'])
    if padding <= 1:
        return 0
    # Native save may round an MP3 source endpoint to the timeline frame grid.
    # Retain only an identical, already-native interval; never extend a new edit
    # or replacement beyond the actual decoded source duration.
    known_interval = not replacement and any(
        (old['material_id'] == segment['material_id'] or old['id'] == duplicate_of)
        and old.get('source_timerange') == source_range
        for _, old in segments(original).values())
    frame = math.ceil(1_000_000 / original.get('fps', 30))
    j.require(known_interval and padding <= frame, 'Edited trim exceeds source media')
    return padding


def build(plan_path, out):
    j.require(j.nd.doctor()['runtime_profile'] in EDIT_PROFILES,
              'Existing-draft editing is unavailable for this exact runtime profile')
    plan = j.read_json(plan_path)
    j.keys(plan, {'schema', 'source', 'name', 'operations'}, 'Edit plan')
    j.require(plan.get('schema') == SCHEMA, 'Unsupported edit plan schema')
    j.keys(plan.get('source'), {'draft_path', 'draft_id', 'timeline_sha256'}, 'Source preconditions')
    source, source_files, original, original_meta, project = load_source(plan['source']['draft_path'])
    j.require(plan['source'].get('draft_id') == original_meta['draft_id'] and
              plan['source'].get('timeline_sha256') == source_files['draft_info.json']['sha256'], 'Stale source preconditions')
    j.validate_plan({'schema': j.SCHEMA, 'name': plan['name'], 'canvas': {'width': 16, 'height': 16, 'fps': 30}, 'tracks': []})
    target = j.nd.DRAFT_ROOT / plan['name']
    j.require(not target.exists(), 'Copy target already exists')
    timeline, metadata = rebase(deepcopy(original), source, target), rebase(deepcopy(original_meta), source, target)
    # Keep copied library references local and explicit. This does not resolve
    # the observed native 11.4.2 project-ID loss during subsequent UI save.
    for owner, child in compound.graph(timeline):
        if owner is not None:
            for key, path in compound.paths(owner, target).items():
                owner[key] = str(path)
    fonts.rebase_existing(timeline, source, target)
    assets, applied = apply_operations(timeline, metadata, plan['operations'], target)
    font_assets = fonts.collect_edit_assets(timeline, source, target,
                                           (o['font_asset'] for o in applied if 'font_asset' in o))
    # All referenced local clips must still exist and contain the requested source interval.
    probes = {}
    media_dependencies = []
    preserved_native_padding = []
    for segment_timeline, track, segment in compound.all_segments(timeline):
        index = material_index(segment_timeline)
        bucket, material = index[segment['material_id']]
        if bucket not in {'videos', 'audios'} or not material.get('path'):
            continue
        replacement = next((a for a in assets if a['local_id'] == material.get('local_material_id')), None)
        path = Path(replacement['source']) if replacement else j.native_media_path(material['path'], target)
        if not replacement and target in path.parents:
            path = source / path.relative_to(target)
        if path not in probes:
            probes[path] = j.probe(path)
        info = probes[path]
        native_path = j.native_media_path(material['path'], target)
        dependency = {'path': str(native_path), 'sha256': info['sha256'], 'size': info['size']}
        if dependency not in media_dependencies:
            media_dependencies.append(dependency)
        duplicate_of = next((o['id'] for o in applied if o['op'] == 'duplicate_segment'
                             and o['created_segment_id'] == segment['id']), None)
        old_timeline = next((old for _, old in compound.graph(original)
                             if any(s['id'] in {segment['id'], duplicate_of} for _, s in segments(old).values())), original)
        padding = validate_media_interval(segment, info, old_timeline, bool(replacement), duplicate_of)
        if padding:
            preserved_native_padding.append({'segment_id': segment['id'], 'padding_us': padding,
                                             'reason': 'preserved-existing-native-frame-rounding'})
    out = j.nd.fresh_directory(out)
    folder = out / 'draft'
    shutil.copytree(source, folder, copy_function=shutil.copy2)
    j.require(j.files_manifest(folder) == source_files and j.files_manifest(source) == source_files, 'Source/copy changed during snapshot')
    now = time.time_ns() // 1000
    metadata.update(draft_id=j.identifier(), draft_name=target.name, draft_fold_path=str(target),
                    tm_draft_create=now, tm_draft_modified=now)
    project.update(id=j.identifier(), create_time=now, update_time=now)
    for asset in assets + font_assets:
        destination = folder / asset['relative']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with Path(asset['source']).open('rb') as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst)
        j.require(j.nd.digest(destination) == asset['sha256'] == j.nd.digest(asset['source']), 'Replacement bytes changed')
    compound_sidecars = compound.write_sidecars(timeline, target, folder, source, write_owned, rebase)
    helper = j.nd.helper()
    cipher_path = out / 'edited-timeline.cipher'
    helper._encrypt_metadata_from_memory(j.nd.packed(timeline), cipher_path)
    cipher = cipher_path.read_bytes()
    for path in mirrors(folder, timeline['id']) + [folder / 'draft_info.json.bak',
                                                  folder / 'Timelines' / timeline['id'] / 'draft_info.json.bak']:
        write_owned(path, cipher)
    metadata['draft_timeline_materials_size_'] = sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
    meta_cipher = out / 'edited-metadata.cipher'
    helper._encrypt_metadata_from_memory(j.nd.packed(metadata), meta_cipher)
    write_owned(folder / 'draft_meta_info.json', meta_cipher.read_bytes())
    for name in ('project.json', 'project.json.bak'):
        write_owned(folder / 'Timelines' / name, project)
    for name in ('key_value.json', 'draft_virtual_store.json', 'timeline_layout.json'):
        path = folder / name
        if path.is_file():
            write_owned(path, rebase(j.read_json(path), source, target))
    kv_path = folder / 'key_value.json'
    kv = j.read_json(kv_path)
    for operation in applied:
        if operation['op'] == 'duplicate_segment' and operation['id'] in kv:
            entry = deepcopy(kv[operation['id']])
            if isinstance(entry, dict) and 'segmentId' in entry:
                entry['segmentId'] = operation['created_segment_id']
            kv[operation['created_segment_id']] = entry
        elif operation['op'] == 'remove_segment':
            kv.pop(operation['id'], None)
    write_owned(kv_path, kv)
    if assets:
        store_path = folder / 'draft_virtual_store.json'
        store = j.read_json(store_path)
        children = next(g['value'] for g in store['draft_virtual_store'] if g['type'] == 1)
        children.extend({'child_id': a['local_id'], 'parent_id': ''} for a in assets)
        write_owned(store_path, store)
        kv_path = folder / 'key_value.json'
        kv = j.read_json(kv_path)
        for segment_timeline, track, segment in compound.all_segments(timeline):
            material = material_index(segment_timeline)[segment['material_id']][1]
            asset = next((a for a in assets if a['local_id'] == material.get('local_material_id')), None)
            if asset:
                entry = deepcopy(j.blueprint()['local_registration'])
                entry.update(segmentId=segment['id'], materialId=asset['sha256'][:32],
                             materialName=Path(asset['source']).name, rank='1')
                kv[segment['id']] = entry
        write_owned(kv_path, kv)
    resources = {str(path.relative_to(folder)): j.nd.digest(path) for path in (folder / 'Resources').rglob('*') if path.is_file()}
    j.write(out / 'plan.json', plan)
    j.write(out / 'source-timeline.json', original)
    j.write(out / 'expected-timeline.json', timeline)
    record = {'schema': BUILD_SCHEMA, 'runtime_manifest': j.nd.MANIFEST_SHA, 'runtime_profile': j.nd.doctor()['runtime_profile'],
              'name': target.name, 'target': str(target), 'draft_id': metadata['draft_id'], 'timeline_id': timeline['id'],
              'project_id': project['id'], 'duration_us': timeline['duration'], 'source': str(source),
              'source_files': source_files, 'files': j.files_manifest(folder), 'resources': resources,
              'plan_sha256': j.nd.digest(out / 'plan.json'), 'expected_timeline_sha256': j.nd.digest(out / 'expected-timeline.json'),
              'operations': applied, 'new_assets': assets, 'font_assets': font_assets,
              'unknown_fields_preserved_before_native_save': True,
              'media_dependencies': media_dependencies,
              'compound_sidecars': compound_sidecars,
              'preserved_native_padding': preserved_native_padding,
              'internal_ids_policy': 'preserved within a new project identity', 'private_copy_not_a_distributable_package': True}
    j.write(out / 'build.json', record)
    verify_build(out)
    return {'status': 'edited-copy-built', 'build': str(out), 'operations': len(applied),
            'duration_us': timeline['duration'], 'source_written': False, 'live_written': False}


def verify_build(out):
    j.require(j.nd.doctor()['runtime_profile'] in EDIT_PROFILES,
              'Existing-draft editing is unavailable for this exact runtime profile')
    out = Path(out).resolve(strict=True)
    record = j.read_json(out / 'build.json')
    j.require(record['schema'] == BUILD_SCHEMA and record['runtime_manifest'] == j.nd.MANIFEST_SHA, 'Edit build provenance differs')
    j.require(j.files_manifest(out / 'draft') == record['files'], 'Edited copy changed after build')
    j.require(j.nd.digest(out / 'plan.json') == record['plan_sha256'] and
              j.nd.digest(out / 'expected-timeline.json') == record['expected_timeline_sha256'], 'Edit plan/evidence changed')
    plan = j.read_json(out / 'plan.json')
    j.require(record['target'] == str(j.nd.DRAFT_ROOT / plan['name']), 'Edit copy target mismatch')
    expected = j.read_json(out / 'expected-timeline.json')
    helper = j.nd.helper()
    j.require(helper._decrypt_metadata_in_memory(out / 'draft/draft_info.json') == expected, 'Edited timeline differs from expected full structure')
    compound.validate(expected, basic_validation)
    compound.check_sidecars(expected, Path(record['target']), out / 'draft', preserved)
    font_assets = fonts.recorded_assets(record, plan)
    if font_assets is not None:
        fonts.verify_assets(font_assets, expected, Path(record['target']), out / 'draft')
    j.require(j.files_manifest(Path(record['source'])) == record['source_files'], 'Source changed since the edit snapshot')
    for item in record['media_dependencies']:
        path = Path(item['path'])
        target = Path(record['target'])
        if target in path.parents:
            path = out / 'draft' / path.relative_to(target)
        j.require(path.is_file() and j.nd.digest(path) == item['sha256'], 'Edited-copy media dependency changed')
    return record


def preserved(expected, actual, path='', frame_tolerance=0, quantized=None):
    """Conservative readback: tolerate omitted empty defaults, never silently discard nonempty fields."""
    if isinstance(expected, dict):
        j.require(isinstance(actual, dict), 'Structure changed: ' + path)
        for key, value in expected.items():
            if key in {'update_time', 'create_time'}:
                continue
            if key == 'has_audio' and re.search(r'/materials/videos/[^/]+$', path):
                j.require(actual.get(key, True) == value, 'Preserved audio capability changed: ' + path)
                continue
            # Native saves may omit only this material's default unit speed.
            # Never generalize this to segment speeds, curves or non-unit values.
            if (key == 'speed' and key not in actual and type(value) in (int, float) and value == 1
                    and re.search(r'/materials/speeds/[^/]+$', path)
                    and expected.get('type') == actual.get('type') == 'speed'
                    and expected.get('mode', 0) == actual.get('mode', 0) == 0
                    and expected.get('curve_speed') in (None, '')
                    and actual.get('curve_speed') in (None, '')):
                continue
            if key not in actual and value in (None, False, 0, '', [], {}):
                continue
            j.require(key in actual, 'Preserved field disappeared: ' + path + '/' + key)
            preserved(value, actual[key], path + '/' + key, frame_tolerance, quantized)
    elif isinstance(expected, list):
        j.require(isinstance(actual, list), 'List changed: ' + path)
        if expected and all(isinstance(v, dict) and 'id' in v for v in expected):
            by_id = {v['id']: v for v in actual if isinstance(v, dict) and 'id' in v}
            for item in expected:
                j.require(item['id'] in by_id, 'Preserved node disappeared: ' + path)
                preserved(item, by_id[item['id']], path + '/' + item['id'], frame_tolerance, quantized)
        else:
            j.require(expected == actual, 'Preserved list changed: ' + path)
    elif type(expected) in (int, float) and type(actual) in (int, float):
        delta = abs(expected - actual)
        timeline_time = re.search(r'/(?:source_timerange|target_timerange)/(?:start|duration)$', path)
        if delta > 1e-5 and timeline_time and delta <= frame_tolerance:
            if quantized is not None:
                quantized.append({'path': path, 'expected_us': expected, 'actual_us': actual})
        else:
            j.require(delta <= 1e-5, 'Preserved numeric value changed: ' + path)
    else:
        j.require(expected == actual, 'Preserved value changed: ' + path)


def normalize_saved_companions(expected, actual, runtime):
    # Observed on the 11.5.0 IG photo tracks after copying a saved 187 timeline.
    # The existing helper admits only uniquely referenced, empty photo-audio
    # companions with a bijective ID mapping. It does not rewrite live evidence.
    reviewed_root = (runtime == 'jy14-headless-macos-11.5.0'
                     and expected.get('new_version') == actual.get('new_version') == '187.0.0'
                     and actual.get('last_modified_platform', {}).get('app_version') == '11.5.0')
    return compound.normalize_companion_ids(expected, actual, nested_root=reviewed_root)


def normalize_saved_provenance(before, after, runtime):
    old = before.get('last_modified_platform', {})
    new = after.get('last_modified_platform', {})
    if not (runtime == 'jy14-headless-macos-11.5.0'
            and before.get('new_version') == after.get('new_version') == '187.0.0'
            and old.get('app_version') == new.get('app_version') == '11.5.0'):
        return []
    changed = []
    for key in ('device_id', 'hard_disk_id', 'mac_address'):
        if key in old and old[key] != new.get(key):
            value = new.get(key)
            j.require(isinstance(value, str) and 0 < len(value) <= 256,
                      'Invalid native save provenance field: ' + key)
            new[key] = old[key]
            changed.append(key)
    return changed


def compare_saved_timeline(expected, actual, runtime_profile, *, frame_tolerance=0, quantized=None):
    """Compare native save output with explicit, validated timeline context.

    Only 11.5 interprets absent fps/speed as native defaults. Walk real compound
    timelines in comparison copies; similarly named plugin data stays strict.
    Normalize both sides so an omitted expected default cannot hide a new value.
    Check known speed contexts in both directions; new modes/curves are not
    harmless native metadata additions.
    Other callers of preserved() retain the existing upstream rules.
    """
    j.require(runtime_profile is not None, 'Saved timeline comparison requires an explicit runtime profile')

    def comparison_copy(value):
        value = deepcopy(value)
        rows = compound.validate(value, basic_validation)
        for _, timeline in rows:
            validate_timeline_schema(timeline, runtime_profile)
        if runtime_profile != 'jy14-headless-macos-11.5.0':
            return value, {}

        speed_contexts = {}

        def default(node, key, number):
            # setdefault alone is insufficient: bool == 1 in Python, and the
            # generic comparator intentionally retains its existing semantics.
            if key not in node:
                node[key] = number
            else:
                j.require(type(node[key]) in (int, float) and math.isfinite(node[key]) and node[key] > 0,
                          'Invalid native saved ' + key)

        def supported_speed_context(material, kind):
            mode, curve = material.get('mode', 0), material.get('curve_speed')
            if type(mode) is not int:
                return False
            if mode == 0 and curve in (None, ''):
                return kind in ('video', 'audio')
            if not (kind == 'video' and mode == 1 and isinstance(curve, dict)
                    and curve.get('id') == '6768730851543880206'):
                return False
            points = curve.get('speed_points')
            if not isinstance(points, list) or len(points) < 2:
                return False
            if not all(isinstance(p, dict) and all(type(p.get(k)) in (int, float)
                       and math.isfinite(p[k]) for k in ('x', 'y'))
                       and 0 <= p['x'] <= 1 and p['y'] > 0 for p in points):
                return False
            return (points[0]['x'] == 0 and points[-1]['x'] == 1
                    and all(a['x'] < b['x'] for a, b in zip(points, points[1:])))

        def speed_default(node, supported):
            # Unsupported contexts may retain explicit speeds, but missing
            # values must not acquire an unverified meaning in either direction.
            j.require(supported or 'speed' in node,
                      'Missing speed in an unverified native speed context')
            default(node, 'speed', 1)

        for _, timeline in rows:
            default(timeline, 'fps', 30)
            index = material_index(timeline)
            owners = {}
            for track in timeline.get('tracks', []):
                bucket = {'video': 'videos', 'audio': 'audios'}.get(track.get('type'))
                for segment in track.get('segments', []):
                    refs = [r for r in segment.get('extra_material_refs', [])
                            if index[r][0] == 'speeds']
                    supported = (bucket is not None and index[segment['material_id']][0] == bucket
                                 and len(refs) == 1
                                 and supported_speed_context(index[refs[0]][1], track['type']))
                    for ref in refs:
                        owners.setdefault(ref, []).append(supported)
                    if bucket is not None and index[segment['material_id']][0] == bucket:
                        speed_default(segment, supported)
            for node in timeline.get('materials', {}).get('speeds', []):
                if node.get('type') == 'speed':
                    contexts = owners.get(node['id'], [])
                    speed_default(node, bool(contexts) and all(contexts))
                    mode = node.setdefault('mode', 0)
                    curve = node.setdefault('curve_speed', None)
                    if type(mode) is int and mode == 0 and curve in (None, ''):
                        node['curve_speed'] = None
                    speed_contexts[timeline['id'], node['id']] = (
                        type(mode), mode, node['curve_speed'])
        return value, speed_contexts

    before, old_contexts = comparison_copy(expected)
    after, new_contexts = comparison_copy(actual)
    for (timeline_id, material_id), context in old_contexts.items():
        j.require(new_contexts.get((timeline_id, material_id)) == context,
                  'Native saved speed context changed: ' + timeline_id + '/materials/speeds/' + material_id)
    preserved(before, after, frame_tolerance=frame_tolerance, quantized=quantized)


def normalize_saved_curve_averages(expected, actual, runtime):
    """Recognize the observed 11.5 custom-curve frame rounding on a copy.

    Both headless and native-UI copies recalculate the linked segment/material
    average to source duration / frame-aligned target duration on first save.
    Curve points and both ranges remain identical, allowing an omitted integer
    zero start. This is separate from speed default filling and does not relax
    the generic preservation comparator.
    """
    compared, changes = deepcopy(actual), []
    if not (runtime == 'jy14-headless-macos-11.5.0'
            and expected.get('new_version') == actual.get('new_version') == '187.0.0'
            and expected.get('last_modified_platform', {}).get('app_version') == '11.5.0'
            and actual.get('last_modified_platform', {}).get('app_version') == '11.5.0'
            and expected.get('fps', 30) == actual.get('fps', 30) == 30
            and not expected.get('materials', {}).get('drafts')
            and not actual.get('materials', {}).get('drafts')):
        return compared, changes

    def positive(value):
        return type(value) in (int, float) and math.isfinite(value) and value > 0

    def same_range(before, after):
        # Only the existing integer-zero start omission is equivalent here.
        # Keep durations, nonzero starts and every other field exact; do not
        # apply the general comparator's frame tolerance to this exception.
        for value in (before, after):
            if not (isinstance(value, dict)
                    and type(value.get('start', 0)) is int and value.get('start', 0) >= 0
                    and type(value.get('duration')) is int and value['duration'] > 0):
                return False
        return dict(before, start=before.get('start', 0)) == dict(after, start=after.get('start', 0))

    old_index, new_index = material_index(expected), material_index(compared)
    old_segments, new_segments = segments(expected), segments(compared)
    frame_us = 1_000_000 / 30
    for sid, (track, old) in old_segments.items():
        new_track, new = new_segments.get(sid, ({}, {}))
        if not (track.get('type') == new_track.get('type') == 'video'
                and track.get('id') == new_track.get('id')
                and old.get('material_id') == new.get('material_id')
                and old_index.get(old.get('material_id'), ('', {}))[0] == 'videos'
                and new_index.get(new.get('material_id'), ('', {}))[0] == 'videos'
                and old.get('extra_material_refs') == new.get('extra_material_refs')):
            continue
        refs = [r for r in old.get('extra_material_refs', [])
                if old_index.get(r, ('', {}))[0] == 'speeds']
        if len(refs) != 1:
            continue
        ref = refs[0]
        before = old_index[ref][1]
        bucket, after = new_index.get(ref, ('', {}))
        if not (bucket == 'speeds' and before.get('type') == after.get('type') == 'speed'
                and type(before.get('mode')) is int and before['mode'] == 1
                and type(after.get('mode')) is int and after['mode'] == 1
                and {k: v for k, v in before.items() if k != 'speed'}
                    == {k: v for k, v in after.items() if k != 'speed'}
                and all(sum(ref in s.get('extra_material_refs', []) for _, s in rows.values()) == 1
                        for rows in (old_segments, new_segments))):
            continue
        curve = before.get('curve_speed')
        if not isinstance(curve, dict) or curve.get('id') != '6768730851543880206':
            continue
        def valid_points(points):
            return (isinstance(points, list) and len(points) >= 3
                    and all(isinstance(p, dict) and type(p.get('x')) in (int, float)
                            and math.isfinite(p['x']) and 0 <= p['x'] <= 1 and positive(p.get('y'))
                            for p in points)
                    and points[0]['x'] == 0 and points[-1]['x'] == 1
                    and all(a['x'] < b['x'] for a, b in zip(points, points[1:]))
                    and len({p['y'] for p in points}) > 1)

        if not all(valid_points(c.get('speed_points')) for c in (curve, after['curve_speed'])):
            continue
        old_speed, new_speed = before.get('speed'), after.get('speed')
        if not (positive(old_speed) and positive(new_speed)
                and old_speed != 1 and new_speed != 1
                and type(old.get('speed')) in (int, float) and old['speed'] == old_speed
                and type(new.get('speed')) in (int, float) and new['speed'] == new_speed
                and abs(old_speed - new_speed) > 1e-5
                and same_range(old.get('source_timerange'), new.get('source_timerange'))
                and same_range(old.get('target_timerange'), new.get('target_timerange'))):
            continue
        source_us = old.get('source_timerange', {}).get('duration')
        target_us = old.get('target_timerange', {}).get('duration')
        if not (type(source_us) is int and source_us > 0
                and type(target_us) is int and target_us > 0
                and abs(target_us - round(target_us / frame_us) * frame_us) <= 1
                and math.isclose(new_speed, source_us / target_us, rel_tol=1e-12, abs_tol=1e-12)):
            continue
        rounding_us = source_us / old_speed - target_us
        if not 0 < rounding_us < frame_us:
            continue
        new['speed'] = after['speed'] = old_speed
        changes.append({'timeline_id': expected['id'], 'segment_id': sid, 'speed_material_id': ref,
                        'expected_speed': old_speed, 'native_speed': new_speed,
                        'source_duration_us': source_us, 'target_duration_us': target_us,
                        'frame_rounding_us': rounding_us})
    return compared, changes


def verify_live(out):
    j.require(j.nd.doctor()['runtime_profile'] in EDIT_PROFILES,
              'Existing-draft editing is unavailable for this exact runtime profile')
    out = Path(out).resolve(strict=True)
    record = j.read_json(out / 'build.json')
    j.require(record.get('schema') == BUILD_SCHEMA and record.get('runtime_manifest') == j.nd.MANIFEST_SHA, 'Unknown edit build')
    j.require(j.nd.digest(out / 'plan.json') == record['plan_sha256'] and
              j.nd.digest(out / 'expected-timeline.json') == record['expected_timeline_sha256'], 'Edit evidence changed')
    plan = j.read_json(out / 'plan.json')
    font_assets = fonts.recorded_assets(record, plan)
    target = j.nd.DRAFT_ROOT / plan['name']
    j.require(str(target) == record['target'] and target.is_dir() and not target.is_symlink(), 'Invalid edit copy path')
    helper = j.nd.helper()
    actual = helper._decrypt_metadata_in_memory(target / 'draft_info.json')
    metadata = helper._decrypt_metadata_in_memory(target / 'draft_meta_info.json')
    j.require(metadata['draft_id'] == record['draft_id'] and metadata['draft_fold_path'] == str(target), 'Edit copy identity changed')
    expected = j.read_json(out / 'expected-timeline.json')
    compound.validate(actual, basic_validation)
    runtime = j.nd.doctor()['runtime_profile']
    compared, companion_identity_changes = normalize_saved_companions(expected, actual, runtime)
    schema_upgrades = []
    provenance_restamps = []
    compared_nodes = {node['id']: node for _, node in compound.graph(compared)}
    for _, before in compound.graph(expected):
        after = compared_nodes[before['id']]
        restamped = normalize_saved_provenance(before, after, runtime)
        if restamped:
            provenance_restamps.append({'timeline_id': before['id'], 'field_names': restamped})
        change = saved_schema_upgrade(before, after, runtime)
        if change:
            schema_upgrades.append(change)
            after['new_version'] = before['new_version']
            old_platform = before.get('last_modified_platform', {})
            if 'app_version' in old_platform:
                j.require(old_platform['app_version'] in j.nd.PROFILES,
                          'Unknown previous native save application')
                change['last_modified_app_before'] = old_platform['app_version']
                change['last_modified_app_after'] = after['last_modified_platform']['app_version']
                after['last_modified_platform']['app_version'] = old_platform['app_version']
            new_platform = after['last_modified_platform']
            if 'os_version' in old_platform and old_platform['os_version'] != new_platform.get('os_version'):
                old_os, new_os = old_platform['os_version'], new_platform.get('os_version')
                current_os = platform.mac_ver()[0]
                j.require(old_platform.get('os') == new_platform.get('os') == 'mac' and
                          all(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,2}', value)
                              for value in (old_os, new_os, current_os)) and new_os == current_os,
                          'Unreviewed native save OS provenance change')
                # Only the reviewed 185 -> 187 save migration can restamp the
                # last-save OS to this host. Source platform remains strict.
                new_platform['os_version'] = old_os
                change['last_modified_os_version_before'] = old_os
                change['last_modified_os_version_after'] = new_os
            # Native save stamps current-machine provenance. These identifiers
            # are not editing content; keep source platform and every other
            # nonempty field strict, and report names without leaking values.
            changed_device_fields = []
            for key in ('device_id', 'hard_disk_id', 'mac_address'):
                if key in old_platform and old_platform[key] != after['last_modified_platform'].get(key):
                    value = after['last_modified_platform'].get(key)
                    j.require(isinstance(value, str) and 0 < len(value) <= 256,
                              'Invalid native save provenance field: ' + key)
                    after['last_modified_platform'][key] = old_platform[key]
                    changed_device_fields.append(key)
            change['restamped_device_field_names'] = changed_device_fields
    compared, curve_average_changes = normalize_saved_curve_averages(expected, compared, runtime)
    quantized = []
    normalize = compound.normalize_paths if font_assets is None else fonts.normalize_paths
    compare_saved_timeline(normalize(expected, target), normalize(compared, target),
                           runtime, frame_tolerance=math.ceil(1_000_000 / expected.get('fps', 30)), quantized=quantized)
    checked_fonts = fonts.verify_assets(font_assets, actual, target, target) if font_assets is not None else 0
    compound.check_order(expected, actual)
    checked_compounds = compound.check_sidecars(actual, target, target, preserved)
    files = mirrors(target, record['timeline_id'])
    j.require(len({j.nd.digest(p) for p in files}) == 1 and len({p.stat().st_ino for p in files}) == 4, 'Edit copy mirrors disagree')
    for relative, fingerprint in record['resources'].items():
        j.require(j.nd.digest(target / relative) == fingerprint, 'Copied resource changed')
    for item in record['media_dependencies']:
        j.require(j.nd.digest(Path(item['path'])) == item['sha256'], 'Edited-copy media dependency changed')
    j.require(j.files_manifest(Path(record['source'])) == record['source_files'], 'Source changed since snapshot')
    root = j.read_json(j.nd.DRAFT_ROOT / 'root_meta_info.json')
    entries = [r for r in root['all_draft_store'] if r.get('draft_id') == record['draft_id']]
    j.require(len(entries) == 1 and entries[0]['draft_fold_path'] == str(target), 'Edit copy home registration differs')
    return {'status': 'verified', 'draft': str(target), 'operations': len(record['operations']),
            'duration_us': actual['duration'], 'tracks': len(actual['tracks']), 'source_files_unchanged': True,
            'preserved_fields_verified': True, 'four_mirrors_equal': True, 'video_exported': False,
            'nested_timelines': len(checked_compounds), 'compound_sidecars_verified': checked_compounds,
            'native_empty_companion_identity_changes': companion_identity_changes,
            'native_schema_upgrades': schema_upgrades,
            'native_provenance_restamps': provenance_restamps,
            'native_curve_speed_recomputations': curve_average_changes,
            'native_frame_quantization': quantized, 'font_files_verified': checked_fonts,
            'native_ui_acceptance': 'requires separate open/play/save/cold-reopen evidence'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('inspect'); p.add_argument('--draft', required=True); p.add_argument('--out', required=True)
    p = sub.add_parser('build'); p.add_argument('--plan', required=True); p.add_argument('--out', required=True)
    for name in ('publish', 'resume-publish', 'verify-build', 'verify'):
        p = sub.add_parser(name); p.add_argument('--build', required=True)
        if name in ('publish', 'resume-publish'):
            p.add_argument('--audit', required=True)
        else:
            p.add_argument('--report')
    args = parser.parse_args()
    j.require(j.nd.doctor()['runtime_profile'] in EDIT_PROFILES,
              'Existing-draft editing is unavailable for this exact runtime profile')
    if args.command == 'inspect':
        result = inspect(args.draft, args.out)
    elif args.command == 'build':
        result = build(args.plan, args.out)
    elif args.command in ('publish', 'resume-publish'):
        compound.require_publishable(j.read_json(Path(args.build) / 'expected-timeline.json'))
        result = j.publish(args.build, args.audit, resume=args.command == 'resume-publish',
                           verify_build_fn=verify_build, verify_live_fn=verify_live)
    elif args.command == 'verify':
        result = verify_live(args.build)
    else:
        record = verify_build(args.build)
        result = {'status': 'verified', 'name': record['name'], 'source_written': False, 'live_written': False}
    if getattr(args, 'report', None):
        j.write(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
