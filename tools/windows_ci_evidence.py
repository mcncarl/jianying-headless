#!/usr/bin/env python3
"""Create retained Windows FFmpeg evidence from the reviewed public media fixture."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
import windows_portable


def mean_volume(path):
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-t', '2',
         '-vn', '-af', 'volumedetect', '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if result.returncode:
        raise RuntimeError('FFmpeg audio-level verification failed')
    matches = re.findall(r'mean_volume: ([+-]?[\d.]+) dB', result.stderr)
    if not matches:
        raise RuntimeError('Public IG case has no measurable audio')
    return float(matches[-1])


def main():
    work = ROOT / 'work'
    input_dir = work / 'windows-ci-input'
    build = work / 'windows-ci-build'
    export = work / 'windows-ci-export'
    if any(path.exists() for path in (input_dir, build, export)):
        raise SystemExit('Windows CI evidence paths must not already exist')
    input_dir.mkdir(parents=True)
    source = ROOT / 'docs/media/hypit-original-preview.mp4'
    if not source.is_file():
        raise SystemExit('Reviewed public media fixture is missing')
    media = input_dir / '真实 media with spaces.mp4'
    shutil.copyfile(source, media)
    font = next((Path(item) for item in
                 (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf')
                 if Path(item).is_file()), None)
    if font is None:
        raise SystemExit('A Windows Chinese system font is required')
    plan = {'schema': 'jy14-headless-plan/v1', 'name': 'windows-ci-evidence',
            'canvas': {'width': 640, 'height': 360, 'fps': 25},
            'tracks': [
                {'type': 'video', 'name': 'main', 'segments': [{
                    'source': str(media.resolve()), 'start_us': 0, 'duration_us': 2_000_000,
                    'source_start_us': 0, 'source_duration_us': 2_000_000,
                    'speed': 1, 'volume': .5}]},
                {'type': 'text', 'name': 'caption one', 'segments': [{
                    'text': 'Windows 中文验收', 'start_us': 100_000, 'duration_us': 1_000_000}]},
                {'type': 'text', 'name': 'caption two', 'segments': [{
                    'text': '重叠字幕与空格路径', 'start_us': 700_000, 'duration_us': 1_000_000}]}
            ]}
    plan_path = input_dir / 'plan.json'
    plan_path.write_bytes(windows_portable.packed(plan))
    entry = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'
    environment = dict(os.environ, JIANYING_HEADLESS_ROOT=str(ROOT))
    commands = [
        [sys.executable, str(entry), 'build', '--backend', 'windows-ffmpeg',
         '--plan', str(plan_path), '--out', str(build)],
        [sys.executable, str(entry), 'verify-build', '--backend', 'windows-ffmpeg',
         '--build', str(build)],
        [sys.executable, str(entry), 'export', '--backend', 'windows-ffmpeg',
         '--build', str(build), '--out', str(export), '--font', str(font)],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=environment, check=True, timeout=900)
    result = windows_portable.read_json(export / 'result.json')
    if not result['full_decode_passed'] or not result['source_build_unchanged']:
        raise SystemExit('Windows evidence did not pass final verification')
    source_db = mean_volume(source)
    export_db = mean_volume(export / 'render.mp4')
    expected_gain_db = 20 * math.log10(.5)
    measured_gain_db = export_db - source_db
    if abs(measured_gain_db - expected_gain_db) > 1.0:
        raise SystemExit('Public IG audio gain differs from the requested 0.5 volume')
    gain = {'source_mean_db': source_db, 'export_mean_db': export_db,
            'requested_volume': .5, 'expected_gain_db': round(expected_gain_db, 3),
            'measured_gain_db': round(measured_gain_db, 3), 'tolerance_db': 1.0}
    windows_portable.write_new(export / 'audio-gain.json', gain)
    print(json.dumps({'status': result['status'], 'output_sha256': result['output_sha256'],
                      'font_sha256': result['font']['sha256'],
                      'full_decode_passed': result['full_decode_passed'],
                      'audio_gain': gain}, ensure_ascii=False))


if __name__ == '__main__':
    main()
