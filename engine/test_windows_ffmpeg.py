import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
import ffmpeg_tools
import windows_export
import jy14_headless as j


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required')
class WindowsFfmpegTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='windows-ffmpeg-', dir=ROOT / 'work'))
        self.build = self.root / 'build'
        draft = self.build / 'draft' / 'Resources'
        draft.mkdir(parents=True)
        self.media = draft / 'sample.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc=size=320x240:rate=25', '-t', '1', '-pix_fmt', 'yuv420p',
                        '-c:v', 'libx264', '-y', str(self.media)], check=True)
        target = Path('C:/Jianying/Test')
        timeline = {'canvas_config': {'width': 320, 'height': 240}, 'fps': 25,
                    'duration': 1_000_000, 'materials': {'videos': [
                        {'id': 'video-1', 'path': str(target / 'Resources/sample.mp4'), 'type': 'video', 'has_audio': False}]},
                    'tracks': [{'type': 'video', 'segments': [{'id': 'segment-1', 'material_id': 'video-1',
                        'target_timerange': {'start': 0, 'duration': 1_000_000},
                        'source_timerange': {'start': 0, 'duration': 1_000_000}, 'speed': 1}]}]}
        (self.build / 'windows-timeline.json').write_text(json.dumps(timeline), encoding='utf-8')
        record = {'schema': 'jy14-headless-build/v1', 'target': str(target),
                  'plan_sha256': '', 'files': j.files_manifest(self.build / 'draft')}
        (self.build / 'plan.json').write_text('{}', encoding='utf-8')
        record['plan_sha256'] = hashlib.sha256((self.build / 'plan.json').read_bytes()).hexdigest()
        (self.build / 'build.json').write_text(json.dumps(record), encoding='utf-8')

    def test_tool_resolution_explicit_and_versions(self):
        self.assertEqual(ffmpeg_tools.resolve_tool('ffmpeg.exe', str(Path(shutil.which('ffmpeg')))).name.lower(), 'ffmpeg.exe')
        self.assertIn('ffmpeg', ffmpeg_tools.version(shutil.which('ffmpeg')).lower())

    def test_render_and_full_decode(self):
        result = windows_export.run(self.build, self.root / 'work' / 'export')
        self.assertEqual(result['schema'], 'jy14-ffmpeg-export/v1')
        self.assertTrue(result['full_decode_passed'])
        self.assertTrue((self.root / 'work' / 'export' / 'render.mp4').is_file())
        self.assertTrue((self.root / 'work' / 'export' / 'filter_complex.txt').is_file())
        self.assertEqual(j.files_manifest(self.build / 'draft'), result and self.build_record_files())

    def test_windows_cli_build_verify_and_export(self):
        source = self.root / 'source.mp4'
        shutil.copyfile(self.media, source)
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'schema': 'jy14-headless-plan/v1', 'name': 'portable-test',
                                    'canvas': {'width': 320, 'height': 240, 'fps': 25},
                                    'tracks': [{'type': 'video', 'name': 'main', 'segments': [{
                                        'source': str(source), 'start_us': 0, 'duration_us': 1_000_000,
                                        'source_start_us': 0, 'source_duration_us': 1_000_000}]}]}),
                         encoding='utf-8')
        build = self.root / 'cli-build'
        entry = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'
        env = dict(os.environ, JIANYING_HEADLESS_ROOT=str(ROOT))
        result = subprocess.run([sys.executable, str(entry), 'build', '--plan', str(plan), '--out', str(build)],
                                env=env, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(entry), 'verify-build', '--build', str(build)],
                                env=env, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(entry), 'export', '--build', str(build),
                                 '--out', str(self.root / 'work' / 'cli-export')],
                                env=env, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def build_record_files(self):
        return json.loads((self.build / 'build.json').read_text())['files']

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
