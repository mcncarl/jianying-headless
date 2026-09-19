"""Transition timing, source handles and native resource integrity."""
import argparse
from copy import deepcopy
from pathlib import Path
import unittest

import jy14_headless as j
import native_effects as effects


class NativeTransitionTests(unittest.TestCase):
    def setUp(self):
        self.plan = j.read_json(FIXTURE / 'transition-plan.json')

    def test_built_dissolve_preserves_total_duration_and_native_resource(self):
        record = j.verify_build(FIXTURE / 'transition-build-v1')
        self.assertEqual(record['duration_us'], 4000000)
        self.assertEqual(len(record['native_resources']), 1)
        self.assertEqual(record['transition_audit'][0]['frames'], 12)
        self.assertEqual(record['transition_audit'][0]['repeated_edges'], [])

    def test_unavailable_transition_and_unknown_fields_are_rejected(self):
        value = self.plan['tracks'][0]['segments'][0]['transition_out']
        value['name'] = 'uncaptured-online-effect'
        with self.assertRaisesRegex(ValueError, 'no current native'):
            j.validate_plan(self.plan)
        value['name'] = 'dissolve'
        value['url'] = 'https://example.invalid'
        with self.assertRaisesRegex(ValueError, 'Unsupported transition fields'):
            j.validate_plan(self.plan)

    def test_odd_frame_and_nonframe_durations_are_rejected(self):
        for duration in (500000, 401000, True, 0, 2200000):
            self.plan['tracks'][0]['segments'][0]['transition_out']['duration_us'] = duration
            with self.assertRaises(ValueError):
                j.validate_plan(self.plan)

    def test_last_segment_cannot_have_outgoing_transition(self):
        self.plan['tracks'][0]['segments'][1]['transition_out'] = {'name': 'dissolve', 'duration_us': 400000}
        with self.assertRaisesRegex(ValueError, 'following segment'):
            j.validate_plan(self.plan)

    def test_overlay_transition_is_not_misrepresented_as_verified(self):
        track = deepcopy(self.plan['tracks'][0])
        self.plan['tracks'].insert(1, track)
        with self.assertRaisesRegex(ValueError, 'main video track'):
            j.validate_plan(self.plan)

    def test_insufficient_handles_fail_before_build(self):
        for side in ('left', 'right'):
            plan = deepcopy(self.plan)
            plan['tracks'][0]['segments'][0 if side == 'left' else 1]['source_start_us'] = 6000000 if side == 'left' else 0
            with self.assertRaisesRegex(ValueError, 'handles are insufficient'):
                j.validate_plan(plan)

    def test_repeat_edge_is_explicit_and_audited_without_changing_timeline(self):
        segments = self.plan['tracks'][0]['segments']
        segments[0]['source_start_us'] = 6000000
        segments[1]['source_start_us'] = 0
        segments[0]['transition_out']['edge_policy'] = 'repeat-edge'
        assets, duration, _ = j.validate_plan(self.plan)
        audit = effects.transition_audit(self.plan, assets)
        self.assertEqual(duration, 4000000)
        self.assertEqual(audit[0]['repeated_edges'], ['left-tail', 'right-head'])

    def test_static_photos_have_natural_hold_handles(self):
        for segment in self.plan['tracks'][0]['segments']:
            segment['source'] = str(FIXTURE / 'assets/still.png')
            segment['source_start_us'] = 0
        assets, duration, _ = j.validate_plan(self.plan)
        self.assertEqual(effects.transition_audit(self.plan, assets)[0]['repeated_edges'], [])

    def test_resource_binding_loss_and_unplanned_transition_are_detected(self):
        spec = self.plan['tracks'][0]['segments'][0]
        segment, materials = {}, {}
        target = WORK / 'draft'
        effects.apply(segment, materials, spec, target)
        node = materials['transitions'][0]
        index = {node['id']: ('transitions', node)}
        effects.verify(segment, index, spec, 0, target, j.native_media_path)
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            effects.verify(segment, index, {}, 0, target, j.native_media_path)
        node['is_overlap'] = False
        with self.assertRaisesRegex(ValueError, 'resource identity'):
            effects.verify(segment, index, spec, 0, target, j.native_media_path)

    def test_transition_duration_changes_are_detected(self):
        spec = self.plan['tracks'][0]['segments'][0]
        segment, materials = {}, {}
        target = WORK / 'draft'
        effects.apply(segment, materials, spec, target)
        node = materials['transitions'][0]
        node['duration'] = 500000
        with self.assertRaisesRegex(ValueError, 'duration changed'):
            effects.verify(segment, {node['id']: ('transitions', node)}, spec, 33334, target, j.native_media_path)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    a = p.parse_args()
    FIXTURE, WORK = a.fixture.resolve(), a.work.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeTransitionTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(), 'live_written': False})
    raise SystemExit(not result.wasSuccessful())
