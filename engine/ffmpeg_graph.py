"""Translate the verified native timeline subset into an FFmpeg filter graph."""
from pathlib import Path
import json
import math


MICROS = 1_000_000


def sec(value):
    return f'{value / MICROS:.6f}'


def esc(value):
    return str(value).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'").replace(',', r'\,').replace('\n', r'\\n')


def atempo_chain(speed):
    speed = float(speed)
    parts = []
    while speed > 2:
        parts.append('atempo=2')
        speed /= 2
    while speed < .5:
        parts.append('atempo=0.5')
        speed /= .5
    parts.append(f'atempo={speed:.6f}')
    return ','.join(parts)


def material_index(timeline):
    return {node['id']: node for nodes in timeline.get('materials', {}).values()
            if isinstance(nodes, list) for node in nodes if isinstance(node, dict) and 'id' in node}


def text_value(node):
    try:
        data = json.loads(node.get('content', '{}'))
        return data.get('text', node.get('text', ''))
    except (TypeError, ValueError):
        return node.get('text', '')


def build(timeline, work, font=None):
    """Return ``(inputs, graph, video_label, audio_label, warnings)``."""
    work = Path(work)
    canvas = timeline.get('canvas_config', {})
    width, height = int(canvas['width']), int(canvas['height'])
    materials = material_index(timeline)
    if timeline.get('materials', {}).get('transitions'):
        raise ValueError('Unsupported transition for Windows FFmpeg backend; dissolve mapping is not yet available')
    inputs, filters, video_tracks, audio_labels, warnings = [], [], [], [], []
    input_count = 0

    def add_input(path, still=False):
        nonlocal input_count
        item = {'path': str(path), 'still': still, 'index': input_count}
        inputs.append(item)
        input_count += 1
        return item['index']

    def segment_input(segment, kind):
        node = materials.get(segment.get('material_id'))
        if not node or not node.get('path'):
            raise ValueError(f'Missing material for {kind} segment {segment.get("id", "?")}')
        path = Path(node['path'])
        if not path.is_file():
            raise ValueError(f'Missing staged material: {path}')
        still = node.get('type') in {'photo', 'image', 'gif'} or path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif'}
        return add_input(path, still), node

    def base_chain(index, node, segment, kind, label):
        start = int(segment.get('source_timerange', {}).get('start', 0))
        duration = int(segment.get('source_timerange', {}).get('duration', segment.get('target_timerange', {}).get('duration', 0)))
        speed = float(segment.get('speed', 1))
        trim = f'trim=start={sec(start)}:duration={sec(duration)},setpts=PTS-STARTPTS'
        if speed != 1:
            trim += f',setpts=PTS/{speed:.6f}'
        filters.append(f'[{index}:v]{trim}[{label}]')
        return label, duration / speed

    main_video = None
    main_audio = None
    for ti, track in enumerate(timeline.get('tracks', [])):
        kind = track.get('type')
        if kind not in {'video', 'audio', 'text'}:
            if track.get('segments'):
                raise ValueError(f'Unsupported track type for Windows FFmpeg backend: {kind}')
            continue
        if kind == 'video':
            segments = track.get('segments', [])
            labels, audio_parts = [], []
            for si, segment in enumerate(segments):
                index, node = segment_input(segment, kind)
                target = segment.get('target_timerange', {})
                start, duration = int(target.get('start', 0)), int(target.get('duration', 0))
                speed = float(segment.get('speed', 1))
                label = f'v{ti}_{si}'
                base_chain(index, node, segment, kind, label)
                transform = segment.get('clip', {}).get('transform', {})
                scale = segment.get('clip', {}).get('scale', {})
                sx, sy = float(scale.get('x', 1)), float(scale.get('y', 1))
                if ti == 0:
                    filters.append(f'[{label}]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[vmain{si}]')
                    labels.append(f'[vmain{si}]')
                else:
                    x = float(transform.get('x', 0)) * width / 2 + width / 2
                    y = float(transform.get('y', 0)) * height / 2 + height / 2
                    filters.append(f'[{label}]scale=iw*{sx:.6f}:ih*{sy:.6f},setpts=PTS-STARTPTS+{sec(start)}/TB[vo{ti}_{si}]')
                    video_tracks.append((f'[vo{ti}_{si}]', x, y, start, duration))
                # Native source audio is attached to the same material. Let FFmpeg
                # omit it when absent; explicit audio tracks are handled below.
                if node.get('has_audio', False):
                    alabel = f'amain{si}'
                    atrim = f'atrim=start={sec(segment.get("source_timerange", {}).get("start", 0))}:duration={sec(segment.get("source_timerange", {}).get("duration", duration))},asetpts=PTS-STARTPTS'
                    if speed != 1:
                        atrim += ',' + atempo_chain(speed)
                    filters.append(f'[{index}:a]{atrim}[{alabel}]')
                    audio_parts.append(f'[{alabel}]')
            if ti == 0:
                if not labels:
                    raise ValueError('Timeline has no main video segments')
                if len(labels) == 1:
                    filters.append(f'{labels[0]}null[vbase]')
                else:
                    filters.append(''.join(labels) + f'concat=n={len(labels)}:v=1:a=0[vbase]')
                main_video = '[vbase]'
                if audio_parts:
                    if len(audio_parts) > 1:
                        filters.append(''.join(audio_parts) + f'concat=n={len(audio_parts)}:v=0:a=1[amain]')
                    else:
                        filters.append(f'{audio_parts[0]}anull[amain]')
                    main_audio = '[amain]'
        elif kind == 'audio':
            for si, segment in enumerate(track.get('segments', [])):
                index, node = segment_input(segment, kind)
                target = segment.get('target_timerange', {})
                start, duration = int(target.get('start', 0)), int(target.get('duration', 0))
                speed = float(segment.get('speed', 1))
                label = f'atrack{ti}_{si}'
                chain = f'atrim=start={sec(segment.get("source_timerange", {}).get("start", 0))}:duration={sec(segment.get("source_timerange", {}).get("duration", duration))},asetpts=PTS-STARTPTS'
                if speed != 1:
                    chain += ',' + atempo_chain(speed)
                chain += f',volume={float(segment.get("volume", 1)):.6f},adelay={int(start / 1000)}:all=1'
                filters.append(f'[{index}:a]{chain}[{label}]')
                audio_labels.append(f'[{label}]')
        elif kind == 'text':
            for si, segment in enumerate(track.get('segments', [])):
                node = materials.get(segment.get('material_id'), {})
                text = text_value(node)
                if not text:
                    raise ValueError(f'Empty text material in segment {segment.get("id", "?")}')
                target = segment.get('target_timerange', {})
                start, duration = int(target.get('start', 0)), int(target.get('duration', 0))
                size = float(node.get('font_size', 48))
                transform = segment.get('clip', {}).get('transform', {})
                x = f'(w-text_w)/2+({float(transform.get("x", 0)):.6f})*w/2'
                y = f'h-text_h-({abs(float(transform.get("y", -.78))):.6f})*h/2'
                fontfile = font or node.get('font_path')
                if not fontfile:
                    raise ValueError('Windows FFmpeg subtitles require --font or a staged font_path')
                filters.append(f"[vbase]drawtext=fontfile='{esc(fontfile)}':text='{esc(text)}':fontsize={int(size)}:fontcolor=white:borderw=2:bordercolor=black:x={x}:y={y}:enable='between(t,{sec(start)},{sec(start + duration)})'[vtext{ti}_{si}]")
                main_video = f'[vtext{ti}_{si}]'

    if main_video is None:
        raise ValueError('Windows FFmpeg export requires a main video track')
    for label, x, y, start, duration in video_tracks:
        filters.append(f'{main_video}{label}overlay=x={x:.3f}:y={y:.3f}:eof_action=pass:shortest=0[ov{len(filters)}]')
        main_video = f'[ov{len(filters)-1}]'
    all_audio = ([main_audio] if main_audio else []) + audio_labels
    if all_audio:
        if len(all_audio) == 1:
            audio_label = all_audio[0]
        else:
            filters.append(''.join(all_audio) + f'amix=inputs={len(all_audio)}:duration=longest:dropout_transition=0[aout]')
            audio_label = '[aout]'
    else:
        audio_label = None
    return inputs, ';'.join(filters), main_video, audio_label, warnings
