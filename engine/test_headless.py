"""Offline behavioral checks; writes only retained fixtures under --work."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jy14_headless as j


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        self.plan = j.read_json(FIXTURE / 'plan.json')

    def invalid(self, change):
        change(self.plan)
        with self.assertRaises((ValueError, FileNotFoundError)):
            j.validate_plan(self.plan)

    def test_real_four_local_assets(self):
        assets, duration, _ = j.validate_plan(self.plan)
        self.assertEqual(len(assets), 4)
        self.assertEqual(duration, 6000000)
        self.assertEqual({a['kind'] for a in assets.values()}, {'video', 'audio'})

    def test_reject_parent_target(self):
        self.invalid(lambda p: p.update(name='../escaped'))

    def test_reject_unknown_operation(self):
        self.invalid(lambda p: p['tracks'][0]['segments'][0].update(transition='unknown'))

    def test_reject_main_gap(self):
        self.invalid(lambda p: p['tracks'][0]['segments'][1].update(start_us=2500000))

    def test_reject_overlap(self):
        self.invalid(lambda p: p['tracks'][2]['segments'][1].update(start_us=1000000))

    def test_reject_trim_past_end(self):
        self.invalid(lambda p: p['tracks'][0]['segments'][0].update(source_start_us=9000000))

    def test_reject_speed_duration_mismatch(self):
        self.invalid(lambda p: p['tracks'][0]['segments'][0].update(speed=1.5))

    def test_reject_audio_on_video_track(self):
        self.invalid(lambda p: p['tracks'][0]['segments'][0].update(source=p['tracks'][4]['segments'][0]['source']))

    def test_reject_tail_beyond_main(self):
        self.invalid(lambda p: p['tracks'][3]['segments'][0].update(duration_us=7000000))

    def test_reject_nonfinite_and_boolean(self):
        self.invalid(lambda p: p['tracks'][0]['segments'][0].update(volume=float('nan')))
        self.plan = j.read_json(FIXTURE / 'plan.json')
        self.invalid(lambda p: p['tracks'][0]['segments'][0].update(duration_us=True))

    def test_blank_is_supported(self):
        self.plan['tracks'] = []
        self.assertEqual(j.validate_plan(self.plan), ({}, 0, {}))

    def test_unicode_text_uses_utf16_ranges(self):
        material = deepcopy(j.blueprint()['text']['materials'][0][1])
        j.text_material(material, {'text': '字幕 🐱', 'color': '#FF0000', 'size': 8})
        text = json.loads(material['content'])
        self.assertEqual(text['styles'][0]['range'], [0, 5])
        self.assertEqual(text['styles'][0]['fill']['content']['solid']['color'], [1, 0, 0])

    def test_unique_ids_and_same_source_dedup(self):
        assets, _, font_assets = j.validate_plan(self.plan)
        for n, a in enumerate(assets.values()):
            a.update(relative='Resources/' + str(n), local_id=j.identifier())
        target = j.nd.DRAFT_ROOT / self.plan['name']
        timeline, records = j.timeline_for(self.plan, assets, target, j.identifier(), j.blueprint(), font_assets)
        ids = [m['id'] for mats in timeline['materials'].values() for m in mats]
        ids += [t['id'] for t in timeline['tracks']]
        ids += [s['id'] for t in timeline['tracks'] for s in t['segments']]
        self.assertEqual(len(ids), len(set(ids)))
        video = {m['id']: m for m in timeline['materials']['videos']}
        # Match the fixture's actual sources, including a main track that mixes
        # two files. Equal sources share a library identity, not a segment ID.
        seen = {}
        for planned, actual in zip(self.plan['tracks'], timeline['tracks']):
            if planned['type'] != 'video':
                continue
            for before, after in zip(planned['segments'], actual['segments']):
                local_id = video[after['material_id']]['local_material_id']
                self.assertEqual(local_id, assets[before['source']]['local_id'])
                self.assertEqual(seen.setdefault(before['source'], local_id), local_id)
        self.assertEqual(len(set(seen.values())), len(seen))
        self.assertEqual(len(records), 6)

    def test_actual_encrypted_build_matches_plan(self):
        result = j.verify_build(FIXTURE / 'build')
        self.assertFalse(result['ui_preparation_used'])
        self.assertEqual(result['duration_us'], 6000000)

    def test_tampered_cipher_refused_before_publish(self):
        dest = WORK / 'tampered-build'
        shutil.copytree(FIXTURE / 'build', dest)
        p = dest / 'draft/draft_info.json'
        p.write_bytes(p.read_bytes() + b'x')
        with self.assertRaisesRegex(ValueError, 'Built draft changed'):
            j.publish(dest, WORK / 'must-not-create')
        self.assertFalse((WORK / 'must-not-create').exists())

    def test_symlink_in_build_refused(self):
        dest = WORK / 'symlink-tree'
        dest.mkdir()
        (dest / 'outside').symlink_to(FIXTURE / 'plan.json')
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            j.files_manifest(dest)

    def test_duplicate_json_key_is_rejected(self):
        path = WORK / 'duplicate.json'
        j.write(path, b'{"name":"a","name":"b"}')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
            j.read_json(path)

    def test_native_saved_resource_paths(self):
        target = WORK / 'draft'
        expected = (target / 'Resources/a.mp4').resolve()
        self.assertEqual(j.native_media_path(j.DRAFT_PATH_TOKEN + 'Resources/a.mp4', target), expected)
        self.assertEqual(j.native_media_path('./Resources/a.mp4', target), expected)
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            j.native_media_path('##_draftpath_placeholder_B_##/Resources/a.mp4', target)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            j.native_media_path(j.DRAFT_PATH_TOKEN + '../a.mp4', target)

    def test_audio_rejects_ignored_visual_options(self):
        self.invalid(lambda p: p['tracks'][4]['segments'][0].update(rotation=30))

    def test_changed_style_and_canvas_are_detected(self):
        record = j.verify_build(FIXTURE / 'build')
        h = j.nd.helper()
        timeline = h._decrypt_metadata_in_memory(FIXTURE / 'build/draft/draft_info.json')
        metadata = h._decrypt_metadata_in_memory(FIXTURE / 'build/draft/draft_meta_info.json')
        target = Path(record['target'])
        bad = deepcopy(timeline)
        bad['tracks'][1]['segments'][0]['clip']['scale']['x'] += .1
        with self.assertRaisesRegex(ValueError, 'Scale changed'):
            j.verify_structure(bad, metadata, self.plan, record['assets'], target)
        bad = deepcopy(timeline)
        bad['canvas_config']['width'] += 10
        with self.assertRaisesRegex(ValueError, 'Canvas changed'):
            j.verify_structure(bad, metadata, self.plan, record['assets'], target)
        bad = deepcopy(timeline)
        mat = bad['materials']['texts'][0]
        content = json.loads(mat['content'])
        content['styles'][0]['fill']['content']['solid']['color'] = [1, 0, 0]
        mat['content'] = json.dumps(content)
        with self.assertRaisesRegex(ValueError, 'Text color changed'):
            j.verify_structure(bad, metadata, self.plan, record['assets'], target)

    def test_unknown_app_version_fails_closed(self):
        with patch.object(j.nd.plistlib, 'loads', return_value={
                'CFBundleShortVersionString': '11.4.3', 'CFBundleVersion': '11.4.3',
                'CFBundleIdentifier': 'com.lemon.lvpro'}):
            with self.assertRaisesRegex(ValueError, 'Unsupported Jianying'):
                j.nd.doctor()

    def test_known_cached_sound_checked_by_bytes(self):
        sound = j.cached_sound('啵1')
        self.assertEqual(sound['size'], 5600)
        self.assertEqual(sound['kind'], 'audio')
        with patch.dict(j.CACHED_SOUNDS['啵1'], sha256='0' * 64):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                j.cached_sound('啵1')
        with self.assertRaisesRegex(ValueError, 'Unknown cached sound'):
            j.cached_sound('unregistered-resource')

    def test_compiled_cached_sounds_need_explicit_conversion_and_keep_overlaps(self):
        source = self.plan['tracks'][0]['segments'][0]['source']
        compiled = {'schema': 'jianying-compiled-plan/v1', 'source': source,
                    'source_sha256': j.nd.digest(source), 'source_duration_us': 8000000,
                    'fps': 30, 'speed': 1, 'voice_volume': 1, 'duration_us': 2000000, 'frames': 60,
                    'ranges': [{'source_start_us': 0, 'source_duration_us': 2000000,
                                'target_start_us': 0, 'target_duration_us': 2000000, 'frames': 60}],
                    'subtitles': [{'start_us': 0, 'end_us': 2000000, 'text': '缓存音效检查'}],
                    'sfx': [{'name': '啵1', 'start_us': t, 'duration_us': 366666, 'volume': .1}
                            for t in (500000, 600000)], 'bgm': False}
        path = WORK / 'compiled-sound.json'
        j.write(path, compiled)
        with self.assertRaisesRegex(ValueError, 'explicitly import'):
            j.from_compiled(path, 'cached-sound-test', WORK / 'not-authorized.json')
        self.assertFalse((WORK / 'not-authorized.json').exists())
        result = j.from_compiled(path, 'cached-sound-test', WORK / 'cached-sound-plan.json', True)
        plan = j.read_json(WORK / 'cached-sound-plan.json')
        audio = [t for t in plan['tracks'] if t['type'] == 'audio']
        self.assertEqual(len(audio), 2)
        self.assertEqual([t['segments'][0]['start_us'] for t in audio], [500000, 600000])
        self.assertEqual(len(result['sound_display_duration_adjustments']), 2)
        self.assertFalse(result['native_library_registration_created'])
        self.assertTrue(result['cached_sounds_as_local'])
        assets, duration, _ = j.validate_plan(plan)
        self.assertEqual(len(assets), 2)
        self.assertEqual(duration, 2000000)

    def test_partial_publish_can_resume_without_overwriting(self):
        # Real encryption and IO, but only in this test's isolated synthetic root.
        root = WORK / 'isolated-projects'
        root.mkdir()
        original = {'root_path': str(root), 'draft_ids': 7,
                    'all_draft_store': [{'draft_id': 'untouched', 'draft_name': 'preserve me'}]}
        j.write(root / 'root_meta_info.json', original)
        plan_path = WORK / 'resume-plan.json'
        plan = deepcopy(self.plan)
        plan['name'] = 'isolated-resume-target'
        j.write(plan_path, plan)
        h = j.nd.helper()
        isolated = SimpleNamespace(**vars(h))
        isolated._validate_runtime_environment = j.nd.doctor
        isolated._ensure_editor_closed = lambda _: None
        out = WORK / 'resume-build'
        with patch.object(j.nd, 'DRAFT_ROOT', root), patch.object(j.nd, 'helper', return_value=isolated):
            j.build(plan_path, out)
            with patch.object(j.os, 'replace', side_effect=OSError('injected index commit failure')):
                with self.assertRaisesRegex(OSError, 'injected'):
                    j.publish(out, WORK / 'interrupted-audit')
            self.assertTrue((root / plan['name']).is_dir())
            self.assertEqual(j.read_json(root / 'root_meta_info.json'), original)
            result = j.publish(out, WORK / 'resume-audit', resume=True)
            self.assertEqual(result['status'], 'created')
            after = j.read_json(root / 'root_meta_info.json')
            self.assertEqual(after['draft_ids'], 8)
            self.assertEqual(after['all_draft_store'][1:], original['all_draft_store'])
            result = j.publish(out, WORK / 'idempotent-audit', resume=True)
            self.assertEqual(result['status'], 'already_registered')
            self.assertEqual(j.read_json(root / 'root_meta_info.json'), after)
            j.write(root / plan['name'] / 'native-user-change', b'changed')
            with self.assertRaisesRegex(ValueError, 'Resume refused'):
                j.publish(out, WORK / 'must-not-resume', resume=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--fixture', required=True, type=Path)
    args = p.parse_args()
    WORK, FIXTURE = args.work.resolve(), args.fixture.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HeadlessTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'failures': len(result.failures),
                                 'errors': len(result.errors), 'passed': result.wasSuccessful(),
                                 'live_drafts_changed': False, 'api_called': False})
    raise SystemExit(0 if result.wasSuccessful() else 1)
