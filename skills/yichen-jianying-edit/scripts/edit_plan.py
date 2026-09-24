#!/usr/bin/env python3
"""Compile one source-time edit map and render voice-only WAV for review."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be a finite number.')
    return float(value)


def compile_plan(raw):
    if raw.get('schema') != 'jianying-edit-plan/v1':
        raise ValueError('Unsupported plan schema.')
    if raw.get('settings_confirmed') is not True:
        raise ValueError('Confirm this task\'s speed, sound and edit intensity before compiling.')
    if raw.get('check_decisions_pending') is not False:
        raise ValueError('Resolve CHECK decisions before compiling the delivery plan.')
    if raw.get('bgm', False):
        raise ValueError('This template builder has no BGM implementation; place requested BGM natively.')
    source = Path(raw['source']).resolve(strict=True)
    source_hash = digest(source)
    if source_hash != raw.get('source_sha256'):
        raise ValueError('Source fingerprint changed.')
    duration = number(raw['source_duration'], 'source duration')
    speed = number(raw['speed'], 'speed')
    fps = number(raw['fps'], 'fps')
    volume = number(raw.get('voice_volume', 1.0), 'voice volume')
    if not 0.1 <= speed <= 8 or fps not in {24, 25, 30, 50, 60} or not 0 <= volume <= 4:
        raise ValueError('Unsupported speed, timeline frame rate, or voice volume.')
    probe = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(source)]))
    if not any(s['codec_type'] == 'audio' for s in probe['streams']):
        raise ValueError('Source has no audio stream for speech editing.')
    if abs(float(probe['format']['duration']) - duration) > 0.1:
        raise ValueError('Declared source duration differs from ffprobe; inspect the actual media.')
    ranges = []
    frame_cursor = 0
    previous_end = 0.0
    for index, keep in enumerate(raw['keeps']):
        start = number(keep['start'], 'keep start')
        end = number(keep['end'], 'keep end')
        if start < previous_end - 1e-7 or not 0 <= start < end <= duration + 1e-6:
            raise ValueError('Keeps must be ordered, non-overlapping and inside source duration.')
        previous_end = end
        protected = keep.get('protect', [])
        for span in protected:
            if len(span) != 2:
                raise ValueError('Protected spans must contain [start, end].')
            a, b = number(span[0], 'protected start'), number(span[1], 'protected end')
            if not start - 1e-7 <= a < b <= end + 1e-7:
                raise ValueError('Protected pronunciation lies outside its keep range.')
        frames = math.floor((end - start) * fps / speed + 1e-7)
        if frames < 1:
            raise ValueError('Keep is shorter than one output frame; merge or adjust it.')
        span = frames * speed / fps
        first = min((p[0] for p in protected), default=end)
        last = max((p[1] for p in protected), default=start)
        lower, upper = max(start, last - span), min(first, end - span)
        if lower > upper + 1e-7:
            raise ValueError('No frame-aligned fit protects speech in keep %d; adjust source margins.' % index)
        chosen_start = min(max(start, lower), upper)
        source_start = round(chosen_start * 1e6)
        source_duration = round(span * 1e6)
        target_start = round(frame_cursor * 1e6 / fps)
        frame_cursor += frames
        target_end = round(frame_cursor * 1e6 / fps)
        ranges.append({'source_start_us': source_start, 'source_duration_us': source_duration,
                       'target_start_us': target_start, 'target_duration_us': target_end - target_start,
                       'frames': frames, 'protect': protected, 'reason': keep.get('reason', '')})
    if not ranges:
        raise ValueError('Plan must retain some source material.')

    def project(t, snap_next=False):
        microseconds = round(number(t, 'source time') * 1e6)
        for r in ranges:
            a, b = r['source_start_us'], r['source_start_us'] + r['source_duration_us']
            if a - 1 <= microseconds <= b + 1:
                return r['target_start_us'] + round((min(max(microseconds, a), b) - a) / speed)
            if snap_next and microseconds < a:
                return r['target_start_us']
        raise ValueError('A subtitle or cue endpoint is inside deleted source: %s' % t)

    total = round(frame_cursor * 1e6 / fps)
    subtitle_input = raw.get('subtitles')
    if subtitle_input is False:
        subtitle_input = []
    elif not isinstance(subtitle_input, list):
        raise ValueError('Subtitles must be a list or false.')
    subtitles = []
    for cue in subtitle_input:
        text = cue['text'].strip()
        if not text or '\x00' in text:
            raise ValueError('Subtitles require non-empty text.')
        start = max(0, project(cue['start']) - 20000)
        end = min(total, project(cue['end']) + 40000)
        if end <= start or (subtitles and start < subtitles[-1]['start_us']):
            raise ValueError('Subtitle order or duration is invalid.')
        subtitles.append({'start_us': start, 'end_us': end, 'text': text})
    for a, b in zip(subtitles, subtitles[1:]):
        a['end_us'] = min(a['end_us'], b['start_us'])
        if a['end_us'] <= a['start_us']:
            raise ValueError('Subtitles overlap completely; revise phrase boundaries.')
    effects = []
    for event in raw.get('sfx', []):
        if event.get('name') != '啵1':
            raise ValueError('Automatic native sound cloning currently supports only registered 啵1.')
        gain = number(event.get('volume', 0.13), 'sound volume')
        if not 0 <= gain <= 1:
            raise ValueError('Sound volume must be between 0 and 1.')
        start = project(event['source_time'], event.get('snap') == 'next')
        start = round(round(start * fps / 1e6) * 1e6 / fps)
        if start + 366666 > total:
            raise ValueError('Sound cue would extend beyond the final video.')
        effects.append({'name': '啵1', 'start_us': start, 'duration_us': 366666,
                        'volume': gain, 'reason': event.get('reason', '')})
    return {'schema': 'jianying-compiled-plan/v1', 'source': str(source),
            'source_sha256': source_hash, 'source_duration_us': round(duration * 1e6),
            'fps': int(fps), 'speed': speed, 'voice_volume': volume,
            'duration_us': total, 'frames': frame_cursor, 'ranges': ranges,
            'subtitles': subtitles, 'sfx': effects, 'bgm': False,
            'settings': raw.get('settings', {}), 'decisions': raw.get('decisions', [])}


def validate_compiled(plan):
    if plan.get('schema') != 'jianying-compiled-plan/v1':
        raise ValueError('Expected compiled plan.')
    if digest(plan['source']) != plan['source_sha256']:
        raise ValueError('Compiled plan source changed.')
    speed = number(plan['speed'], 'compiled speed')
    volume = number(plan['voice_volume'], 'compiled voice volume')
    if not 0.1 <= speed <= 8 or plan['fps'] not in {24, 25, 30, 50, 60} or not 0 <= volume <= 4:
        raise ValueError('Compiled speed, volume, or frame rate is invalid.')
    if plan.get('bgm') is not False or not plan['ranges']:
        raise ValueError('Compiled plan must contain speech with no automatic BGM.')
    if not isinstance(plan.get('subtitles'), list):
        raise ValueError('Compiled subtitles must be a list, which may be empty.')
    cursor = 0
    source_end = -1
    frames = 0
    for r in plan['ranges']:
        for field in ['source_start_us', 'source_duration_us', 'target_start_us', 'target_duration_us', 'frames']:
            if isinstance(r[field], bool) or not isinstance(r[field], int):
                raise ValueError('Compiled range times and frame counts must be integers.')
        if r['target_start_us'] != cursor or r['target_duration_us'] <= 0:
            raise ValueError('Video timeline has a gap, overlap or invalid duration.')
        if r['source_start_us'] < source_end or r['source_duration_us'] <= 0:
            raise ValueError('Source time map is invalid.')
        source_end = r['source_start_us'] + r['source_duration_us']
        if source_end > plan['source_duration_us'] + 1:
            raise ValueError('Source range exceeds source duration.')
        if abs(r['source_duration_us'] / plan['speed'] - r['target_duration_us']) > 2:
            raise ValueError('Source duration, target duration and speed disagree.')
        for a, b in r.get('protect', []):
            if not r['source_start_us'] - 1 <= round(a * 1e6) < round(b * 1e6) <= source_end + 1:
                raise ValueError('Compiled edit would remove protected pronunciation.')
        frames += r['frames']
        cursor += r['target_duration_us']
        if cursor != round(frames * 1e6 / plan['fps']):
            raise ValueError('Compiled timeline is not aligned to its frame grid.')
    if cursor != plan['duration_us'] or frames != plan['frames']:
        raise ValueError('Final duration disagrees with video timeline.')
    previous = -1
    for s in plan['subtitles']:
        if not previous <= s['start_us'] < s['end_us'] <= cursor:
            raise ValueError('Subtitle timing is invalid.')
        if not isinstance(s['text'], str) or not s['text'].strip():
            raise ValueError('Compiled subtitle text is empty.')
        previous = s['end_us']
    for cue in plan['sfx']:
        if cue.get('name') != '啵1' or cue['duration_us'] != 366666:
            raise ValueError('Compiled sound cue is outside the native allowlist.')
        if not 0 <= cue['start_us'] < cue['start_us'] + cue['duration_us'] <= cursor:
            raise ValueError('Compiled sound extends past the timeline.')
        if not 0 <= number(cue['volume'], 'sound volume') <= 1:
            raise ValueError('Compiled sound volume is invalid.')


def timecode(us):
    milliseconds = round(us / 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return '%02d:%02d:%02d,%03d' % (hours, minutes, seconds, milliseconds)


def write_compiled(plan, out):
    out = Path(out).resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    (out / 'compiled.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    if plan['subtitles']:
        (out / 'subtitles.srt').write_text('\n\n'.join(
            '%d\n%s --> %s\n%s' % (i + 1, timecode(s['start_us']), timecode(s['end_us']), s['text'])
            for i, s in enumerate(plan['subtitles'])) + '\n')
        (out / 'transcript.md').write_text('# 剪后转写稿\n\n' + '\n\n'.join(
            '[%s] %s' % (timecode(s['start_us']), s['text']) for s in plan['subtitles']) + '\n')


def tempo_filters(speed):
    factors = []
    while speed > 2:
        factors.append(2)
        speed /= 2
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    factors.append(speed)
    return ','.join('atempo=%.10g' % s for s in factors)


def render_audio(plan, output, work):
    validate_compiled(plan)
    output, work = Path(output).resolve(), Path(work).resolve()
    if output.suffix.lower() != '.wav' or output.exists():
        raise ValueError('Review output must be a brand-new WAV; MP4 rendering is not supported.')
    work.mkdir(parents=True, mode=0o700, exist_ok=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    pcm = work / 'source-48k.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-i', plan['source'], '-map', '0:a:0',
                    '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(pcm)], check=True)
    samples_written = 0
    with wave.open(str(pcm), 'rb') as source, wave.open(str(output), 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48000)
        for r in plan['ranges']:
            a = round(r['source_start_us'] * 48000 / 1e6)
            n = round(r['source_duration_us'] * 48000 / 1e6)
            source.setpos(a)
            raw = source.readframes(n)
            if len(raw) != n * 2:
                raise ValueError('Source PCM is shorter than the protected edit map.')
            end = round((r['target_start_us'] + r['target_duration_us']) * 48000 / 1e6)
            count = end - samples_written
            filt = tempo_filters(plan['speed']) + ',volume=%.10g,apad=whole_len=%d,atrim=end_sample=%d' % (
                plan['voice_volume'], count, count)
            result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 's16le', '-ar', '48000', '-ac', '1',
                                     '-i', 'pipe:0', '-af', filt, '-f', 's16le', 'pipe:1'],
                                    input=raw, capture_output=True, check=True)
            if len(result.stdout) != count * 2:
                raise ValueError('Review audio duration is not exact.')
            target.writeframesraw(result.stdout)
            samples_written = end
    return {'status': 'completed', 'wav': str(output), 'sha256': digest(output),
            'samples': samples_written, 'duration': samples_written / 48000, 'video_rendered': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('compile')
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p = sub.add_parser('render-audio')
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if args.command == 'compile':
        result = compile_plan(plan)
        validate_compiled(result)
        write_compiled(result, args.out)
        print(json.dumps({'duration': result['duration_us'] / 1e6, 'segments': len(result['ranges']),
                          'subtitles': len(result['subtitles']), 'sfx': len(result['sfx'])}))
    else:
        print(json.dumps(render_audio(plan, args.out, args.work), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
