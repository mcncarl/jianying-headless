"""Portable beginner-flow checks; no editor writes or downloads."""
from copy import deepcopy
import importlib.util
import json
import contextlib
import io
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('start_here', ROOT / 'tools/start_here.py')
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)
sys.path.insert(0, str(ROOT / 'engine'))
import runtime_profiles as profiles


class FirstDraftTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / 'work/onboarding-tests'
        work.mkdir(parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='case-', dir=work))
        self.source = self.folder / "真实 素材's.mp4"
        self.source.write_bytes(b'unit test bytes')
        self.media = {'streams': [{'codec_type': 'video', 'codec_name': 'h264',
            'pix_fmt': 'yuv420p', 'width': 1080, 'height': 1920, 'duration': '6.033333'}]}

    def run_check(self, info, fingerprint, macos_version):
        root = Path(tempfile.mkdtemp(prefix='check-', dir=self.folder))
        app = root / 'VideoFusion-macOS.app' / 'Contents'
        (app / 'Frameworks').mkdir(parents=True)
        with (app / 'Info.plist').open('wb') as stream:
            plistlib.dump(info, stream)
        (app / 'Frameworks/libvideoeditor.dylib').write_bytes(b'test library')
        bridge = root / 'bridge'
        bridge.mkdir()
        (bridge / 'SOURCE_MANIFEST.json').write_text(
            json.dumps({'reproduction_environment': {}}, ensure_ascii=False))
        toolchain = {'compiler': 'Apple clang 17.0.0', 'sdk_version': '26.1',
                     'linker': '1230.1'}
        output = io.StringIO()
        with patch.object(start, 'ROOT', root), patch.object(start, 'APP', app.parent), \
                patch.object(start, 'digest', return_value=fingerprint), \
                patch.object(start.platform, 'system', return_value='Darwin'), \
                patch.object(start.platform, 'machine', return_value='arm64'), \
                patch.object(start.platform, 'mac_ver', return_value=(macos_version, '', '')), \
                patch.object(start.shutil, 'which', return_value='/usr/bin/tool'), \
                patch.object(start, 'select_toolchain', return_value=(None, toolchain)), \
                contextlib.redirect_stdout(output):
            result = start.check()
        return result, output.getvalue()

    def test_check_accepts_exact_appstore_build_481_on_macos_15_6_1(self):
        profile = profiles.APPSTORE_1140_PROFILE
        info = {'CFBundleShortVersionString': '11.4.0', 'CFBundleVersion': '481',
                'CFBundleIdentifier': 'com.lemon.lvpro'}
        result, output = self.run_check(info, profiles.PROFILES[profile], '15.6.1')

        self.assertEqual(result, 0)
        self.assertIn('[PASS] macOS', output)
        self.assertIn('[PASS] 剪映身份：11.4.0 / build 481', output)

    def test_check_rejects_nearby_build_and_old_1140_on_macos_15(self):
        cases = (
            ({'CFBundleShortVersionString': '11.4.0', 'CFBundleVersion': '480',
              'CFBundleIdentifier': 'com.lemon.lvpro'},
             profiles.PROFILES[profiles.APPSTORE_1140_PROFILE], True),
            ({'CFBundleShortVersionString': '11.4.0', 'CFBundleVersion': '11.4.0',
              'CFBundleIdentifier': 'com.lemon.lvpro'}, profiles.PROFILES['11.4.0'], False),
        )
        for info, fingerprint, identity_rejected in cases:
            with self.subTest(info=info):
                result, output = self.run_check(info, fingerprint, '15.6.1')
                self.assertNotEqual(result, 0)
                self.assertIn('[FAIL] macOS', output)
                self.assertIn('[FAIL] 剪映身份' if identity_rejected else '[PASS] 剪映身份', output)

    def test_check_rejects_exact_build_on_other_macos_15_patch(self):
        info = {'CFBundleShortVersionString': '11.4.0', 'CFBundleVersion': '481',
                'CFBundleIdentifier': 'com.lemon.lvpro'}
        for macos_version in ('15.6.0', '26.1'):
            with self.subTest(macos_version=macos_version):
                result, output = self.run_check(
                    info, profiles.PROFILES[profiles.APPSTORE_1140_PROFILE], macos_version)
                self.assertNotEqual(result, 0)
                self.assertIn('[FAIL] macOS', output)
                self.assertIn('[PASS] 剪映身份', output)

    def test_drag_paths_and_literal_spaces(self):
        self.assertEqual(start.parse_source(str(self.source)), self.source)
        self.assertEqual(start.parse_source(start.shlex.quote(str(self.source))), self.source)
        escaped = str(self.source).replace(' ', '\\ ').replace("'", "\\'")
        self.assertEqual(start.parse_source(escaped), self.source)

    def test_shell_syntax_is_not_executed(self):
        with self.assertRaises((ValueError, OSError)):
            start.parse_source('$(touch should-not-exist)')
        self.assertFalse((ROOT / 'should-not-exist').exists())

    def test_plan_is_real_media_and_editable_text_without_cache_resources(self):
        plan = start.make_plan(self.source, self.media, 'example')
        self.assertEqual(plan['canvas'], {'width': 1080, 'height': 1920, 'fps': 30})
        self.assertEqual([t['type'] for t in plan['tracks']], ['video', 'text'])
        self.assertEqual(plan['tracks'][0]['segments'][0]['source'], str(self.source))
        self.assertTrue(all(t['segments'][0]['duration_us'] == 2000000 for t in plan['tracks']))

    def test_unsupported_media_is_rejected_not_transcoded(self):
        for change in ({'codec_name': 'hevc'}, {'pix_fmt': 'yuv420p10le'},
                       {'duration': '1.9'}, {'duration': 'NaN'}, {'duration': 'inf'}, {'width': 1079},
                       {'width': 9000}, {'tags': {'rotate': '90'}},
                       {'side_data_list': [{'rotation': -90}]}):
            data = deepcopy(self.media)
            data['streams'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                start.make_plan(self.source, data, 'example')

    def test_multiple_video_streams_rejected(self):
        self.media['streams'].append(deepcopy(self.media['streams'][0]))
        with self.assertRaises(ValueError):
            start.make_plan(self.source, self.media, 'example')

    def test_failed_doctor_does_not_create_output(self):
        def failed(command, timeout=60):
            return subprocess.CompletedProcess(command, 1, '', 'component missing')
        with patch.object(start, 'ROOT', self.folder), patch.object(start, 'run', side_effect=failed):
            with self.assertRaisesRegex(ValueError, 'doctor'):
                start.build(str(self.source))
        self.assertFalse((self.folder / 'work').exists())

    def test_build_never_publishes_or_exports_and_repeat_is_unique(self):
        calls = []
        def success(command, timeout=60):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0,
                json.dumps(self.media) if command[0] == 'ffprobe' else '{}', '')
        with patch.object(start, 'ROOT', self.folder), patch.object(start, 'run', side_effect=success), contextlib.redirect_stdout(io.StringIO()):
            start.build(str(self.source))
            start.build(str(self.source))
        jobs = list((self.folder / 'work').iterdir())
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all('publish' not in c and 'export' not in c and 'create' not in c for c in calls))
        for job in jobs:
            result = json.loads((job / 'next-steps.json').read_text())
            self.assertFalse(result['draft_registered'])
            self.assertFalse(result['video_exported'])
            self.assertEqual(start.shlex.split(result['commands']['publish'])[2], 'publish')
            self.assertIn(result['commands']['export'], (job / 'next-steps.md').read_text())
        self.assertEqual(self.source.read_bytes(), b'unit test bytes')


if __name__ == '__main__':
    unittest.main()
