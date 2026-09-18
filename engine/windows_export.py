"""Portable Windows FFmpeg renderer for an already verified build.

This backend produces a rendered MP4. It never writes a Jianying draft and never
invokes the native editor, macOS tools, account data, or the network.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

import ffmpeg_graph
import ffmpeg_tools
import jy14_headless as j

SCHEMA = 'jy14-ffmpeg-export/v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_timeline(build, record):
    sidecar = build / 'windows-timeline.json'
    if sidecar.is_file():
        return j.read_json(sidecar)
    raw = build / 'draft' / 'draft_info.json'
    try:
        return j.read_json(raw)
    except Exception as error:
        raise ValueError('Windows export requires a portable windows-timeline.json sidecar; '
                         'the existing draft_info.json is macOS-encrypted') from error


def verify_build(build):
    build = Path(build).resolve(strict=True)
    record = j.read_json(build / 'build.json')
    j.require(record.get('schema') in {'jy14-headless-build/v1', 'jy14-edit-build/v1'},
              'Export requires a supported verified build')
    if record.get('schema') == 'jy14-headless-build/v1':
        j.require((build / 'plan.json').is_file() and digest(build / 'plan.json') == record['plan_sha256'],
                  'Plan changed after build')
    j.require(j.files_manifest(build / 'draft') == record['files'], 'Built draft changed')
    timeline = read_timeline(build, record)
    j.require(isinstance(timeline, dict) and timeline.get('tracks'), 'Cannot export an empty timeline')
    return build, record, timeline


def stage_timeline(timeline, build, record, out):
    """Copy only build-owned resources and rewrite native paths to the job."""
    out = Path(out)
    source_root = (build / 'draft').resolve()
    target = Path(record.get('target', '')).resolve()
    copied = {}

    def rewrite(value, key=''):
        if isinstance(value, dict):
            return {k: rewrite(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v, key) for v in value]
        if isinstance(value, str) and key.lower().endswith('path'):
            path = Path(value)
            candidates = []
            if path.is_absolute():
                try:
                    candidates.append(path.resolve().relative_to(target))
                except ValueError:
                    pass
            if not candidates and value.startswith('##_draftpath_placeholder'):
                candidates.append(Path('Resources') / Path(value.split('/', 1)[-1]))
            for relative in candidates:
                source = source_root / relative
                if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(source_root):
                    destination = out / relative
                    if str(relative) not in copied:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                        j.require(digest(destination) == record['files'][str(relative)]['sha256'],
                                  'Build resource changed while staging: ' + str(relative))
                        copied[str(relative)] = digest(destination)
                    return str(destination)
            return value
        return value

    return rewrite(timeline), copied


def probe(executable, output, job, timeout):
    result = __import__('subprocess').run(
        [str(executable), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    (job / 'ffprobe.stdout.log').write_text(result.stdout, encoding='utf-8')
    (job / 'ffprobe.stderr.log').write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError('ffprobe failed: ' + result.stderr.strip())
    value = json.loads(result.stdout)
    j.require(value.get('format', {}).get('format_name', '').split(',')[0] == 'mov', 'Output is not MP4')
    video = [s for s in value.get('streams', []) if s.get('codec_type') == 'video']
    j.require(len(video) == 1 and video[0].get('codec_name') == 'h264', 'Expected one H.264 stream')
    j.require(video[0].get('pix_fmt') == 'yuv420p', 'Expected yuv420p output')
    return value


def run(build, out, ffmpeg=None, ffprobe=None, font=None, crf=18, preset='medium', timeout=600):
    build, record, original = verify_build(build)
    out = Path(out).resolve()
    j.require(not out.exists() and out.is_absolute() and 'work' in out.parts,
              'Export job must be a new absolute directory under work')
    out.mkdir(parents=True)
    started = time.monotonic()
    evidence = {'schema': SCHEMA, 'backend': 'windows-ffmpeg', 'status': 'preparing',
                'build': str(build), 'native_editable_draft': False,
                'source_build_unchanged': False, 'full_decode_passed': False,
                'network_allowed': False, 'warnings': [
                    'Output is a rendered MP4, not a Jianying-native editable draft']}
    try:
        ffmpeg = ffmpeg_tools.resolve_tool('ffmpeg.exe', ffmpeg, 'JIANYING_FFMPEG')
        ffprobe = ffmpeg_tools.resolve_tool('ffprobe.exe', ffprobe, 'JIANYING_FFPROBE')
        evidence.update(ffmpeg_path=str(ffmpeg), ffprobe_path=str(ffprobe),
                        ffmpeg_version=ffmpeg_tools.version(ffmpeg),
                        ffprobe_version=ffmpeg_tools.version(ffprobe))
        timeline, copied = stage_timeline(original, build, record, out)
        if font is None and any(t.get('type') == 'text' for t in timeline.get('tracks', [])):
            for candidate in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf',
                              r'C:\Windows\Fonts\simkai.ttf'):
                if Path(candidate).is_file():
                    font = candidate
                    break
        inputs, graph, video, audio, warnings = ffmpeg_graph.build(timeline, out, font)
        (out / 'filter_complex.txt').write_text(graph, encoding='utf-8')
        command = []
        for item in inputs:
            if item['still']:
                command += ['-loop', '1']
            command += ['-i', item['path']]
        command += ['-filter_complex_script', str(out / 'filter_complex.txt'), '-map', video]
        if audio:
            command += ['-map', audio]
        command += ['-c:v', 'libx264', '-preset', preset, '-crf', str(crf),
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
        if audio:
            command += ['-c:a', 'aac', '-b:a', '192k']
        command += ['-shortest', str(out / 'render.mp4')]
        evidence['ffmpeg_command'] = [str(ffmpeg), *command]
        command, returncode = ffmpeg_tools.run(ffmpeg, command, out / 'ffmpeg.stdout.log',
                                               out / 'ffmpeg.stderr.log', timeout)
        j.require(returncode == 0, 'FFmpeg render failed; inspect ffmpeg.stderr.log')
        output = out / 'render.mp4'
        media = probe(ffprobe, output, out, min(timeout, 120))
        decode_command = ['-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-']
        _, decode_code = ffmpeg_tools.run(ffmpeg, decode_command, out / 'decode.stdout.log',
                                          out / 'decode.stderr.log', timeout)
        j.require(decode_code == 0, 'Rendered MP4 did not fully decode')
        evidence.update(status='encoded-and-decoded', output=str(output), output_sha256=digest(output),
                        output_bytes=output.stat().st_size, media=media, warnings=warnings + evidence['warnings'],
                        full_decode_passed=True, source_build_unchanged=(j.files_manifest(build / 'draft') == record['files']),
                        elapsed_seconds=round(time.monotonic() - started, 3))
        j.require(evidence['source_build_unchanged'], 'Source build changed during export')
        j.write(out / 'result.json', evidence)
        return evidence
    except Exception as error:
        evidence.update(status='failed', error=str(error), partial_artifacts_retained=True,
                        elapsed_seconds=round(time.monotonic() - started, 3))
        j.write(out / 'result.json', evidence)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    parser.add_argument('--font')
    parser.add_argument('--crf', type=int, default=18)
    parser.add_argument('--preset', default='medium')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    try:
        result = run(args.build, args.out, args.ffmpeg, args.ffprobe, args.font,
                     args.crf, args.preset, args.timeout)
        print(json.dumps({k: result[k] for k in ('status', 'output', 'media', 'full_decode_passed',
                                                'source_build_unchanged', 'elapsed_seconds')}, ensure_ascii=False))
    except Exception as error:
        print('Windows FFmpeg export failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
