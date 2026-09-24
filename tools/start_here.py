#!/usr/bin/env python3
"""Beginner preflight and a two-second draft from an existing local video.

Never installs software, downloads media, registers drafts or exports video.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
sys.path.insert(0, str(ROOT / 'tools'))
from runtime_profiles import validate_identity
from build_toolchain import select_toolchain

APP = Path('/Applications/VideoFusion-macOS.app')
ENTRY = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'


def run(command, timeout=60):
    env = dict(os.environ, JIANYING_HEADLESS_ROOT=str(ROOT))
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=timeout)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def check():
    rows = []
    def add(level, label, message):
        rows.append({'level': level, 'check': label, 'message': message})
        print(f'[{level}] {label}：{message}')
    supported = platform.system() == 'Darwin' and platform.machine() == 'arm64'
    add('PASS' if supported else 'FAIL', '电脑', '需要 Apple Silicon Mac，终端不能在 Rosetta 模式下运行')
    version = platform.mac_ver()[0]
    modern = bool(version) and int(version.split('.')[0]) >= 26
    add('PASS' if modern else 'FAIL', 'macOS', '当前 ' + (version or '非 macOS') + '；要求 26.0 或以上')
    add('PASS' if sys.version_info >= (3, 9) else 'FAIL', 'Python', platform.python_version())
    for tool in ('ffmpeg', 'ffprobe'):
        add('PASS' if shutil.which(tool) else 'FAIL', tool,
            '已找到' if shutil.which(tool) else '未找到；安装后重新打开终端')
    if supported:
        try:
            manifest = json.loads((ROOT / 'bridge/SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
            _, toolchain = select_toolchain(manifest['reproduction_environment'])
            add('PASS', '已验工具链', toolchain['compiler'] + ' / SDK ' + toolchain['sdk_version']
                + ' / linker ' + toolchain['linker'] + '；最终仍须校验编译产物')
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            # An already-verified codec does not need recompilation.
            add('WARN' if (ROOT / 'bridge/jy14_codec_hardened_11_4').is_file() else 'FAIL',
                '编译工具', str(error))
    try:
        info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
        version = validate_identity(info, digest(APP / 'Contents/Frameworks/libvideoeditor.dylib'))
        add('PASS', '剪映身份', version + '，程序库指纹匹配；完整签名由桥接构建和正式写入检查')
    except (OSError, ValueError, KeyError) as error:
        add('FAIL', '剪映身份', str(error) + '；需要匹配的官方安装，不能修改哈希绕过')
    codec = ROOT / 'bridge/jy14_codec_hardened_11_4'
    if not codec.exists():
        add('WARN', '桥接组件', '首次下载尚未构建；下一步运行 python3 tools/build_native_codec.py')
    else:
        result = run([sys.executable, str(ENTRY), 'doctor'])
        add('PASS' if result.returncode == 0 else 'FAIL', 'doctor',
            '运行检查通过' if result.returncode == 0 else result.stderr.strip())
    draft_root = Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
    try:
        index = json.loads((draft_root / 'root_meta_info.json').read_bytes())
        ready = index.get('root_path') == str(draft_root) and isinstance(index.get('all_draft_store'), list)
    except (OSError, ValueError, AttributeError):
        ready = False
    add('PASS' if ready else 'WARN', '剪映首页',
        '默认草稿目录已初始化' if ready else '登记首页前需先打开剪映、创建并保存一个空白工程、正常退出；自定义草稿位置暂不支持')
    failed = any(row['level'] == 'FAIL' for row in rows)
    print('检查未通过，请先处理 FAIL。' if failed else
          '基础条件检查通过。WARN 需按说明处理；这不是其他电脑、画面或声音验收。')
    return 1 if failed else 0


def parse_source(raw):
    # Accept a path dragged from the file manager, or shell quoting pasted from
    # a terminal, without executing anything.
    raw = raw.strip()
    direct = Path(raw).expanduser()
    if direct.is_file():
        return direct.resolve()
    # Windows drag-and-drop hands over a double-quoted path. A backslash there is
    # always a separator and never an escape character, so shell unescaping must
    # not be applied to it.
    if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in '"\'':
        quoted = Path(raw[1:-1]).expanduser()
        if quoted.is_file():
            return quoted.resolve()
    if os.name != 'nt':
        parts = shlex.split(raw)
        if len(parts) != 1:
            raise ValueError('请只拖入一个本地视频文件。')
        path = Path(parts[0]).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError('素材必须是本地文件。')
        return path
    raise ValueError('素材必须是本地文件。')


def make_plan(source, data, name):
    streams = data.get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    if len(videos) != 1 or len(audios) > 1:
        raise ValueError('首次示例要求单画面视频，最多一条音轨。')
    video = videos[0]
    if video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p':
        raise ValueError('首次示例请使用 H.264 / yuv420p 的 SDR 视频；本工具不自动转码。')
    rotation = float(video.get('tags', {}).get('rotate', 0))
    rotations = [rotation] + [float(s.get('rotation', 0)) for s in video.get('side_data_list', [])]
    if any(value % 360 for value in rotations):
        raise ValueError('视频带旋转信息；请使用已正常定向的视频副本，不能直接照搬手机原片。')
    duration = float(video.get('duration', data.get('format', {}).get('duration', 0)))
    if not math.isfinite(duration) or duration < 2:
        raise ValueError('首次示例要求视频至少 2 秒。')
    width, height = video['width'], video['height']
    if any(type(n) is not int or n < 16 or n > 7680 or n % 2 for n in (width, height)):
        raise ValueError('首次示例要求宽高为 16–7680 范围内的偶数。')
    return {'schema': 'jy14-headless-plan/v1', 'name': name,
            'canvas': {'width': width, 'height': height, 'fps': 30},
            'tracks': [
                {'type': 'video', 'name': '原视频', 'segments': [
                    {'source': str(source), 'start_us': 0, 'duration_us': 2000000, 'volume': 1}]},
                {'type': 'text', 'name': '可编辑文字', 'segments': [
                    {'text': '我的第一个可编辑草稿', 'start_us': 0, 'duration_us': 2000000,
                     'size': 8, 'y': -0.65, 'color': '#FFFFFF', 'border_color': '#000000', 'border_width': .05}]}]}


def build(raw):
    doctor = run([sys.executable, str(ENTRY), 'doctor'])
    if doctor.returncode:
        raise ValueError('doctor 未通过。先运行 check 和桥接构建。\n' + doctor.stderr)
    if raw is None:
        if not sys.stdin.isatty():
            raise ValueError('非交互运行需提供 --source 视频路径。')
        raw = input('将一个至少 2 秒的 H.264 视频拖到终端，然后按回车：\n')
    source = parse_source(raw)
    probe = run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)])
    if probe.returncode:
        raise ValueError('无法读取视频；请确认文件已下载到本机。\n' + probe.stderr)
    plan = make_plan(source, json.loads(probe.stdout), 'first-draft')
    before = digest(source)
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='first-draft-', dir=work))
    plan['name'] = job.name
    (job / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for command, extra in (
        ('build', ['--plan', str(job / 'plan.json'), '--out', str(job / 'build')]),
        ('verify-build', ['--build', str(job / 'build')]),
    ):
        result = run([sys.executable, str(ENTRY), command] + extra, timeout=180)
        (job / (command + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
        (job / (command + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
        if result.returncode:
            raise ValueError('未完成 ' + command + '，日志保留在 ' + str(job) + '\n' + result.stderr)
    if digest(source) != before:
        raise ValueError('源视频发生变化；停止交付，记录保留在 ' + str(job))
    commands = {
        'publish': [sys.executable, str(ENTRY), 'publish', '--build', str(job / 'build'), '--audit', str(job / 'publish-audit')],
        'verify': [sys.executable, str(ENTRY), 'verify', '--build', str(job / 'build'), '--report', str(job / 'after-native-save.json')],
        'export': [sys.executable, str(ENTRY), 'export', '--build', str(job / 'build'), '--out', str(job / 'export')],
    }
    report = {'status': 'build-verified', 'name': plan['name'], 'build': str(job / 'build'),
              'source_unchanged': True, 'draft_registered': False, 'video_exported': False,
              'commands': {key: shlex.join(value) for key, value in commands.items()}}
    (job / 'next-steps.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    labels = {'publish': '保存工作并完全退出剪映后，登记到本机首页',
              'verify': '打开播放、保存退出、冷重开检查，再次退出后回读',
              'export': '可选：导出最初构建的快照，不包含后来手工修改'}
    notes = '# ' + plan['name'] + '\n'
    for key, label in labels.items():
        notes += '\n## ' + label + '\n\n```bash\n' + report['commands'][key] + '\n```\n'
    (job / 'next-steps.md').write_text(notes, encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('\n尚未写入剪映首页。保存工作并完全退出剪映后，再复制执行下面这一行：\n' +
          report['commands']['publish'])
    print('\n打开、播放、保存、退出并冷重开检查后，再次退出剪映，执行：\n' + report['commands']['verify'])
    print('\n可选：需要 MP4 时导出最初快照（不含后续手工修改）：\n' + report['commands']['export'])
    print('\n以上可复制命令也保存在：' + str(job / 'next-steps.md'))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='只读检查安装前提，不安装或修改系统')
    draft = sub.add_parser('build', help='从真实视频生成两秒草稿，不登记首页或导出')
    draft.add_argument('--source', help='本地视频；省略后交互拖入')
    args = parser.parse_args()
    try:
        return check() if args.command == 'check' else build(args.source)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
