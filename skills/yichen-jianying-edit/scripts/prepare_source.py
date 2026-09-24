#!/usr/bin/env python3
"""Inspect or normalize one source for the headless talking-head workflow."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def probe(source):
    source = Path(source)
    if source.is_symlink():
        raise ValueError('Source must be a regular file, not a symlink.')
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError('Source must be a regular file, not a symlink.')
    data = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(source)]))
    videos = [s for s in data.get('streams', [])
              if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    audio = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if len(videos) != 1 or len(audio) != 1:
        raise ValueError('Speech source must contain exactly one video stream and one audio stream.')
    video = videos[0]
    rotation = float(video.get('tags', {}).get('rotate', 0))
    for side in video.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    duration = float(video.get('duration', data.get('format', {}).get('duration', 0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Source duration is unavailable.')
    reasons = []
    if video.get('codec_name') != 'h264':
        reasons.append('video codec is not H.264')
    if video.get('pix_fmt') not in {'yuv420p', 'yuvj420p'}:
        reasons.append('pixel format is not 8-bit 4:2:0')
    if rotation % 360:
        reasons.append('rotation metadata is present')
    return {
        'source': str(source), 'sha256': digest(source), 'size': source.stat().st_size,
        'duration': duration, 'video_codec': video.get('codec_name'),
        'audio_codec': audio[0].get('codec_name'), 'pixel_format': video.get('pix_fmt'),
        'width': video.get('width'), 'height': video.get('height'), 'rotation': rotation,
        'compatible': not reasons, 'normalization_reasons': reasons,
    }


def normalize(source, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; normalization never overwrites files.')
    details = probe(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    portrait = details['rotation'] % 180 != 0 or details['height'] >= details['width']
    width, height = (1080, 1920) if portrait else (1920, 1080)
    vf = ('scale=%d:%d:force_original_aspect_ratio=decrease,'
          'pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1') % (width, height, width, height)
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-i', details['source'],
        '-map', '0:v:0', '-map', '0:a:0', '-vf', vf, '-c:v', 'h264_videotoolbox',
        '-pix_fmt', 'yuv420p', '-b:v', '12M', '-c:a', 'aac', '-b:a', '192k',
        '-movflags', '+faststart', '-metadata:s:v:0', 'rotate=0', str(output)], check=True)
    normalized = probe(output)
    if not normalized['compatible']:
        raise ValueError('Normalized output still fails compatibility checks: ' +
                         ', '.join(normalized['normalization_reasons']))
    return {'status': 'normalized', 'input_sha256': details['sha256'],
            'output': normalized['source'], 'output_sha256': normalized['sha256'],
            'duration': normalized['duration'], 'width': normalized['width'],
            'height': normalized['height'], 'compatible': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--source', required=True, type=Path)
    convert = commands.add_parser('normalize')
    convert.add_argument('--source', required=True, type=Path)
    convert.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.source) if args.command == 'inspect' else normalize(args.source, args.out)
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
