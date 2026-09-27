#!/usr/bin/env python3
"""Run seven native-export path shapes against an already verified build snapshot.

Requires official Jianying 11.5.0 and a fully quit editor before starting; this
script refuses to run while the editor is open and never quits it. It creates
output only below this repository's work/ directory, removing only its own
same-named case directories before each run. It never writes live drafts or the
home-page index, changes permissions/SIP/TCC, uses sudo or the network, or
modifies tracked repository files.

A case passes only when the tool exits 0, status is encoded-and-decoded, full
decode passed, and media.frames equals the expected count. The expected count
comes from result.json first, otherwise from build.json duration_us times
plan.json canvas.fps; if unavailable, the case cannot pass. An unreadable
render or invalid ftyp also fails its case.
"""

import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'work'
ENTRY = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'
CASES = (
    ('ascii', 'jm-ascii'),
    ('cjk', 'jm-路径-中文'),
    ('space', 'jm path space'),
    ('quote', 'jm"quote"'),
    ('backslash', 'jm\\backslash'),
    ('newline', 'jm\nnewline'),
    ('combo', '组合 空"格\\双\\新\n行'),
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_ftyp(path):
    """Walk bounded MP4 boxes; seek over mdat rather than reading media data."""
    with path.open('rb') as source:
        source.seek(0, os.SEEK_END)
        file_size = source.tell()
        offset = 0
        container = None
        while offset < file_size:
            if file_size - offset < 8:
                raise ValueError('Truncated MP4 box header')
            source.seek(offset)
            header = source.read(8)
            box_size = int.from_bytes(header[:4], 'big')
            header_size = 8
            if box_size == 1:
                if file_size - offset < 16:
                    raise ValueError('Truncated MP4 largesize')
                box_size = int.from_bytes(source.read(8), 'big')
                header_size = 16
            if box_size == 0 or box_size < header_size or box_size > file_size - offset:
                raise ValueError('Invalid MP4 box size at offset %d' % offset)
            if header[4:] == b'ftyp':
                payload_size = box_size - header_size
                if payload_size < 8 or (payload_size - 8) % 4:
                    raise ValueError('Invalid ftyp payload')
                current = {
                    'major_brand': source.read(4).decode('latin-1'),
                    'minor_version': int.from_bytes(source.read(4), 'big'),
                    'compatible_brands': [source.read(4).decode('latin-1')
                                          for _ in range((payload_size - 8) // 4)],
                }
                if container is not None and container != current:
                    raise ValueError('Conflicting ftyp boxes')
                container = current
            offset += box_size
    if container is None:
        raise ValueError('Missing ftyp box')
    return container


def expected_from_build(build):
    try:
        record = json.loads((build / 'build.json').read_text(encoding='utf-8'))
        plan = json.loads((build / 'plan.json').read_text(encoding='utf-8'))
        return round(Fraction(record['duration_us'], 1_000_000) *
                     Fraction(str(plan['canvas']['fps'])))
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def editor_running():
    processes = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True,
                               text=True, check=True).stdout
    return any(Path(line.strip()).name in ('VideoFusion-macOS', 'JianyingPro') or
               '/VideoFusion-macOS.app/Contents/MacOS/' in line
               for line in processes.splitlines())


def probe_video(video):
    command = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
               '-show_entries', 'stream=width,height,r_frame_rate,nb_frames,nb_read_frames,duration:'
               'format=duration:format_tags=major_brand', '-of', 'json', str(video)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError('ffprobe: ' + (result.stderr.strip() or str(result.returncode)))
    info = json.loads(result.stdout)
    stream = next((item for item in info.get('streams', [])), {})
    fmt = info.get('format', {})
    return {
        'width': stream.get('width'), 'height': stream.get('height'),
        'r_frame_rate': stream.get('r_frame_rate'), 'nb_frames': stream.get('nb_frames'),
        'nb_read_frames': stream.get('nb_read_frames'), 'duration': stream.get('duration'),
        'format_duration': fmt.get('duration'),
        'format_tags_major_brand': fmt.get('tags', {}).get('major_brand'),
    }


def run_case(name, directory, build, output, timeout, fallback_frames):
    job = output / directory
    if job.is_symlink():
        raise ValueError('Refusing to remove symlinked case directory: ' + repr(str(job)))
    if job.exists():
        if not job.is_dir():
            raise ValueError('Case path is not a directory: ' + repr(str(job)))
        shutil.rmtree(job)
    command = [sys.executable, str(ENTRY), 'export', '--build', str(build),
               '--out', str(job), '--timeout', str(timeout)]
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                 env=dict(os.environ, JIANYING_HEADLESS_ROOT=str(ROOT)),
                                 timeout=timeout + 240)
        returncode = process.returncode
        output_tail = (process.stderr + '\n' + process.stdout).strip().splitlines()[-5:]
    except subprocess.TimeoutExpired as error:
        returncode = None
        output_tail = ['Export command timed out: ' + str(error)]
    elapsed = round(time.monotonic() - started, 3)
    row = {'case': name, 'directory': directory, 'out': str(job),
           'returncode': returncode, 'elapsed_seconds': elapsed,
           'status': None, 'native_returncode': None, 'error': None, 'media': None,
           'full_decode_passed': None, 'source_build_unchanged': None,
           'render_exists': False, 'render_bytes': None, 'ffprobe': None, 'ftyp': None,
           'expected_frames': fallback_frames, 'passed': False}
    result_path = job / 'result.json'
    if result_path.is_file():
        try:
            evidence = json.loads(result_path.read_text(encoding='utf-8'))
            for key in ('status', 'native_returncode', 'error', 'media',
                        'full_decode_passed', 'source_build_unchanged'):
                row[key] = evidence.get(key)
        except (OSError, ValueError, AttributeError) as error:
            row['error'] = 'Invalid result.json: ' + str(error)
    else:
        row['error'] = '\n'.join(output_tail) or 'Missing result.json'
    media = row['media'] if isinstance(row['media'], dict) else {}
    if media.get('expected_frames') is not None:
        row['expected_frames'] = media['expected_frames']
    video = job / 'render.mp4'
    if video.is_file():
        row['render_exists'] = True
        row['render_bytes'] = video.stat().st_size
        for field, reader in (('ffprobe', probe_video), ('ftyp', read_ftyp)):
            try:
                row[field] = reader(video)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                row['error'] = row['error'] or str(error)
    row['passed'] = (returncode == 0 and row['status'] == 'encoded-and-decoded'
                     and row['full_decode_passed'] is True and
                     row['expected_frames'] is not None and
                     media.get('frames') == row['expected_frames'] and
                     row['render_exists'] and row['ffprobe'] is not None and
                     row['ftyp'] is not None)
    if not row['passed'] and not row['error']:
        row['error'] = '\n'.join(output_tail) or 'Acceptance criteria not met'
    return row


def show(rows, total):
    print('用例 | 目录名 | 退出码 | 原生退出码 | 产出 | 帧数/期望 | 时长 | ftyp brand | ffprobe TAG | 失败原因')
    for row in rows:
        media = row['media'] if isinstance(row['media'], dict) else {}
        probe = row['ffprobe'] or {}
        ftyp = row['ftyp'] or {}
        print(' | '.join(str(value) for value in (
            row['case'], json.dumps(row['directory'], ensure_ascii=False), row['returncode'],
            row['native_returncode'],
            str(row['render_bytes']) + ' bytes' if row['render_exists'] else '无',
            str(media.get('frames', '?')) + '/' + str(row['expected_frames'] if row['expected_frames'] is not None else '未知'),
            probe.get('format_duration') or media.get('duration_seconds') or '?',
            ftyp.get('major_brand', '?'), probe.get('format_tags_major_brand', '?'),
            json.dumps(row['error'], ensure_ascii=False) if row['error'] else '-',
        )))
    print('通过 %d/7（本次执行 %d/7）' % (total, len(rows)))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', required=True, type=Path, help='已验 build 快照绝对路径，须有 build.json')
    parser.add_argument('--tag', default='local', help='work/verify-export-paths-<tag> 的名字（默认 local）')
    parser.add_argument('--cases', default='all', help='all 或逗号分隔的用例名：' + ','.join(name for name, _ in CASES))
    parser.add_argument('--timeout', type=int, default=600, help='原生导出超时秒数（默认 600）')
    parser.add_argument('--results', type=Path, help='JSON 结果路径（默认本次输出目录的 results.json）')
    args = parser.parse_args()
    if not args.build.is_absolute() or not (args.build / 'build.json').is_file():
        parser.error('--build 必须是包含 build.json 的绝对目录')
    if not re.fullmatch(r'[\w.-]+', args.tag) or args.tag in ('.', '..'):
        parser.error('--tag 只能包含字母、数字、下划线、点或连字符')
    if args.timeout <= 0:
        parser.error('--timeout 必须为正整数')
    names = [name for name, _ in CASES]
    selected = names if args.cases == 'all' else args.cases.split(',')
    if not selected or len(selected) != len(set(selected)) or any(name not in names for name in selected):
        parser.error('--cases 必须为 all 或不重复的已知用例名列表')
    output = WORK / ('verify-export-paths-' + args.tag)
    if WORK.is_symlink() or output.is_symlink():
        parser.error('work 或输出目录不能是符号链接')
    build = args.build.resolve()
    if build == output or output in build.parents:
        parser.error('输出目录不能包含 build 快照')
    results = (args.results or output / 'results.json').absolute()
    target = results.resolve()
    if (results.is_symlink() or results.is_dir() or
            (ROOT.resolve() in target.parents and
             output.resolve() not in target.parents) or
            build == target or build in target.parents or
            any(output / directory == target or output / directory in target.parents
                for _, directory in CASES) or
            (results.parent != output and not results.parent.is_dir())):
        parser.error('--results 不能覆盖仓库受控文件、build 或用例目录；自定义父目录须已存在')
    print('真实导出要求官方剪映 11.5.0 已安装，导出前剪映必须完全退出；脚本不会替你退出应用。', flush=True)
    try:
        if editor_running():
            parser.error('检测到剪映进程：请先自行完全退出，再重新运行')
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
        build_hash = sha256(build / 'build.json')
        fallback_frames = expected_from_build(build)
        output.mkdir(parents=True, exist_ok=True)
        rows = [run_case(name, directory, build, output, args.timeout, fallback_frames)
                for name, directory in CASES if name in selected]
        passed = sum(row['passed'] for row in rows)
        report = {'repo_head': head, 'build': str(build), 'build_sha256': build_hash,
                  'tag': args.tag, 'cases': rows,
                  'summary': {'passed': passed, 'total': len(CASES), 'executed': len(rows)}}
        results.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        show(rows, passed)
        print(results)
        return 0 if passed == len(rows) else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
