import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edit_plan = load('edit_plan_for_test', ROOT / 'skills/yichen-jianying-edit/scripts/edit_plan.py')
prepare_source = load('prepare_source_for_test', ROOT / 'skills/yichen-jianying-edit/scripts/prepare_source.py')
sys.path.insert(0, str(ROOT / 'engine'))
headless = load('jy14_headless_for_talking_head_test', ROOT / 'engine/jy14_headless.py')


class TalkingHeadWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'test-source')
        self.probe = {'format': {'duration': '10.0'}, 'streams': [
            {'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p',
             'width': 1920, 'height': 1080},
            {'codec_type': 'audio', 'codec_name': 'aac'}]}

    def tearDown(self):
        self.temp.cleanup()

    def raw_plan(self, subtitles):
        return {'schema': 'jianying-edit-plan/v1', 'source': str(self.source),
                'source_sha256': edit_plan.digest(self.source), 'source_duration': 10,
                'fps': 30, 'speed': 1.15, 'voice_volume': 1,
                'settings_confirmed': True, 'check_decisions_pending': False,
                'bgm': False, 'keeps': [{'start': 0, 'end': 5, 'protect': []}],
                'subtitles': subtitles, 'sfx': []}

    @patch.object(edit_plan.subprocess, 'check_output')
    def test_subtitles_false_compiles_without_caption_artifacts(self, check_output):
        check_output.return_value = json.dumps(self.probe).encode()
        compiled = edit_plan.compile_plan(self.raw_plan(False))
        edit_plan.validate_compiled(compiled)
        output = self.root / 'compiled'
        edit_plan.write_compiled(compiled, output)
        self.assertEqual(compiled['subtitles'], [])
        self.assertTrue((output / 'compiled.json').is_file())
        self.assertFalse((output / 'subtitles.srt').exists())
        self.assertFalse((output / 'transcript.md').exists())

    @patch.object(edit_plan.subprocess, 'check_output')
    def test_captioned_plan_keeps_existing_outputs(self, check_output):
        check_output.return_value = json.dumps(self.probe).encode()
        compiled = edit_plan.compile_plan(self.raw_plan([
            {'start': .1, 'end': 4.9, 'text': '保留字幕'}]))
        output = self.root / 'compiled-captioned'
        edit_plan.write_compiled(compiled, output)
        self.assertIn('保留字幕', (output / 'subtitles.srt').read_text())
        self.assertTrue((output / 'transcript.md').is_file())

    @patch.object(prepare_source.subprocess, 'check_output')
    def test_inspect_reports_normalization_reasons(self, check_output):
        rotated = dict(self.probe)
        rotated['streams'] = [dict(self.probe['streams'][0], codec_name='hevc',
                                   pix_fmt='yuv420p10le', side_data_list=[{'rotation': -90}]),
                              self.probe['streams'][1]]
        check_output.return_value = json.dumps(rotated).encode()
        result = prepare_source.probe(self.source)
        self.assertFalse(result['compatible'])
        self.assertEqual(len(result['normalization_reasons']), 3)

    def test_inspect_rejects_symlink(self):
        link = self.root / 'linked.mp4'
        link.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, 'not a symlink'):
            prepare_source.probe(link)

    @patch.object(prepare_source, 'probe')
    @patch.object(prepare_source.subprocess, 'run')
    def test_normalize_is_quiet_and_never_overwrites(self, run, probe):
        output = self.root / 'normalized.mp4'
        before = {'source': str(self.source), 'sha256': 'a' * 64, 'duration': 10,
                  'width': 3840, 'height': 2160, 'rotation': -90}
        after = {'source': str(output), 'sha256': 'b' * 64, 'duration': 10,
                 'width': 1080, 'height': 1920, 'compatible': True,
                 'normalization_reasons': []}
        probe.side_effect = [before, after]
        result = prepare_source.normalize(self.source, output)
        command = run.call_args.args[0]
        self.assertIn('-loglevel', command)
        self.assertEqual(command[command.index('-loglevel') + 1], 'error')
        self.assertIn('-n', command)
        self.assertEqual(result['output_sha256'], 'b' * 64)
        output.write_bytes(b'exists')
        with self.assertRaisesRegex(ValueError, 'never overwrites'):
            prepare_source.normalize(self.source, output)

    def test_from_compiled_omits_text_track_when_subtitles_are_empty(self):
        compiled = self.root / 'compiled.json'
        compiled.write_text(json.dumps({
            'source': str(self.source), 'fps': 30, 'speed': 1.15, 'voice_volume': 1,
            'ranges': [{'source_start_us': 0, 'source_duration_us': 1150000,
                        'target_start_us': 0, 'target_duration_us': 1000000}],
            'subtitles': [], 'sfx': []}))
        output = self.root / 'draft-plan.json'
        with patch.object(headless.nd, 'validate_compiled'), \
                patch.object(headless, 'probe', return_value={'width': 1080, 'height': 1920}), \
                patch.object(headless, 'validate_plan'):
            headless.from_compiled(compiled, 'no-subtitles', output)
        result = json.loads(output.read_text())
        self.assertEqual([track['type'] for track in result['tracks']], ['video'])


if __name__ == '__main__':
    unittest.main()
