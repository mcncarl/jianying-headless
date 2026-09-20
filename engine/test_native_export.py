"""Behavioral guard tests; keep all generated fixtures under the requested work path."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import native_export as e

WORK = Path(os.environ['JY_NATIVE_EXPORT_TEST_WORK']).resolve()
WORK.mkdir(parents=True, exist_ok=True)


class ExportGuards(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=WORK))
        self.folder = self.root / 'build/draft'
        self.folder.mkdir(parents=True)
        self.target = self.root / 'planned-live-draft'
        self.relative = Path('Resources/headless-media/asset.mp4')
        self.source = self.folder / self.relative
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'owned test bytes')
        self.settings = dict(width=1280, height=720, fps=25, bitrate=4_000_000, timeout_seconds=60)
        self.probe = {'format': {'duration': '6.0', 'tags': {'major_brand': 'isom'}},
                      'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1280, 'height': 720,
                                   'r_frame_rate': '25/1', 'nb_frames': '150'},
                                  {'codec_type': 'audio', 'codec_name': 'aac'}]}

    def test_native_placeholder_resolves_to_build_not_live(self):
        source, relative = e.source_in_build(e.j.DRAFT_PATH_TOKEN + str(self.relative), self.target, self.folder)
        self.assertEqual(source, self.source)
        self.assertEqual(relative, self.relative)
        self.assertFalse(self.target.exists())

    def test_external_or_traversal_dependency_rejected(self):
        for path in ('/outside/asset.mp4', '../asset.mp4', e.j.DRAFT_PATH_TOKEN + '../asset.mp4'):
            with self.assertRaises(ValueError):
                e.source_in_build(path, self.target, self.folder)

    def test_symlink_dependency_rejected(self):
        link = self.folder / 'Resources/link.mp4'
        link.symlink_to(self.source)
        with self.assertRaises(ValueError):
            e.source_in_build(str(self.target / 'Resources/link.mp4'), self.target, self.folder)

    def test_changed_dependency_is_not_staged(self):
        out = self.root / 'output'; out.mkdir()
        raw = str(self.target / self.relative)
        timeline = {'materials': {'videos': [{'path': raw}]}}
        record = {'target': str(self.target), 'files': {str(self.relative): {'size': 16, 'sha256': '0' * 64}}}
        with self.assertRaisesRegex(ValueError, 'manifest'):
            e.stage_timeline(timeline, record, self.folder, out)
        self.assertFalse((out / self.relative).exists())

    def test_stage_copies_and_rewrites_dependency_without_mutating_input(self):
        out = self.root / 'output'; out.mkdir()
        raw = str(self.target / self.relative)
        timeline = {'materials': {'videos': [{'path': raw}]}}
        record = {'target': str(self.target), 'files': {str(self.relative): {
            'size': self.source.stat().st_size, 'sha256': e.j.nd.digest(self.source)}}}
        value, files = e.stage_timeline(timeline, record, self.folder, out)
        self.assertEqual(value['materials']['videos'][0]['path'], str(out / self.relative))
        self.assertEqual(timeline['materials']['videos'][0]['path'], raw)
        self.assertEqual((out / self.relative).read_bytes(), self.source.read_bytes())
        self.assertEqual(files, record['files'])

    def test_unknown_resource_and_online_dependency_rejected(self):
        for node in ({'mystery_path': '/outside/file'}, {'resource_url': 'https://example.test/private'}):
            with self.assertRaises(ValueError):
                e.stage_timeline(node, {'target': str(self.target)}, self.folder, self.root)

    def test_serialized_text_style_path_staged_but_literal_text_unchanged(self):
        out = self.root / 'text-output'; out.mkdir()
        raw = str(self.target / self.relative)
        content = {'text': raw, 'styles': [{'effectStyle': {'id': 'test-style', 'path': raw}}]}
        timeline = {'materials': {'videos': [{'path': raw}],
                                 'texts': [{'type': 'text', 'content': json.dumps(content)}]}}
        original = deepcopy(timeline)
        record = {'target': str(self.target), 'files': {str(self.relative): {
            'size': self.source.stat().st_size, 'sha256': e.j.nd.digest(self.source)}}}
        staged, files = e.stage_timeline(timeline, record, self.folder, out)
        actual = json.loads(staged['materials']['texts'][0]['content'])
        self.assertEqual(actual['styles'][0]['effectStyle']['path'], str(out / self.relative))
        self.assertEqual(actual['text'], raw)
        self.assertEqual(timeline, original)
        self.assertEqual(files, record['files'])

    def test_serialized_text_cannot_hide_external_or_ambiguous_style_paths(self):
        contents = [
            json.dumps({'text': 'https://example.test is literal subtitle text', 'styles': [
                {'effectStyle': {'id': 'not-staged', 'path': '/outside/style'}}]}),
            json.dumps({'text': 'font', 'styles': [{'font': {'path': '/outside/font.ttf'}}]}),
            '{"text":"duplicate","styles":[],"styles":[{"effectStyle":{"path":"/outside/style"}}]}',
            'not valid native text JSON',
        ]
        for content in contents:
            with self.subTest(content=content), self.assertRaises((ValueError, RuntimeError)):
                e.stage_timeline({'materials': {'texts': [{'type': 'text', 'content': content}]}},
                                 {'target': str(self.target)}, self.folder, self.root)

    def test_quicktime_mislabeled_mp4_rejected(self):
        self.probe['format']['tags']['major_brand'] = 'qt  '
        with self.assertRaisesRegex(ValueError, 'MP4 container'):
            e.validate_probe(self.probe, self.settings, 6_000_000, True)

    def test_1150_duplicated_brand_accepted(self):
        # Jianying 11.5.0's MP4 writer writes the major_brand twice; ffprobe
        # joins them with ';' (e.g. 'isom;isom'). A repeated whitelisted brand
        # must still be recognized as a standard MP4 container.
        for brand in ('isom;isom', 'mp41;mp41', 'mp42;mp42'):
            with self.subTest(brand=brand):
                self.probe['format']['tags']['major_brand'] = brand
                value = e.validate_probe(self.probe, self.settings, 6_000_000, True)
                self.assertEqual(value['major_brand'], brand)

    def test_whitelisted_brand_combinations_accepted(self):
        # Any combination of values drawn from the standard-MP4 brand set
        # (isom, mp41, mp42) is still a standard MP4 container.
        for brand in ('isom;mp42', 'mp42;isom', 'isom;mp41;mp42'):
            with self.subTest(brand=brand):
                self.probe['format']['tags']['major_brand'] = brand
                value = e.validate_probe(self.probe, self.settings, 6_000_000, True)
                self.assertEqual(value['major_brand'], brand)

    def test_mixed_brand_with_unknown_value_rejected(self):
        # Adding any non-whitelisted brand (even alongside whitelisted ones)
        # must still be rejected: do not silently accept a forged container.
        for brand in ('isom;malicious', 'malicious;isom', 'qt  ;isom', 'mp42;'):
            with self.subTest(brand=brand):
                self.probe['format']['tags']['major_brand'] = brand
                with self.assertRaisesRegex(ValueError, 'MP4 container'):
                    e.validate_probe(self.probe, self.settings, 6_000_000, True)

    def test_truncated_video_rejected(self):
        self.probe['streams'][0]['nb_frames'] = '75'
        with self.assertRaisesRegex(ValueError, 'frame count'):
            e.validate_probe(self.probe, self.settings, 6_000_000, True)

    def test_missing_audio_rejected(self):
        self.probe['streams'].pop()
        with self.assertRaisesRegex(ValueError, 'audio stream'):
            e.validate_probe(self.probe, self.settings, 6_000_000, True)

    def test_wrong_canvas_and_fps_rejected(self):
        for field, value in (('width', 1920), ('r_frame_rate', '30/1')):
            bad = deepcopy(self.probe)
            bad['streams'][0][field] = value
            with self.assertRaises(ValueError):
                e.validate_probe(bad, self.settings, 6_000_000, True)

    def test_matching_mp4_accepted(self):
        value = e.validate_probe(self.probe, self.settings, 6_000_000, True)
        self.assertEqual(value['frames'], 150)
        self.assertEqual(value['audio_codec'], 'aac')
        self.assertEqual(value['frame_delta'], 0)

    def test_aligned_timeline_missing_or_extra_frame_rejected(self):
        for count in ('149', '151'):
            with self.subTest(count=count):
                self.probe['streams'][0]['nb_frames'] = count
                with self.assertRaisesRegex(ValueError, 'frame count'):
                    e.validate_probe(self.probe, self.settings, 6_000_000, True)

    def test_microsecond_precision_does_not_admit_a_missing_frame(self):
        self.settings['fps'] = 30
        self.probe['streams'][0]['r_frame_rate'] = '30/1'
        self.probe['format']['duration'] = '1.233333'
        self.probe['streams'][0]['nb_frames'] = '37'
        value = e.validate_probe(self.probe, self.settings, 1_233_333, True)
        self.assertEqual(value['frame_count_policy'], 'exact-aligned')
        self.probe['streams'][0]['nb_frames'] = '36'
        with self.assertRaisesRegex(ValueError, 'frame count'):
            e.validate_probe(self.probe, self.settings, 1_233_333, True)

    def test_fractional_timeline_allows_only_adjacent_frame_counts(self):
        for count in (150, 151):
            self.probe['streams'][0]['nb_frames'] = str(count)
            value = e.validate_probe(self.probe, self.settings, 6_020_000, True)
            self.assertEqual(value['accepted_frame_range'], [150, 151])
            self.assertEqual(value['frame_count_policy'], 'adjacent-fractional')
        for count in (149, 152):
            self.probe['streams'][0]['nb_frames'] = str(count)
            with self.assertRaisesRegex(ValueError, 'frame count'):
                e.validate_probe(self.probe, self.settings, 6_020_000, True)

    def test_unknown_mask_identity_is_rejected(self):
        for node in ({'id': 'circle'}, {'resource_type': 'text'},
                     {'resource_type': 'circle', 'resource_id': 'uncaptured'}):
            with self.assertRaisesRegex(ValueError, 'mask'):
                e.supported_features({'materials': {'common_mask': [node]}})

    def test_captured_static_mask_parameters_are_accepted(self):
        for shape in e.resources.SHAPES:
            node = e.motion.mask_material({'shape': shape}, self.target)
            value = e.supported_features({'materials': {'common_mask': [node]}})
            self.assertIn('six captured shapes', value['mask_export'])

    def test_mask_custom_parameters_and_invalid_values_rejected(self):
        for override in ({'text': 'not supported'}, {'feather': float('nan')}, {'invert': 1},
                         {'aspectRatio': True}, {'roundCorner': .5}):
            node = e.motion.mask_material({'shape': 'circle'}, self.target)
            node['config'].update(override)
            with self.assertRaises(ValueError):
                e.supported_features({'materials': {'common_mask': [node]}})

    def test_forged_mask_directory_is_not_staged(self):
        out = self.root / 'output'; out.mkdir()
        relative = Path('Resources/headless-native/forged')
        folder = self.folder / relative; folder.mkdir(parents=True)
        (folder / 'config.json').write_bytes(b'{}')
        node = e.motion.mask_material({'shape': 'circle'}, self.target)
        node['path'] = str(self.target / relative)
        record = {'target': str(self.target), 'files': {
            str(relative / 'config.json'): {'size': 2, 'sha256': e.j.nd.digest(folder / 'config.json')}}}
        with self.assertRaisesRegex(ValueError, 'captured native resource'):
            e.stage_timeline({'materials': {'common_mask': [node]}}, record, self.folder, out)
        self.assertFalse((out / relative).exists())

    def test_unverified_effects_and_animations_rejected(self):
        for materials in ({'video_effects': [{'id': 'effect'}]},
                          {'material_animations': [{'animations': [{'name': 'pop'}]}]},
                          {'speeds': [{'curve_speed': {'points': [1, 2]}}]},
                          {'transitions': [{'effect_id': 'uncaptured'}]}):
            with self.assertRaises(ValueError):
                e.supported_features({'materials': materials})

    def test_captured_light_shake_and_native_parameters_accepted(self):
        node = deepcopy(e.resources.definition('effect/light-shake')['material'])
        node['id'] = 'captured-effect'
        node['adjust_params'][0]['value'] = 0
        node['adjust_params'][1]['value'] = 1
        value = e.supported_features({'materials': {'video_effects': [node]},
            'tracks': [{'type': 'effect', 'segments': [{'material_id': node['id']}]}]})
        self.assertEqual([w['code'] for w in value['warnings']], ['native-resource-use-limits'])

    def test_effect_identity_parameters_and_binding_cannot_be_forged(self):
        original = deepcopy(e.resources.definition('effect/light-shake')['material'])
        original['id'] = 'captured-effect'
        for change in ({'effect_id': 'uncaptured'}, {'resource_id': 'uncaptured'}, {'apply_target_type': 0},
                       {'value': .5}, {'value': True}, {'adjust_params': []},
                       {'adjust_params': [{'name': 'effects_adjust_range', 'value': float('nan')},
                                          {'name': 'effects_adjust_speed', 'value': .5}]}):
            with self.assertRaises(ValueError):
                e.supported_features({'materials': {'video_effects': [{**original, **change}]}})
        for segment in ({'material_id': 'different'},
                        {'material_id': original['id'], 'common_keyframes': [{'id': 'unexpected-animation'}]}):
            with self.assertRaises(ValueError):
                e.supported_features({'materials': {'video_effects': [original]},
                                      'tracks': [{'type': 'effect', 'segments': [segment]}]})
        with self.assertRaises(ValueError):
            e.supported_features({'materials': {'video_effects': [original]},
                                  'tracks': [{'type': 'video', 'segments': [{'material_id': original['id']}]}]})

    def test_retired_filter_and_flower_rejected(self):
        for kind in ('filter', 'text_effect'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'support has been removed'):
                e.supported_features({'materials': {'effects': [{'type': kind, 'id': 'retired'}]}})

    def test_orphan_flower_style_still_rejected(self):
        text = {'id': 'text', 'content': json.dumps({'styles': [{'effectStyle': {'id': 'retired'}}]})}
        with self.assertRaisesRegex(ValueError, 'no captured material binding'):
            e.supported_features({'materials': {'texts': [text]},
                                  'tracks': [{'type': 'text', 'segments': [{'material_id': 'text'}]}]})

    def test_retired_effects_fail_before_export_job_creation(self):
        out = self.root / 'must-not-export'
        timeline = {'materials': {'effects': [{'type': 'filter', 'id': 'retired'}]}}
        with patch.object(e, 'verified_build', return_value=(self.folder, {}, timeline)):
            with self.assertRaisesRegex(ValueError, 'support has been removed'):
                e.run(self.folder, out)
        self.assertFalse(out.exists())

    def test_changed_light_shake_resource_is_not_staged(self):
        out = self.root / 'output'; out.mkdir()
        relative = Path('Resources/headless-native/forged-effect')
        folder = self.folder / relative; folder.mkdir(parents=True)
        (folder / 'config.json').write_bytes(b'{}')
        node = deepcopy(e.resources.definition('effect/light-shake')['material'])
        node['path'] = str(self.target / relative)
        record = {'target': str(self.target), 'files': {
            str(relative / 'config.json'): {'size': 2, 'sha256': e.j.nd.digest(folder / 'config.json')}}}
        with self.assertRaisesRegex(ValueError, 'captured native resource'):
            e.stage_timeline({'materials': {'video_effects': [node]}}, record, self.folder, out)
        self.assertFalse((out / relative).exists())

    def test_known_dissolve_and_empty_native_defaults_accepted(self):
        value = e.supported_features({'materials': {
            'common_mask': [], 'video_effects': [], 'material_animations': [{'animations': []}],
            'transitions': [{'effect_id': '6724845717472416269'}]}, 'tracks': [{'type': 'video'}]})
        self.assertIn('requires viewing', value['visual_acceptance'])
        self.assertEqual([w['code'] for w in value['warnings']], ['native-dissolve-audio-overlap'])
        self.assertEqual(e.supported_features({'materials': {}, 'tracks': []})['warnings'], [])

    def test_invalid_settings_rejected(self):
        timeline = {'canvas_config': {'width': 1280, 'height': 721}, 'fps': 25}
        with self.assertRaises(ValueError):
            e.settings_for(timeline, 4_000_000, 60)
        timeline['canvas_config']['height'] = 720
        for bitrate, timeout in ((True, 60), (0, 60), (4_000_000, 0)):
            with self.assertRaises(ValueError):
                e.settings_for(timeline, bitrate, timeout)

    def test_sandbox_allows_only_owned_user_data_and_no_external_writes(self):
        job = self.root / 'job'; job.mkdir()
        profile = job / 'test.sb'; profile.write_bytes(e.sandbox_profile(job))
        allowed = job / 'allowed.txt'; allowed.write_text('fixture')
        denied = self.root / 'denied.txt'; denied.write_text('fixture')
        permitted = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), '/bin/cat', str(allowed)], capture_output=True)
        forbidden = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), '/bin/cat', str(denied)], capture_output=True)
        write = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), '/usr/bin/touch', str(self.root / 'forbidden-write')], capture_output=True)
        self.assertEqual(permitted.returncode, 0)
        self.assertNotEqual(forbidden.returncode, 0)
        self.assertNotEqual(write.returncode, 0)
        self.assertFalse((self.root / 'forbidden-write').exists())

    def test_sandbox_profile_writes_non_ascii_path_as_utf8_literal(self):
        # sandbox-exec does not interpret JSON-style \uXXXX escapes. The
        # profile must carry the path as a raw UTF-8 string literal so that
        # subpath matches the real on-disk directory.
        import re as _re
        chinese = self.root / '中文目录-工作区' / 'export-x'
        profile = e.sandbox_profile(chinese).decode('utf-8')
        literal = _re.search(r'\(allow file-read-data \(subpath (.+?)\)\)', profile).group(1)
        # The literal is the bare UTF-8 path inside SBPL quotes -- no escape sequences.
        self.assertNotIn('\\u', literal)
        self.assertIn(str(chinese), literal)

    def test_sandbox_profile_real_non_ascii_path_allows_read(self):
        # End-to-end: with a Chinese-named work directory the buggy profile
        # denied every read (issue #11); the fixed profile must permit it.
        chinese = self.root / '剪映-issue-11-test' / 'export-y'
        chinese.mkdir(parents=True)
        marker = chinese / 'timeline.json'
        marker.write_text('{"ok":true}')
        profile = chinese / 'profile.sb'
        profile.write_bytes(e.sandbox_profile(chinese))
        permitted = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile),
                                    '/bin/cat', str(marker)], capture_output=True)
        self.assertEqual(permitted.returncode, 0)
        self.assertIn('"ok":true', permitted.stdout.decode('utf-8'))

    def test_sandbox_profile_rejects_chars_that_break_sbpl_syntax(self):
        # A literal newline, double-quote, or backslash in the path would
        # break the SBPL grammar. Refuse to emit a broken profile.
        for bad in ('/tmp/has"quote', '/tmp/has\nnewline', '/tmp/has\\backslash'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, 'must not contain'):
                    e.sandbox_profile(bad)


if __name__ == '__main__':
    result = unittest.main(exit=False).result
    e.j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(),
                                   'failures': len(result.failures), 'errors': len(result.errors),
                                   'live_written': False, 'export_renderer_started': False})
    raise SystemExit(not result.wasSuccessful())
