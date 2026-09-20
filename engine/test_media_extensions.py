"""Current-native photo/GIF behavior and xattr safety regression checks."""
import argparse
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import jy14_headless as j


class MediaExtensionTests(unittest.TestCase):
    def setUp(self):
        self.plan = j.read_json(FIXTURE / 'photo-gif-plan.json')

    def test_photo_and_transparency_remain_original_pngs(self):
        assets, span, _ = j.validate_plan(self.plan)
        self.assertEqual(span, 5000000)
        self.assertEqual([a['media_type'] for a in assets.values()], ['gif', 'photo', 'photo'])
        self.assertTrue(all(not a['has_audio'] for a in assets.values()))
        self.assertEqual(assets[str(FIXTURE / 'assets/alpha.png')]['codec'], 'png')

    def test_variable_gif_uses_packet_duration_not_frame_average(self):
        data = j.probe(FIXTURE / 'assets/variable-delay.gif')
        self.assertEqual(data['duration_us'], 500000)
        self.assertEqual(data['media_type'], 'gif')

    def test_gif_rejects_unverified_loop_extension(self):
        self.plan['tracks'][0]['segments'][0]['source_start_us'] = 500000
        with self.assertRaisesRegex(ValueError, 'trim exceeds'):
            j.validate_plan(self.plan)

    def test_photo_rejects_meaningless_speed_and_offset(self):
        for field, value in [('speed', 2), ('source_start_us', 10)]:
            plan = deepcopy(self.plan)
            plan['tracks'][0]['segments'][1][field] = value
            with self.assertRaisesRegex(ValueError, 'Photos do not support'):
                j.validate_plan(plan)

    def test_native_photo_material_and_library_durations_are_distinct(self):
        assets, _, font_assets = j.validate_plan(self.plan)
        for i, asset in enumerate(assets.values()):
            asset.update(relative='Resources/' + str(i), local_id=j.identifier())
        timeline, _ = j.timeline_for(self.plan, assets, WORK / 'draft', j.identifier(), j.blueprint(), font_assets)
        self.assertEqual([m['type'] for m in timeline['materials']['videos']], ['gif', 'photo', 'photo'])
        self.assertEqual([m['duration'] for m in timeline['materials']['videos']], [10800000000] * 3)
        self.assertEqual([j.library_record(a, WORK / 'draft', 0)['duration'] for a in assets.values()],
                         [2000000, 5000000, 5000000])
        self.assertTrue(all(j.library_record(a, WORK / 'draft', 0)['roughcut_time_range']['start'] == -1
                            for a in assets.values()))

    def test_quarantine_loss_refused_before_commit(self):
        audit = WORK / 'xattr-security-test'
        audit.mkdir()
        attrs = {'com.apple.quarantine': b'required', 'user.test': b'preserved'}
        with patch.object(j.subprocess, 'run'), patch.object(j, 'read_xattrs', return_value={'user.test': b'preserved'}):
            with self.assertRaisesRegex(ValueError, 'before commit'):
                j.copy_xattrs(attrs, audit / 'staged', audit)

    def test_os_provenance_change_is_recorded_not_removed(self):
        audit = WORK / 'xattr-provenance-test'
        audit.mkdir()
        attrs = {'com.apple.provenance': b'old', 'com.apple.quarantine': b'preserved'}
        copied = dict(attrs, **{'com.apple.provenance': b'new'})
        with patch.object(j.subprocess, 'run') as command, patch.object(j, 'read_xattrs', return_value=copied):
            got, changed = j.copy_xattrs(attrs, audit / 'staged', audit)
            self.assertEqual(got, copied)
            self.assertEqual(changed, ['com.apple.provenance'])
            self.assertTrue(all(c.args[0][1] == '-wx' for c in command.call_args_list))

    def test_com_apple_macl_difference_is_kernel_owned(self):
        # Issue #5: when the publish target lives inside a TCC-protected
        # directory the kernel rewrites com.apple.macl on every inode, and
        # the attribute is SIP-protected so user space cannot preserve it.
        # This is the same class of OS-owned bookkeeping as provenance and
        # must be tolerated by copy_xattrs; the diff is recorded in
        # os_attribute_changes, not used to deny the publish.
        audit = WORK / 'xattr-macl-test'
        audit.mkdir()
        attrs = {'com.apple.macl': b'old-uuid-list', 'com.apple.quarantine': b'preserved'}
        copied = dict(attrs, **{'com.apple.macl': b'kernel-appended-uuid'})
        with patch.object(j.subprocess, 'run') as command, \
                patch.object(j, 'read_xattrs', return_value=copied):
            got, changed = j.copy_xattrs(attrs, audit / 'staged', audit)
            self.assertEqual(got, copied)
            self.assertEqual(changed, ['com.apple.macl'])
            # All xattr writes went through -wx; no -d (stripping) was used.
            self.assertTrue(all(c.args[0][1] == '-wx' for c in command.call_args_list))

    def test_macl_and_provenance_both_tolerated(self):
        # The two known OS-owned bookkeeping attributes can differ
        # simultaneously; both are reported, both are tolerated.
        audit = WORK / 'xattr-both-test'
        audit.mkdir()
        attrs = {'com.apple.macl': b'a', 'com.apple.provenance': b'old', 'com.apple.quarantine': b'q'}
        copied = {'com.apple.macl': b'a-prime', 'com.apple.provenance': b'new', 'com.apple.quarantine': b'q'}
        with patch.object(j.subprocess, 'run'), patch.object(j, 'read_xattrs', return_value=copied):
            got, changed = j.copy_xattrs(attrs, audit / 'staged', audit)
            self.assertEqual(sorted(changed), ['com.apple.macl', 'com.apple.provenance'])

    def test_unknown_xattr_diff_still_rejected(self):
        # The OS_OWNED_XATTRS allow-list is closed: any other changed
        # attribute -- even alongside the allowed ones -- still aborts.
        audit = WORK / 'xattr-unknown-test'
        audit.mkdir()
        attrs = {'com.apple.macl': b'a', 'user.custom': b'old'}
        copied = {'com.apple.macl': b'a-prime', 'user.custom': b'new'}
        with patch.object(j.subprocess, 'run'), patch.object(j, 'read_xattrs', return_value=copied):
            with self.assertRaisesRegex(ValueError, 'before commit'):
                j.copy_xattrs(attrs, audit / 'staged', audit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    args = parser.parse_args()
    WORK, FIXTURE = args.work.resolve(), args.fixture.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MediaExtensionTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(),
                                 'live_written': False, 'video_transcoded': False})
    raise SystemExit(not result.wasSuccessful())
