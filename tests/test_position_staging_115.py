"""Offline 11.5 position/staging regression contracts; no native acceptance.

Run with ``python3 -m unittest discover -s tests -p test_position_staging_115.py -v``.
These tests call the production staging entry directly. Native acceptance is
separate from these JSON and manifest contracts.

The real strict JSON parser is used for synthetic text. Only the helper loader
is substituted to avoid its unrelated doctor/codec preflight. No renderer,
codec result, runtime fingerprint, or visual acceptance is simulated.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
sys.path.insert(0, str(ROOT / 'bridge'))

import jy14_headless as j
import native_compound as compound
import native_edit as edit
import native_export as export
import native_motion as motion
import platform_support

PROFILE_115 = 'jy14-headless-macos-11.5.0'
PROFILE_114 = 'jy14-headless-macos-11.4.2'
DURATION = 4_000_000
Y = 'KFTypePositionY'


def segment(timeline):
    return timeline['tracks'][0]['segments'][0]


def fixture(target, y=.4, start=0, kind='text'):
    spec = {'text': 'synthetic horizontal motion', 'start_us': start,
            'duration_us': DURATION, 'y': y, 'keyframes': {'x': [
                {'at_us': 0, 'value': -.7}, {'at_us': DURATION, 'value': .7}]}}
    assets = {}
    if kind == 'video':
        spec.pop('text')
        source = str(target.parent / 'opaque-source.mp4')
        spec['source'] = source
        assets[source] = {'relative': 'Resources/synthetic.mp4', 'source': source,
                          'duration_us': DURATION * 3, 'local_id': j.identifier(),
                          'sha256': '0' * 64, 'width': 128, 'height': 72, 'has_audio': False}
    plan = {'canvas': {'width': 640, 'height': 360, 'fps': 30},
            'tracks': [{'type': kind, 'name': 'synthetic', 'segments': [spec]}]}
    timeline, _ = j.timeline_for(plan, assets, target, j.identifier(), j.blueprint())
    timeline['new_version'] = '187.0.0'
    return timeline


def keyframes_by_segment(timeline):
    """Keep exact group and point IDs, also for deeper embedded descendants."""
    return {(child['id'], track['id'], item['id']): item.get('common_keyframes', [])
            for _, child in compound.graph(timeline)
            for track in child.get('tracks', [])
            for item in track.get('segments', [])}


@unittest.skipIf(sys.platform == 'win32',
                 'macOS 11.5 staging fixture uses macOS font and export paths')
class PositionStaging115Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='position-staging-115-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.target = self.base / 'target'
        self.source_folder = self.base / 'source'
        self.source_folder.mkdir()
        # A cover is an opaque copied dependency in this structural test. These
        # bytes are deliberately not claimed to be an image or exportable media.
        (self.source_folder / 'draft_cover.jpg').write_bytes(b'offline synthetic cover')
        (self.source_folder / 'Resources').mkdir()
        # Also opaque: staging tests validate copying, not media decoding.
        (self.source_folder / 'Resources/synthetic.mp4').write_bytes(b'offline synthetic video')
        self.stage_count = 0
        parser_only = SimpleNamespace(
            _parse_strict_json=platform_support.RUNTIME_IO._parse_strict_json)
        helper = patch.object(j.nd, 'helper', return_value=parser_only)
        helper.start()
        self.addCleanup(helper.stop)
        # Fail on accidental native preflight instead of requiring an installed app.
        for name in ('doctor', 'validate_runtime'):
            preflight = patch.object(j.nd, name, side_effect=AssertionError('native preflight in offline test'))
            preflight.start()
            self.addCleanup(preflight.stop)

    def prepare_compound(self, timeline, depth=1):
        for level in range(depth):
            compound.wrap_all(timeline, 'synthetic level ' + str(level + 1), self.target)
        compound.write_sidecars(timeline, self.target, self.source_folder, self.target,
                                edit.write_owned, edit.rebase)
        compound.validate(timeline, edit.basic_validation)
        compound.check_sidecars(timeline, self.target, self.source_folder, edit.preserved)
        return timeline

    def stage(self, timeline, profile=PROFILE_115):
        original = deepcopy(timeline)
        source_bytes = {str(p.relative_to(self.source_folder)): p.read_bytes()
                        for p in self.source_folder.rglob('*') if p.is_file()}
        record = {'target': str(self.target), 'runtime_profile': profile,
                  'files': j.files_manifest(self.source_folder)}
        record_before = deepcopy(record)
        self.stage_count += 1
        out = self.base / ('stage-' + str(self.stage_count))
        out.mkdir()
        staged, files = export.stage_timeline(timeline, record, self.source_folder, out)
        self.assertEqual(timeline, original, 'staging modified the source timeline')
        self.assertEqual(record, record_before, 'staging modified the source build record')
        self.assertEqual(source_bytes,
                         {str(p.relative_to(self.source_folder)): p.read_bytes()
                          for p in self.source_folder.rglob('*') if p.is_file()},
                         'staging modified the source sidecars or cover')
        self.assertEqual(files, j.files_manifest(out),
                         'returned manifest must hash the final staged bytes')
        return staged, files, out

    def assert_completed(self, original, staged):
        before = segment(original)
        after = segment(staged)
        groups = after.get('common_keyframes', [])
        ys = [group for group in groups if group['property_type'] == Y]
        self.assertEqual(len(ys), 1, '11.5 X-only motion at nonzero Y needs one constant Y group')
        self.assertEqual([group for group in groups if group['property_type'] != Y],
                         before['common_keyframes'], 'existing X groups/point IDs must remain exact')
        expected_y = before['clip']['transform']['y']
        self.assertEqual([(point['time_offset'], point['values']) for point in ys[0]['keyframe_list']],
                         [(0, [expected_y]), (before['target_timerange']['duration'], [expected_y])])
        ids = [ys[0]['id']] + [point['id'] for point in ys[0]['keyframe_list']]
        self.assertEqual(len(ids), len(set(ids)), 'the Y group and its points need distinct identities')
        self.assertTrue(all(isinstance(item, str) and item for item in ids))

    def assert_sidecars_match(self, staged, out):
        for owner, child in compound.graph(staged):
            if owner is None:
                continue
            with self.subTest(child=child['id']):
                content_path = Path(owner['draft_file_path'])
                self.assertTrue(content_path.is_relative_to(out))
                sidecar = j.read_json(content_path)
                owners = sidecar['materials']['drafts']
                self.assertEqual(len(owners), 1)
                self.assertEqual(owners[0]['id'], owner['id'])
                self.assertEqual(owners[0]['combination_id'], owner['combination_id'])
                saved = owners[0]['draft']
                self.assertEqual(keyframes_by_segment(child), keyframes_by_segment(saved),
                                 'embedded child and every sidecar copy need identical keyframes and IDs')
                # Same content/order checks as check_sidecars, excluding its
                # draft-relative config path policy (staging paths are absolute).
                compared, _ = compound.normalize_companion_ids(child, saved, nested_root=True)
                edit.preserved(compound.normalize_paths(child, out),
                               compound.normalize_paths(compared, out))
                compound.check_order(child, saved)

    def test_root_nonzero_y_is_completed_without_changing_x(self):
        for kind in ('video', 'text'):
            for y in (.4, -.4):
                with self.subTest(kind=kind, y=y):
                    original = fixture(self.target, y=y, kind=kind)
                    staged, _, _ = self.stage(original)
                    self.assert_completed(original, staged)

    def test_root_target_start_does_not_offset_relative_y_points(self):
        original = fixture(self.target, start=2_000_000)
        staged, _, _ = self.stage(original)
        self.assert_completed(original, staged)

    def test_compound_embedded_child_is_completed(self):
        original = self.prepare_compound(fixture(self.target))
        child = original['materials']['drafts'][0]['draft']
        staged, _, _ = self.stage(original)
        self.assert_completed(child, staged['materials']['drafts'][0]['draft'])

    def test_compound_sidecar_is_completed(self):
        original = self.prepare_compound(fixture(self.target))
        child = original['materials']['drafts'][0]['draft']
        staged, _, _ = self.stage(original)
        owner = staged['materials']['drafts'][0]
        saved = j.read_json(Path(owner['draft_file_path']))['materials']['drafts'][0]['draft']
        self.assert_completed(child, saved)

    def test_compound_embedded_and_sidecar_keyframes_are_identical(self):
        original = self.prepare_compound(fixture(self.target))
        staged, _, out = self.stage(original)
        self.assert_sidecars_match(staged, out)

    def test_two_levels_keep_every_sidecar_copy_identical(self):
        original = self.prepare_compound(fixture(self.target), depth=2)
        staged, _, out = self.stage(original)
        self.assertEqual(len(list(compound.graph(staged))), 3)
        self.assert_sidecars_match(staged, out)

    def test_existing_y_animation_is_unchanged(self):
        original = fixture(self.target)
        segment(original)['common_keyframes'].extend(motion.keyframe_groups({'keyframes': {'y': [
            {'at_us': 0, 'value': .4}, {'at_us': DURATION, 'value': -.3}]}}))
        staged, _, _ = self.stage(original)
        self.assertEqual(staged, original)

    def test_zero_y_is_unchanged(self):
        original = fixture(self.target, y=0)
        staged, _, _ = self.stage(original)
        self.assertEqual(staged, original)

    def test_missing_y_is_unchanged(self):
        original = fixture(self.target)
        segment(original)['clip']['transform'].pop('y')
        staged, _, _ = self.stage(original)
        self.assertEqual(staged, original)

    def test_non_x_animation_is_unchanged(self):
        original = fixture(self.target)
        segment(original)['common_keyframes'] = motion.keyframe_groups({'keyframes': {'scale': [
            {'at_us': 0, 'value': 1}, {'at_us': DURATION, 'value': .5}]}})
        staged, _, _ = self.stage(original)
        self.assertEqual(staged, original)

    def test_114_profile_does_not_apply_completion(self):
        original = fixture(self.target)
        original['new_version'] = '185.0.0'
        staged, _, _ = self.stage(original, profile=PROFILE_114)
        self.assertEqual(staged, original)

    def test_completion_is_idempotent_and_segment_ids_are_unique(self):
        original = fixture(self.target)
        another = fixture(self.target, y=-.4)
        original['tracks'].extend(another['tracks'])
        for bucket, nodes in another['materials'].items():
            original['materials'].setdefault(bucket, []).extend(nodes)
        staged, _, _ = self.stage(original)
        before = deepcopy(staged)
        self.assertEqual(export.complete_position_keyframes(staged, PROFILE_115), {})
        self.assertEqual(staged, before)
        y_ids = [node['id'] for _, _, item in compound.all_segments(staged)
                 for group in item['common_keyframes'] if group['property_type'] == Y
                 for node in [group] + group['keyframe_list']]
        self.assertEqual(len(y_ids), 6)
        self.assertEqual(len(set(y_ids)), 6)
        compound.validate(staged, edit.basic_validation)

    def test_other_tracks_are_unchanged(self):
        for kind in ('audio', 'effect', 'filter'):
            with self.subTest(kind=kind):
                original = fixture(self.target)
                # Deliberately nonvisual partial feature fixture: the helper must
                # not reinterpret X/Y-shaped data on an unrelated track type.
                original['tracks'][0]['type'] = kind
                staged, _, _ = self.stage(original)
                self.assertEqual(staged, original)

    def test_no_keyframes_is_unchanged(self):
        original = fixture(self.target)
        segment(original).pop('common_keyframes')
        staged, _, _ = self.stage(original)
        self.assertEqual(staged, original)

    def test_unknown_profile_or_new_schema_on_old_profile_is_rejected(self):
        for profile in ('jy14-headless-macos-11.5.1', 'jy14-headless-macos-11.4.0', PROFILE_114):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    self.stage(fixture(self.target), profile=profile)

    def test_114_compound_curves_are_unchanged(self):
        original = fixture(self.target)
        original['new_version'] = '185.0.0'
        self.prepare_compound(original, depth=2)
        staged, _, out = self.stage(original, profile=PROFILE_114)
        self.assertEqual(keyframes_by_segment(original), keyframes_by_segment(staged))
        self.assert_sidecars_match(staged, out)

    def test_wrapper_unknown_fields_and_config_survive(self):
        original = self.prepare_compound(fixture(self.target), depth=2)
        originals = {}
        for owner, child in compound.graph(original):
            if owner is None:
                continue
            content_path = self.source_folder / Path(owner['draft_file_path']).relative_to(self.target)
            sidecar = j.read_json(content_path)
            sidecar['unrecognized_wrapper_metadata'] = {'token': 'keep', 'number': 37}
            saved = sidecar['materials']['drafts'][0]['draft']
            saved['unrecognized_child_metadata'] = {'fps': 27, 'speed': 2.5}
            edit.write_owned(content_path, sidecar)
            config_path = self.source_folder / Path(owner['draft_config_path']).relative_to(self.target)
            config = j.read_json(config_path)
            config['unrecognized_config'] = ['preserve', 42]
            edit.write_owned(config_path, config)
            originals[child['id']] = (sidecar, config)
        staged, _, out = self.stage(original)
        self.assert_sidecars_match(staged, out)
        for owner, child in list(compound.graph(staged))[1:]:
            sidecar = j.read_json(Path(owner['draft_file_path']))
            before, config = originals[child['id']]
            self.assertEqual(sidecar['id'], before['id'])
            self.assertEqual(sidecar['tracks'], before['tracks'])
            self.assertEqual(sidecar['unrecognized_wrapper_metadata'], before['unrecognized_wrapper_metadata'])
            self.assertEqual(sidecar['materials']['drafts'][0]['draft']['unrecognized_child_metadata'],
                             {'fps': 27, 'speed': 2.5})
            got_config = j.read_json(Path(owner['draft_config_path']))
            self.assertEqual(got_config['unrecognized_config'], config['unrecognized_config'])
            self.assertEqual({k: v for k, v in got_config.items() if k != 'cover_path'},
                             {k: v for k, v in config.items() if k != 'cover_path'})

    def test_staged_file_tampering_is_rejected(self):
        original = self.prepare_compound(fixture(self.target))
        staged, files, out = self.stage(original)
        cover = Path(staged['materials']['drafts'][0]['draft_cover_path'])
        cover.write_bytes(cover.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'Staged export input changed'):
            export.verify_staged_inputs(staged, files, out)

    def test_sidecar_curve_order_is_checked_even_with_updated_hash(self):
        original = self.prepare_compound(fixture(self.target), depth=2)
        staged, files, out = self.stage(original)
        owner = staged['materials']['drafts'][0]
        content_path = Path(owner['draft_file_path'])
        sidecar = j.read_json(content_path)
        saved = sidecar['materials']['drafts'][0]['draft']
        deepest = list(compound.graph(saved))[-1][1]
        segment(deepest)['common_keyframes'].reverse()
        edit.write_owned(content_path, sidecar)
        files = j.files_manifest(out)
        with self.assertRaisesRegex(ValueError, 'keyframe content, identity or order'):
            export.verify_staged_inputs(staged, files, out)

    def test_inconsistent_source_sidecar_is_rejected_without_repairing_it(self):
        original = self.prepare_compound(fixture(self.target))
        owner = original['materials']['drafts'][0]
        content_path = self.source_folder / Path(owner['draft_file_path']).relative_to(self.target)
        sidecar = j.read_json(content_path)
        saved = sidecar['materials']['drafts'][0]['draft']
        segment(saved)['common_keyframes'].extend(motion.keyframe_groups({'keyframes': {'y': [
            {'at_us': 0, 'value': .8}, {'at_us': DURATION, 'value': .8}]}}))
        edit.write_owned(content_path, sidecar)
        before = content_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'keyframes differ before position completion'):
            self.stage(original)
        self.assertEqual(content_path.read_bytes(), before)

    def test_export_run_checks_installed_profile_before_staging(self):
        class StageReached(Exception):
            pass

        (self.source_folder / 'build.json').write_text('{}')
        for build_profile in (PROFILE_115, PROFILE_114):
            for runtime_profile in (PROFILE_115, PROFILE_114):
                with self.subTest(build=build_profile, runtime=runtime_profile):
                    timeline = fixture(self.target)
                    timeline['new_version'] = '185.0.0'
                    record = {'runtime_profile': build_profile}
                    job = self.base / 'work' / (build_profile + '-' + runtime_profile)
                    with patch.object(export, 'verified_build', return_value=(self.source_folder, record, timeline)), \
                            patch.object(j.nd, 'validate_runtime', return_value={'runtime_profile': runtime_profile}) as runtime, \
                            patch.object(j.nd, 'fresh_directory') as fresh, \
                            patch.object(export, 'stage_timeline', side_effect=StageReached) as stage:
                        if build_profile != runtime_profile:
                            with self.assertRaisesRegex(ValueError, 'Build runtime differs'):
                                export.run(self.source_folder, job)
                            stage.assert_not_called()
                            fresh.assert_not_called()
                        else:
                            fresh.side_effect = lambda path: (path.mkdir(parents=True), path)[1]
                            with self.assertRaises(StageReached):
                                export.run(self.source_folder, job)
                            stage.assert_called_once_with(timeline, record, self.source_folder / 'draft', job)
                        runtime.assert_called_once_with()

    def test_compound_manifest_matches_final_files_and_source_is_unchanged(self):
        original = self.prepare_compound(fixture(self.target), depth=2)
        _, files, _ = self.stage(original)
        self.assertEqual(len(files), 6, 'each child has content, config, and cover sidecars')


if __name__ == '__main__':
    unittest.main()
