import argparse
from copy import deepcopy
from pathlib import Path
import unittest

import jy14_headless as j
import native_motion as motion


class KeyframeTests(unittest.TestCase):
    def setUp(self):
        self.plan = j.read_json(FIXTURE / 'keyframe-plan.json')

    def test_seven_motion_channels_on_three_media_types(self):
        assets, duration, _ = j.validate_plan(self.plan)
        self.assertEqual(duration, 4000000)
        self.assertEqual(len(assets), 3)
        self.assertEqual(sum(len(s.get('keyframes', {})) for t in self.plan['tracks'] for s in t['segments']), 7)

    def test_out_of_order_and_duplicate_times_are_rejected(self):
        spec = self.plan['tracks'][1]['segments'][0]
        for at in (0, -1, 4000001, True):
            changed = deepcopy(spec)
            changed['keyframes']['x'][1]['at_us'] = at
            with self.assertRaisesRegex(ValueError, 'ordered'):
                motion.validate(changed, 'video')

    def test_conflicting_static_value_is_rejected(self):
        spec = self.plan['tracks'][1]['segments'][0]
        spec['x'] = 0
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            j.validate_plan(self.plan)

    def test_unsupported_curves_not_silently_dropped(self):
        self.plan['tracks'][1]['segments'][0]['keyframes']['x'][0]['curve'] = 'ease'
        with self.assertRaisesRegex(ValueError, 'linear'):
            j.validate_plan(self.plan)

    def test_audio_cannot_have_visual_animation(self):
        self.plan['tracks'][3]['segments'][0]['keyframes']['x'] = [{'at_us': 0, 'value': 0}, {'at_us': 1, 'value': 1}]
        with self.assertRaisesRegex(ValueError, 'Unsupported keyframe'):
            j.validate_plan(self.plan)

    def test_trim_and_speed_mapping_fail_closed(self):
        spec = self.plan['tracks'][1]['segments'][0]
        for change in ({'source_start_us': 1000}, {'speed': 1.5}):
            with self.assertRaisesRegex(ValueError, 'time mapping'):
                motion.validate(dict(spec, **change), 'video')

    def test_initial_values_are_applied_and_ids_are_unique(self):
        spec = self.plan['tracks'][1]['segments'][0]
        segment = {'clip': {'transform': {'x': 0, 'y': 0}, 'scale': {'x': 1, 'y': 1}}}
        motion.apply(segment, {}, spec, 'video')
        self.assertEqual(segment['clip']['transform']['x'], -.55)
        self.assertEqual(segment['clip']['scale'], {'x': .25, 'y': .25})
        self.assertEqual(segment['clip']['alpha'], .55)
        ids = [node['id'] for g in segment['common_keyframes'] for node in [g] + g['keyframe_list']]
        self.assertEqual(len(ids), len(set(ids)))
        motion.verify(segment, {}, spec, 'video', 0)
        segment['common_keyframes'][0]['keyframe_list'][1]['values'][0] = 2
        with self.assertRaisesRegex(ValueError, 'value changed'):
            motion.verify(segment, {}, spec, 'video', 0)

    def test_uncaptured_mask_shapes_remain_rejected_by_public_plan(self):
        self.plan['tracks'][1]['segments'][0]['mask'] = {'shape': 'custom-unverified'}
        with self.assertRaisesRegex(ValueError, 'Unsupported geometric mask'):
            j.validate_plan(self.plan)

    def test_still_duration_audit_is_not_claimed_as_measured(self):
        asset = j.probe(FIXTURE / 'assets/still.png')
        self.assertIsNone(asset['decoded_duration_us'])
        self.assertEqual(asset['duration_basis'], 'native-still-capacity')

    def test_native_omits_enabled_uniform_scale_but_false_is_rejected(self):
        spec = self.plan['tracks'][1]['segments'][0]
        segment = {'clip': {'transform': {}, 'scale': {}}}
        motion.apply(segment, {}, spec, 'video')
        segment['uniform_scale'].pop('on')
        motion.verify(segment, {}, spec, 'video', 0)
        segment['uniform_scale']['on'] = False
        with self.assertRaisesRegex(ValueError, 'Uniform keyframe'):
            motion.verify(segment, {}, spec, 'video', 0)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    a = p.parse_args()
    FIXTURE, WORK = a.fixture.resolve(), a.work.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(KeyframeTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(), 'live_written': False})
    raise SystemExit(not result.wasSuccessful())
