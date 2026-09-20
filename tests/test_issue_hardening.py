"""Retained isolated fixtures; never modify real projects or the installed app."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for part in ('engine', 'bridge', 'tools'):
    sys.path.insert(0, str(ROOT / part))
import build_toolchain as tc
import runtime_report
import runtime_io
import jy14_headless as j
import native_edit


class ToolchainTests(unittest.TestCase):
    def test_clean_environment_does_not_inherit_injection(self):
        with patch.dict(tc.os.environ, {'SDKROOT': '/bad', 'CPATH': '/bad',
                                      'MACOSX_DEPLOYMENT_TARGET': '99', 'DYLD_INSERT_LIBRARIES': '/bad'}):
            self.assertEqual(set(tc.clean_environment('/reviewed')), {'PATH', 'LC_ALL', 'DEVELOPER_DIR'})

    def test_mismatched_first_candidate_does_not_hide_matching_xcode(self):
        paths = [Path('/new-clt'), Path('/reviewed-xcode')]
        with patch.object(tc, 'candidates', return_value=paths), patch.object(
                tc, 'inspect', side_effect=[ValueError('wrong generation'), ({}, {'matched': True})]):
            self.assertEqual(tc.select_toolchain({}), ({}, {'matched': True}))

    def test_explicit_selection_never_falls_back(self):
        self.assertEqual(tc.candidates('/explicit'), [Path('/explicit')])
        with self.assertRaises(ValueError):
            tc.candidates('relative')

    def test_command_pins_sdk_minimum_os_and_output_basename(self):
        identity = {'sdk_path': '/reviewed-sdk', 'deployment_target': '26.0'}
        command = tc.compile_command('/source.cpp', '/frameworks', '/work/jy14_codec_hardened_11_4', identity)
        self.assertIn('-mmacosx-version-min=26.0', command)
        self.assertEqual(command[command.index('-isysroot') + 1], '/reviewed-sdk')
        self.assertEqual(command[-1], '/work/jy14_codec_hardened_11_4')

    def test_exact_compiler_build_not_just_major_version(self):
        reproduction = {'compiler': 'Apple clang 21.0.0 (clang-2100.1.1.101)',
                        'macos_sdk': '26.5', 'binary_minimum_macos': '26.0'}
        for compiler, linker, accepted in [
                ('Apple clang version 21.0.0 (clang-2100.1.1.101)', '1267', True),
                ('Apple clang version 21.0.0 (clang-2100.3.34.2)', '1267', False),
                ('Apple clang version 21.0.0 (clang-2100.1.1.101)', '9999', False)]:
            with self.subTest(compiler=compiler, linker=linker), patch.object(Path, 'is_dir', return_value=True), patch.object(tc, 'run', side_effect=[compiler, '/sdk', '26.5', json.dumps({'version': linker})]):
                if accepted:
                    self.assertEqual(tc.inspect(Path('/developer'), reproduction)[1]['linker'], '1267')
                else:
                    with self.assertRaisesRegex(ValueError, 'Not the reviewed toolchain'):
                        tc.inspect(Path('/developer'), reproduction)


class RuntimeReportTests(unittest.TestCase):
    def test_missing_installation_never_leaks_path(self):
        result = runtime_report.report(Path('/private-placeholder/no-app'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('private-placeholder', json.dumps(result))
        self.assertTrue(result['read_only'])


class DefaultSpeedTests(unittest.TestCase):
    def test_only_observed_unit_speed_material_omission_is_accepted(self):
        expected = {'materials': {'speeds': [{'id': 'speed-id', 'type': 'speed', 'speed': 1}]}}
        actual = deepcopy(expected)
        del actual['materials']['speeds'][0]['speed']
        native_edit.preserved(expected, actual)
        for value in (2, .5, True):
            bad = deepcopy(expected)
            bad['materials']['speeds'][0]['speed'] = value
            with self.assertRaises(ValueError):
                native_edit.preserved(bad, actual)

    def test_other_fields_segments_and_curves_stay_strict(self):
        for expected, actual, path in [
                ({'speed': 1}, {}, '/tracks/track/segments/segment'),
                ({'speed': 1, 'type': 'speed', 'curve_speed': 'curve'},
                 {'type': 'speed', 'curve_speed': 'curve'}, '/materials/speeds/id'),
                ({'speed': 1, 'type': 'speed', 'mode': 1},
                 {'type': 'speed', 'mode': 1}, '/materials/speeds/id'),
                ({'volume': 1}, {}, '/materials/speeds/id')]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                native_edit.preserved(expected, actual, path)

class SavedPhotoCompanionTests(unittest.TestCase):
    def fixture(self):
        expected = {'id': 'timeline', 'new_version': '187.0.0',
                    'last_modified_platform': {'app_version': '11.5.0'},
                    'materials': {'videos': [{'id': 'picture', 'type': 'photo', 'has_audio': False}],
                                  'loudnesses': [{'id': 'old'}]},
                    'tracks': [{'id': 'track', 'type': 'video', 'segments': [
                        {'id': 'segment', 'material_id': 'picture', 'extra_material_refs': ['old']}]}]}
        actual = deepcopy(expected)
        actual['materials']['loudnesses'][0]['id'] = 'new'
        actual['tracks'][0]['segments'][0]['extra_material_refs'] = ['new']
        return expected, actual

    def test_reviewed_photo_defaults_are_bijective_and_nonmutating(self):
        expected, actual = self.fixture()
        before = deepcopy(actual)
        compared, changes = native_edit.normalize_saved_companions(expected, actual, 'jy14-headless-macos-11.5.0')
        self.assertEqual(compared, expected)
        self.assertEqual(actual, before)
        self.assertEqual(len(changes), 1)

    def test_other_versions_audio_video_parameters_and_shared_refs_stay_strict(self):
        for case in ('version', 'schema', 'provenance', 'audio', 'video', 'gain', 'shared'):
            expected, actual = self.fixture()
            runtime = 'jy14-headless-macos-11.5.0'
            if case == 'version':
                runtime = 'jy14-headless-macos-11.4.2'
            elif case == 'schema':
                expected['new_version'] = '185.0.0'
            elif case == 'provenance':
                actual['last_modified_platform']['app_version'] = '11.4.2'
            elif case == 'audio':
                expected['materials']['videos'][0]['has_audio'] = True
            elif case == 'video':
                expected['materials']['videos'][0]['type'] = 'video'
            elif case == 'gain':
                actual['materials']['loudnesses'][0]['gain'] = .5
            else:
                actual['tracks'][0]['segments'].append(
                    {'id': 'other', 'material_id': 'picture', 'extra_material_refs': ['new']})
            with self.subTest(case=case):
                compared, changes = native_edit.normalize_saved_companions(expected, actual, runtime)
                self.assertEqual(changes, [])
                with self.assertRaises(ValueError):
                    native_edit.preserved(expected, compared)


class SavedProvenanceTests(unittest.TestCase):
    def test_only_reviewed_device_identifiers_are_normalized(self):
        before = {'new_version': '187.0.0', 'last_modified_platform':
                  {'app_version': '11.5.0', 'hard_disk_id': 'old'}}
        after = deepcopy(before)
        after['last_modified_platform']['hard_disk_id'] = 'new'
        self.assertEqual(native_edit.normalize_saved_provenance(
            before, after, 'jy14-headless-macos-11.5.0'), ['hard_disk_id'])
        self.assertEqual(after, before)

    def test_versions_missing_values_and_content_remain_strict(self):
        for case in ('runtime', 'schema', 'app', 'missing', 'empty', 'other'):
            before = {'new_version': '187.0.0', 'last_modified_platform':
                      {'app_version': '11.5.0', 'hard_disk_id': 'old', 'os': 'mac'}}
            after = deepcopy(before)
            after['last_modified_platform']['hard_disk_id'] = 'new'
            runtime = 'jy14-headless-macos-11.5.0'
            if case == 'runtime':
                runtime = 'jy14-headless-macos-11.4.2'
            elif case == 'schema':
                after['new_version'] = '188.0.0'
            elif case == 'app':
                after['last_modified_platform']['app_version'] = '11.6.0'
            elif case == 'missing':
                del after['last_modified_platform']['hard_disk_id']
            elif case == 'empty':
                after['last_modified_platform']['hard_disk_id'] = ''
            else:
                after['last_modified_platform']['os'] = 'other'
            with self.subTest(case=case), self.assertRaises(ValueError):
                native_edit.normalize_saved_provenance(before, after, runtime)
                native_edit.preserved(before, after)


class PublishRecoveryTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / 'work/issue-hardening-tests'
        work.mkdir(parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='case-', dir=work))
        self.root = self.folder / 'projects'
        self.root.mkdir()
        self.out = self.folder / 'build'
        self.target = self.root / 'new-draft'
        j.write(self.out / 'draft/draft_meta_info.json',
                {'draft_id': 'new-id', 'draft_fold_path': str(self.target), 'draft_timeline_materials_size_': 0})
        self.original = {'root_path': str(self.root), 'draft_ids': 7,
                         'all_draft_store': [{'draft_id': 'existing', 'draft_name': 'untouched'}]}
        j.write(self.root / 'root_meta_info.json', self.original)
        self.record = {'runtime_profile': 'test-profile', 'target': str(self.target), 'draft_id': 'new-id',
                       'files': j.files_manifest(self.out / 'draft')}
        helper = SimpleNamespace(
            _validate_runtime_environment=lambda: {'runtime_profile': 'test-profile'},
            _ensure_editor_closed=lambda _: None, _decrypt_metadata_in_memory=j.read_json,
            **{name: getattr(runtime_io, name) for name in (
                '_snapshot_file', '_parse_strict_json', '_revalidate_snapshot',
                '_acquire_directory_transaction_lock', '_release_directory_transaction_lock')})
        for context in (patch.object(j.nd, 'DRAFT_ROOT', self.root),
                        patch.object(j.nd, 'helper', return_value=helper),
                        patch.object(j, 'read_xattrs', return_value={}),
                        patch.object(j, 'copy_xattrs', return_value=({}, [])),
                        patch.object(j, 'exclusive_rename', side_effect=lambda a,b: a.rename(b))):
            context.start()
            self.addCleanup(context.stop)

    def publish(self, name, resume=False, live=None):
        return j.publish(self.out, self.folder / name, resume=resume,
                         verify_build_fn=lambda _: self.record,
                         verify_live_fn=live or (lambda _: {'status': 'verified'}))

    def failure(self, name):
        return j.read_json(self.folder / name / 'failure.json')

    def test_attribute_failure_happens_before_final_draft_is_created(self):
        with patch.object(j, 'copy_xattrs', side_effect=ValueError('macl differs')):
            with self.assertRaisesRegex(ValueError, 'macl'):
                self.publish('denied')
        self.assertFalse(self.target.exists())
        self.assertEqual(j.read_json(self.root / 'root_meta_info.json'), self.original)
        self.assertEqual(self.failure('denied')['recovery'], 'publish-after-fixing-cause')
        self.assertTrue(Path(self.failure('denied')['temporary_index']).is_file())
        self.assertEqual(self.publish('retry')['status'], 'created')

    def test_commit_failure_resumes_once_and_preserves_other_entries(self):
        with patch.object(j.os, 'replace', side_effect=OSError('injected commit failure')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                self.publish('interrupted')
        self.assertTrue(self.target.is_dir())
        self.assertEqual(self.failure('interrupted')['phase'], 'draft_placed')
        self.assertEqual(j.read_json(self.root / 'root_meta_info.json'), self.original)
        self.assertEqual(self.publish('resume', True)['status'], 'created')
        after = j.read_json(self.root / 'root_meta_info.json')
        self.assertEqual(after['draft_ids'], 8)
        self.assertEqual(after['all_draft_store'][1:], self.original['all_draft_store'])
        self.assertEqual(self.publish('again', True)['status'], 'already_registered')
        self.assertEqual(j.read_json(self.root / 'root_meta_info.json'), after)

    def test_user_modified_draft_is_never_resumed(self):
        self.publish('ok')
        j.write(self.target / 'user-change', b'preserve')
        with self.assertRaisesRegex(ValueError, 'Resume refused'):
            self.publish('refused', True)

    def test_failure_after_commit_reports_write_not_rollback(self):
        def failed(_):
            raise ValueError('post-write verification failed')
        with self.assertRaisesRegex(ValueError, 'post-write'):
            self.publish('after-commit', live=failed)
        self.assertTrue(self.failure('after-commit')['index_replaced'])
        self.assertEqual(self.failure('after-commit')['recovery'], 'inspect-index-and-run-verify')
        self.assertEqual(j.read_json(self.root / 'root_meta_info.json')['draft_ids'], 8)

    def test_prepared_index_mutation_is_detected(self):
        def move_and_tamper(source, target):
            source.rename(target)
            for path in self.root.glob('.root_meta_info.headless-*.tmp'):
                path.write_bytes(b'tampered index')
        with patch.object(j, 'exclusive_rename', side_effect=move_and_tamper):
            with self.assertRaisesRegex(ValueError, 'Prepared home index'):
                self.publish('tampered')
        self.assertEqual(j.read_json(self.root / 'root_meta_info.json'), self.original)
        self.assertEqual(self.failure('tampered')['phase'], 'draft_placed')

    def test_already_registered_verification_failure_is_not_an_uncommitted_draft(self):
        self.publish('created')
        def failed(_):
            raise ValueError('registered draft verification failed')
        with self.assertRaisesRegex(ValueError, 'registered draft'):
            self.publish('registered-check', True, live=failed)
        report = self.failure('registered-check')
        self.assertTrue(report['index_already_registered'])
        self.assertFalse(report['index_replaced'])
        self.assertEqual(report['recovery'], 'inspect-index-and-run-verify')


class AttributePolicyTests(unittest.TestCase):
    def test_macl_and_quarantine_changes_remain_blocked(self):
        # com.apple.macl is kernel/TCC-owned metadata describing which app may
        # open the file. The kernel rewrites it on every inode inside a
        # TCC-protected directory and the attribute is SIP-protected, so the
        # publishing process cannot preserve it. Treated the same as
        # com.apple.provenance (issue #5): the diff is recorded in
        # os_attribute_changes, not used to abort the publish.
        audit = Path('/audit')
        with patch.object(j, 'write'), patch.object(j.subprocess, 'run'), \
                patch.object(j, 'read_xattrs', return_value={'com.apple.macl': b'changed'}):
            got, changed = j.copy_xattrs({'com.apple.macl': b'original'}, Path('/unused'), audit)
            self.assertEqual(changed, ['com.apple.macl'])
        # com.apple.quarantine is a real security attribute and must still
        # be rejected on loss: the file must remain quarantined.
        with self.subTest(name='com.apple.quarantine'), \
                patch.object(j, 'write'), patch.object(j.subprocess, 'run'), \
                patch.object(j, 'read_xattrs', return_value={}):
            with self.assertRaisesRegex(ValueError, 'com.apple.quarantine'):
                j.copy_xattrs({'com.apple.quarantine': b'original'}, Path('/unused'), audit)


if __name__ == '__main__':
    unittest.main()
