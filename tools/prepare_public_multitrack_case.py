#!/usr/bin/env python3
"""从明确授权的本地开放影片创建可复核多轨案例，不联网或登记草稿。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

SOURCE_URL = 'https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_640x360.m4v.zip'
SOURCE_SHA256 = '7118242b6728d40c871479c5b3c0f0fb27d748089df15d7f1b469f297c74a2d6'
SOURCE_MEMBER = 'BigBuckBunny_640x360.m4v'
ATTRIBUTION = '(c) copyright 2008, Blender Foundation / www.bigbuckbunny.org'
LICENSE_URL = 'https://creativecommons.org/licenses/by/3.0/'
DURATION_US = 50233333


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-zip', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if digest(args.source_zip) != SOURCE_SHA256:
        raise ValueError('来源压缩包指纹不匹配')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(args.source_zip) as archive:
        source = out / SOURCE_MEMBER
        source.write_bytes(archive.read(SOURCE_MEMBER))
    assets = out / 'assets'
    assets.mkdir()
    records = []

    def extract(name, offset, options, media_type):
        destination = assets / name
        command = ['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(offset), '-i', str(source)]
        subprocess.run(command + options + [str(destination)], check=True)
        records.append({'file': 'assets/' + name, 'sha256': digest(destination),
                        'size': destination.stat().st_size, 'source_offset_seconds': offset,
                        'media_type': media_type, 'ffmpeg_options': options})
        return str(destination)

    videos = [extract(f'video-{i:02}.mp4', 35 + i * 11,
                      ['-t', '10', '-an', '-vf', 'fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1', '-c:v', 'libx264',
                       '-preset', 'fast', '-crf', '22', '-pix_fmt', 'yuv420p'], 'video')
              for i in range(24)]
    pictures = [extract(f'frame-{i:02}.png', 75 + i * 23,
                        ['-frames:v', '1'], 'photo') for i in range(8)]
    audios = [extract(f'audio-{i:02}.wav', 35 + i * 10,
                      ['-t', '8', '-vn', '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '2'], 'audio')
              for i in range(7)]

    def segment(source_path, start, duration, speed=1, **values):
        return dict(source=source_path, start_us=start, duration_us=duration,
                    source_start_us=0, source_duration_us=round(duration * speed),
                    speed=speed, **values)

    # 变速段按整帧分配；124 可被 4 整除，0.75/1.5 倍的源区间也落在整帧。
    frames = [i * 124 for i in range(12)] + [1507]
    edges = [round(frame * 1000000 / 30) for frame in frames]
    main_video = [segment(videos[i], edges[i], edges[i + 1] - edges[i],
                          (1, 1.5, .75, 2)[i % 4] if i < 11 else 1, volume=0) for i in range(12)]
    tracks = [{'type': 'video', 'name': '主画面', 'segments': main_video}]
    overlay_sources = videos[12:] + pictures + videos[12:18]
    cursor = 0
    for i in range(7):
        count = 4 if i < 5 else 3
        segments = []
        for j in range(count):
            start = 2000000 + j * 12000000 + i * 350000
            segments.append(segment(overlay_sources[cursor], start, 2000000,
                                    volume=0, scale=.28, x=(-.65 if i % 2 == 0 else .65),
                                    y=.65 - (i // 2) * .36))
            cursor += 1
        tracks.append({'type': 'video', 'name': f'补充画面 {i + 1}', 'segments': segments})
    assert cursor == 26
    audio_edges = [round(i * DURATION_US / 7) for i in range(8)]
    tracks.append({'type': 'audio', 'name': '影片原声分段', 'segments': [
        segment(audios[i], audio_edges[i], audio_edges[i + 1] - audio_edges[i], volume=.7)
        for i in range(7)]})
    for i in range(14):
        count = 8 if i < 11 else 7
        segments = []
        for j in range(count):
            start = j * 6000000 + i * 220000
            text = ('开放影片多轨验收' if i == 0 else f'轨道 {i + 1} · 片段 {j + 1}')
            if i == 13:
                text = 'Blender Foundation · CC BY 3.0'
            segments.append(dict(text=text, start_us=start, duration_us=900000,
                                 size=4, x=0, y=-.78 if i % 2 == 0 else -.64,
                                 color='#FFFFFF', border_color='#000000', border_width=.03))
        tracks.append({'type': 'text', 'name': f'可编辑文字 {i + 1}', 'segments': segments})
    plan = {'schema': 'jy14-headless-plan/v1', 'name': 'Public BBB multitrack b481 acceptance',
            'canvas': {'width': 1080, 'height': 1920, 'fps': 30}, 'tracks': tracks}
    assert len(tracks) == 23 and sum(len(t['segments']) for t in tracks) == 154
    assert len(records) == 39
    (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    manifest = dict(schema='jianying-public-case/v1', source_url=SOURCE_URL,
                    source_archive_sha256=SOURCE_SHA256, source_sha256=digest(source),
                    attribution=ATTRIBUTION, license_url=LICENSE_URL,
                    license_source='https://peach.blender.org/about/',
                    modifications='影片切片、抽帧、原声拆分、变速、画中画和新增中文文字；非原始影片工程',
                    media=records, duration_us=DURATION_US, tracks=23, segments=154)
    (out / 'materials.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'prepared', 'tracks': 23, 'segments': 154, 'media_files': 39,
                      'plan': str(out / 'plan.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
