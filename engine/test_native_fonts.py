"""Focused local-font tests; run with --font <authorized real OTF/TTF>."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys

import jy14_headless as j
import native_edit as edit
import native_fonts as fonts
import native_export as export

sys.path.insert(0, str(j.HERE.parent / 'bridge'))
import runtime_io


class LocalFontTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='font-case-', dir=WORK))
        self.target = self.folder / 'native-copy'
        plan = {'canvas': {'width': 640, 'height': 360, 'fps': 24}, 'tracks': [
            {'type': 'text', 'name': '对白', 'segments': [
                {'text': '原字幕 😀', 'start_us': 0, 'duration_us': 4000000, 'y': -.7,
                 'keyframes': {'x': [{'at_us': 0, 'value': -.1}, {'at_us': 4000000, 'value': .1}]}}]}]}
        self.timeline, _ = j.timeline_for(plan, {}, self.target, j.identifier(), j.blueprint())
        self.material = self.timeline['materials']['texts'][0]
        self.operation = {'op': 'set_text_font', 'id': self.material['id'], 'source': str(FONT)}

    def test_real_sfnt_names_and_digest(self):
        result = fonts.inspect_font(FONT)
        self.assertEqual(result['sha256'], hashlib.sha256(FONT.read_bytes()).hexdigest())
        self.assertGreater(result['glyph_count'], 1)
        self.assertTrue(result['postscript_name'])

    def test_only_font_fields_change_and_fonts_are_not_media(self):
        self.material['opaque_vendor_field'] = {'keep': ['a', 42]}
        before = deepcopy(self.timeline)
        assets, applied = edit.apply_operations(self.timeline, {}, [self.operation], self.target)
        self.assertEqual(assets, [])
        self.assertEqual(self.timeline['tracks'], before['tracks'])
        asset = applied[0]['font_asset']
        old_material = before['materials']['texts'][0]
        actual_content = json.loads(self.material['content'])
        old_content = json.loads(old_material['content'])
        for actual, prior in zip(actual_content['styles'], old_content['styles']):
            self.assertEqual({k:v for k,v in actual.items() if k != 'font'},
                             {k:v for k,v in prior.items() if k != 'font'})
            self.assertEqual(actual['font']['path'], str(self.target / asset['relative']))
            self.assertEqual(actual['font']['id'], '')
        self.assertEqual(actual_content['text'], old_content['text'])
        self.assertEqual({k:v for k,v in self.material.items() if k not in {'content','font_path'}},
                         {k:v for k,v in old_material.items() if k not in {'content','font_path'}})

    def test_inconsistent_or_mixed_font_paths_are_rejected_atomically(self):
        obj = json.loads(self.material['content'])
        obj['styles'][0]['font']['path'] = '/different/font.otf'
        self.material['content'] = json.dumps(obj)
        before = deepcopy(self.timeline)
        with self.assertRaisesRegex(ValueError, 'paths disagree'):
            edit.apply_operations(self.timeline, {}, [self.operation], self.target)
        self.assertEqual(self.timeline, before)

    def test_online_font_id_is_never_cleared(self):
        for placement in ('font_id', 'resource_id', 'style_id'):
            with self.subTest(placement=placement):
                material = deepcopy(self.material)
                if placement == 'style_id':
                    obj = json.loads(material['content'])
                    obj['styles'][0]['font']['id'] = 'online-font-identity'
                    material['content'] = json.dumps(obj)
                else:
                    material[placement] = 'online-font-identity'
                before = deepcopy(material)
                with self.assertRaisesRegex(ValueError, 'identity'):
                    fonts.set_text_font(material, FONT, self.target)
                self.assertEqual(material, before)

    def test_corrupt_font_rejected_without_material_mutation(self):
        broken = self.folder / 'broken.otf'
        # Structural damage is distinct from stale internal sfnt checksums.
        broken.write_bytes(FONT.read_bytes()[:16])
        before = deepcopy(self.material)
        with self.assertRaisesRegex(ValueError, 'Cannot parse local font'):
            fonts.set_text_font(self.material, broken, self.target)
        self.assertEqual(self.material, before)

    def test_truncated_outline_is_rejected_before_binding(self):
        data = FONT.read_bytes()
        count = struct.unpack_from('>H', data, 4)[0]
        tables = [struct.unpack_from('>4sIII', data, 12 + i * 16) for i in range(count)]
        tag, _, start, size = next(row for row in tables if row[0] in (b'glyf', b'CFF '))
        broken = self.folder / 'truncated-outline.otf'
        broken.write_bytes(data[:start + size // 2])
        before = deepcopy(self.material)
        with self.assertRaisesRegex(ValueError, 'Cannot parse local font'):
            fonts.set_text_font(self.material, broken, self.target)
        self.assertEqual(self.material, before)

    def test_system_static_fonts_use_both_entrypoints_and_staging(self):
        # Read installed files; never bundle platform fonts with the source.
        candidates = ['Monaco.ttf', 'Geneva.ttf', 'Supplemental/DIN Alternate Bold.ttf',
                      'Supplemental/DIN Condensed Bold.ttf', 'Supplemental/Apple Chancery.ttf']
        with self.built_plan(custom_font=False) as (out, plan, record, timeline, _, _):
            for index, name in enumerate(candidates):
                with self.subTest(font=name):
                    font = Path('/System/Library/Fonts') / name
                    if not font.is_file():
                        self.skipTest('System font is unavailable: ' + str(font))
                    wanted = deepcopy(plan)
                    for spec in wanted['tracks'][1]['segments']:
                        spec['font_path'] = str(font)
                    plan_path, build = self.folder / ('system-' + str(index) + '.json'), self.folder / ('system-' + str(index))
                    j.write(plan_path, wanted)
                    j.build(plan_path, build)
                    checked = j.verify_build(build)
                    actual = j.read_json(build / 'draft/draft_info.json')
                    export.stage_timeline(actual, checked, build / 'draft', build / 'export')
                    changed = deepcopy(timeline)
                    operations = [{'op': 'set_text_font', 'id': material['id'], 'source': str(font)}
                                  for material in changed['materials']['texts']]
                    _, applied = edit.apply_operations(changed, {}, operations, Path(record['target']))
                    for material, event in zip(changed['materials']['texts'], applied):
                        fonts.verify_binding(material, event['font_asset'], Path(record['target']))

    def test_variable_font_is_still_rejected(self):
        font = Path('/System/Library/Fonts/SFNS.ttf')
        if not font.is_file():
            self.skipTest('System variable-font fixture is unavailable')
        with self.assertRaisesRegex(ValueError, 'Variable fonts'):
            fonts.inspect_font(font)

    def test_missing_parser_keeps_default_and_snapshot_workflows(self):
        # -S gives this child only the standard library, even from our test venv.
        script = '''
import importlib.util, json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import test_native_fonts as fixture
import native_fonts as fonts
import native_edit as edit
import native_export as export
import jy14_headless as j
assert importlib.util.find_spec('fontTools') is None
fixture.FONT, fixture.WORK = Path(sys.argv[2]), Path(sys.argv[3])
fixture.WORK.mkdir()
case = fixture.LocalFontTests(); case.setUp()
with case.built_plan(custom_font=False) as (out, plan, record, timeline, font, result):
    j.verify_build(out)
    export.stage_timeline(timeline, record, out / 'draft', out / 'staged')
    try:
        fonts.inspect_font(font)
    except ValueError as exc:
        assert 'install requirements-fonts.txt' in str(exc)
    else:
        raise AssertionError('Missing font parser silently accepted')
case = fixture.LocalFontTests(); case.setUp()
case.test_existing_fonts_survive_unrelated_edit_and_readback()
case = fixture.LocalFontTests(); case.setUp()
with case.legacy_edit_build() as (out, record, timeline, relative):
    edit.verify_build(out); edit.verify_live(out)
out = Path(sys.argv[4]); record = j.read_json(out / 'build.json')
helper = SimpleNamespace(_decrypt_metadata_in_memory=j.read_json,
                         _parse_strict_json=fixture.runtime_io._parse_strict_json)
with patch.object(j.nd, 'DRAFT_ROOT', Path(record['target']).parent), patch.object(j.nd, 'helper', return_value=helper):
    j.verify_build(out)
    export.stage_timeline(j.read_json(out / 'draft/draft_info.json'), record, out / 'draft', out / 'without-parser')
assert 'fontTools' not in sys.modules
print('default-build, ordinary-edit, legacy-readback, font-snapshot, export: passed without fontTools')
'''
        with self.built_plan() as (out, plan, record, timeline, font, result):
            font.unlink()
        run = subprocess.run([sys.executable, '-S', '-c', script, str(j.HERE), str(FONT),
                              str(self.folder / 'stdlib-only'), str(out)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('passed without fontTools', run.stdout)

    def test_symlink_collection_and_bad_directory_are_rejected(self):
        link = self.folder / 'link.otf'; link.symlink_to(FONT)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            fonts.inspect_font(link)
        for content in (b'ttcf' + b'\0'*100, b'OTTO' + b'\xff'*100):
            bad = self.folder / 'bad.otf'; bad.write_bytes(content)
            with self.assertRaises(ValueError):
                fonts.inspect_font(bad)

    def test_build_and_live_font_checks_reject_tampering(self):
        asset = fonts.set_text_font(self.material, FONT, self.target)
        path = self.target / asset['relative']; path.parent.mkdir(parents=True); shutil.copyfile(FONT, path)
        self.assertEqual(fonts.verify_assets([asset], self.timeline, self.target, self.target), 1)
        with self.assertRaisesRegex(ValueError, 'no verified dependency'):
            fonts.verify_assets([], self.timeline, self.target, self.target)
        data = bytearray(path.read_bytes()); data[-16] ^= 0x40; path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'Copied font bytes changed'):
            fonts.verify_assets([asset], self.timeline, self.target, self.target)

    def test_native_saved_relative_and_placeholder_paths_are_equivalent(self):
        asset = fonts.set_text_font(self.material, FONT, self.target)
        expected = deepcopy(self.timeline)
        for prefix in ('./', j.DRAFT_PATH_TOKEN):
            actual = deepcopy(expected)
            material = actual['materials']['texts'][0]
            material['font_path'] = prefix + asset['relative']
            obj = json.loads(material['content'])
            for style in obj['styles']:
                style['font']['path'] = prefix + asset['relative']
            material['content'] = json.dumps(obj, ensure_ascii=True, indent=4)
            edit.preserved(fonts.normalize_paths(expected, self.target), fonts.normalize_paths(actual, self.target))

    def test_copy_rebases_existing_font_including_json_encoded_path(self):
        source = self.folder / 'old-draft'; target = self.folder / 'new-draft'
        asset = fonts.set_text_font(self.material, FONT, source)
        path = source / asset['relative']; path.parent.mkdir(parents=True); shutil.copyfile(FONT, path)
        copied = edit.rebase(deepcopy(self.timeline), source, target)
        fonts.rebase_existing(copied, source, target)
        assets = fonts.collect_edit_assets(copied, source, target)
        self.assertEqual(len(assets), 1)
        changed = copied['materials']['texts'][0]
        self.assertEqual(changed['font_path'], str(target / asset['relative']))
        self.assertEqual(json.loads(changed['content'])['styles'][0]['font']['path'], changed['font_path'])
        self.assertEqual(assets[0]['source'], str(path))

    def test_edit_collection_reads_each_existing_font_once(self):
        source = self.folder / 'old-draft'; target = self.folder / 'new-draft'
        asset = fonts.set_text_font(self.material, FONT, source)
        fonts.copy_assets([asset], source)
        self.timeline['materials']['texts'] = [deepcopy(self.material) for _ in range(121)]
        copied = edit.rebase(self.timeline, source, target)
        # Existing resources are preserved as bytes, not admitted as new fonts.
        with patch.object(fonts, 'inspect_font', side_effect=AssertionError('Reparsed existing font')), \
                patch.object(fonts.rt, 'digest', wraps=fonts.rt.digest) as digest:
            fonts.rebase_existing(copied, source, target)
            assets = fonts.collect_edit_assets(copied, source, target)
        self.assertEqual(len(assets), 1)
        self.assertEqual(digest.call_count, 1)
        self.assertEqual(assets[0]['sha256'], asset['sha256'])
        for material in copied['materials']['texts']:
            self.assertEqual(material['font_path'], str(target / asset['relative']))
            self.assertEqual(json.loads(material['content'])['styles'][0]['font']['path'], material['font_path'])

    def test_explicit_font_change_still_requires_text_styles(self):
        for styles in ([], None):
            material = deepcopy(self.material)
            obj = json.loads(material['content'])
            if styles is None:
                del obj['styles']
            else:
                obj['styles'] = styles
            material['content'] = json.dumps(obj)
            before = deepcopy(material)
            with self.assertRaisesRegex(ValueError, 'explicit text styles'):
                fonts.set_text_font(material, FONT, self.target)
            self.assertEqual(material, before)

    def test_bulk_font_changes_parse_each_source_once_per_plan(self):
        plan = {'canvas': {'width': 640, 'height': 360, 'fps': 24}, 'tracks': [
            {'type': 'text', 'segments': [{'text': 'Caption ' + str(i), 'start_us': i * 1000000,
                                         'duration_us': 1000000} for i in range(121)]}]}
        timeline, _ = j.timeline_for(plan, {}, self.target, j.identifier(), j.blueprint())
        operations = [{'op': 'set_text_font', 'id': m['id'], 'source': str(FONT)}
                      for m in timeline['materials']['texts']]
        with patch.object(fonts, 'inspect_font', wraps=fonts.inspect_font) as inspect:
            for _ in range(2):
                edited = deepcopy(timeline)
                _, applied = edit.apply_operations(edited, {}, operations, self.target)
                for material, event in zip(edited['materials']['texts'], applied):
                    fonts.verify_binding(material, event['font_asset'], self.target)
                self.assertEqual(edited['tracks'], timeline['tracks'])
            self.assertEqual(inspect.call_count, 2)  # Each plan owns its parsed sources.

    def test_shared_parsed_font_still_checks_each_material(self):
        invalid = deepcopy(self.material)
        invalid['id'] = j.identifier()
        invalid['font_id'] = 'registered-font'
        self.timeline['materials']['texts'].append(invalid)
        before = deepcopy(invalid)
        operations = [self.operation, dict(self.operation, id=invalid['id'])]
        with patch.object(fonts, 'inspect_font', wraps=fonts.inspect_font) as inspect:
            with self.assertRaisesRegex(ValueError, 'identity'):
                edit.apply_operations(self.timeline, {}, operations, self.target)
            self.assertEqual(inspect.call_count, 1)
        self.assertEqual(invalid, before)

    @contextmanager
    def font_edit_plan(self, missing=False, nested=False):
        with self.built_plan(custom_font=False) as (out, plan, record, timeline, font_source, result):
            source = Path(record['target'])
            shutil.copytree(out / 'draft', source)
            relative = 'original-fonts/existing' + FONT.suffix
            old_font = source / relative
            if not missing:
                old_font.parent.mkdir(parents=True)
                shutil.copyfile(FONT, old_font)
            texts = timeline['materials']['texts']
            for material in texts:
                material['font_path'] = str(old_font)
                obj = json.loads(material['content'])
                for style in obj['styles']:
                    style['font']['path'] = './' + relative
                material['content'] = json.dumps(obj)
            operations = [{'op': 'set_text_font', 'id': m['id'], 'source': str(font_source)} for m in texts]
            if nested:
                event = edit.compound.wrap_all(timeline, 'Nested text', source)
                for operation in operations:
                    operation['timeline_id'] = event['created_timeline_id']
                edit.compound.write_sidecars(timeline, source, source, source, edit.write_owned, edit.rebase)
            for path in edit.mirrors(source, timeline['id']):
                path.write_bytes(j.nd.packed(timeline))
            before = j.files_manifest(source)
            edit_plan = {'schema': edit.SCHEMA, 'name': 'font-replacement-copy',
                         'source': {'draft_path': str(source), 'draft_id': record['draft_id'],
                                    'timeline_sha256': before['draft_info.json']['sha256']},
                         'operations': operations}
            with patch.object(j.nd.helper(), '_ensure_editor_closed', create=True, return_value=None):
                yield edit_plan, source, relative
            self.assertEqual(j.files_manifest(source), before)

    def build_font_edit(self, plan):
        path, out = self.folder / 'edit-plan.json', self.folder / 'edited'
        j.write(path, plan)
        edit.build(path, out)
        record = edit.verify_build(out)
        return out, record, j.read_json(out / 'expected-timeline.json')

    def test_replaced_old_font_is_not_an_export_dependency(self):
        with self.font_edit_plan() as (plan, source, old_relative):
            out, record, timeline = self.build_font_edit(plan)
            self.assertEqual(len(record['font_assets']), 1)
            self.assertNotIn(old_relative, [a['relative'] for a in record['font_assets']])
            self.assertEqual((out / 'draft' / old_relative).read_bytes(), (source / old_relative).read_bytes())
            staged, files = export.stage_timeline(timeline, record, out / 'draft', out / 'staged')
            self.assertNotIn(old_relative, files)
            self.assertEqual(fonts.verify_assets(record['font_assets'], staged, out / 'staged', out / 'staged'), 1)

    def test_missing_old_font_can_be_fully_replaced(self):
        with self.font_edit_plan(missing=True) as (plan, source, old_relative):
            out, record, timeline = self.build_font_edit(plan)
            self.assertEqual(len(record['font_assets']), 1)
            self.assertNotIn(old_relative, [a['relative'] for a in record['font_assets']])
            export.stage_timeline(timeline, record, out / 'draft', out / 'staged')

    def test_still_referenced_old_font_keeps_export_restrictions(self):
        with self.font_edit_plan() as (plan, source, old_relative):
            plan['operations'] = plan['operations'][:1]
            out, record, timeline = self.build_font_edit(plan)
            self.assertEqual(len(record['font_assets']), 2)
            self.assertIn(old_relative, [a['relative'] for a in record['font_assets']])
            with self.assertRaisesRegex(ValueError, 'Export dependencies must be in Resources'):
                export.stage_timeline(timeline, record, out / 'draft', out / 'staged')

    def test_still_referenced_missing_font_is_rejected(self):
        with self.font_edit_plan(missing=True) as (plan, source, old_relative):
            plan['operations'] = plan['operations'][:1]
            with self.assertRaisesRegex(ValueError, 'Existing font file is missing or nonregular'):
                self.build_font_edit(plan)

    def test_nested_font_replacement_uses_final_dependencies(self):
        with self.font_edit_plan(nested=True) as (plan, source, old_relative):
            out, record, timeline = self.build_font_edit(plan)
            self.assertEqual(len(record['font_assets']), 1)
            staged, files = export.stage_timeline(timeline, record, out / 'draft', out / 'staged')
            self.assertNotIn(old_relative, files)
            self.assertEqual(fonts.verify_assets(record['font_assets'], staged, out / 'staged', out / 'staged'), 1)
            for owner, child in edit.compound.graph(staged):
                if owner is not None:
                    sidecar = j.read_json(Path(owner['draft_file_path']))
                    self.assertEqual(sidecar['materials']['drafts'][0]['draft'], child)

    def test_shared_parsed_source_is_rechecked_when_copied(self):
        with self.font_edit_plan() as (plan, source, old_relative):
            font_source = Path(plan['operations'][0]['source'])
            bind = fonts.bind
            def change_after_binding(material, asset, target):
                bind(material, asset, target)
                font_source.write_bytes(b'font changed after parsing')
            with patch.object(fonts, 'bind', side_effect=change_after_binding), \
                    patch.object(fonts, 'inspect_font', wraps=fonts.inspect_font) as inspect:
                with self.assertRaisesRegex(ValueError, 'Replacement bytes changed'):
                    self.build_font_edit(plan)
                self.assertEqual(inspect.call_count, 1)

    def test_existing_fonts_survive_unrelated_edit_and_readback(self):
        with self.built_plan(custom_font=False) as (out, plan, record, timeline, font_source, result):
            source = Path(record['target'])
            shutil.copytree(out / 'draft', source)
            helper = j.nd.helper()
            for case in ('empty_styles', 'missing_styles', 'mixed_fonts', 'opaque_font', 'font_outside_resources'):
                with self.subTest(case=case):
                    original = deepcopy(timeline)
                    material = original['materials']['texts'][0]
                    obj = json.loads(material['content'])
                    relative = 'Resources/existing-font.ttc'
                    if case == 'empty_styles':
                        obj['styles'] = []
                    elif case == 'missing_styles':
                        del obj['styles']
                    else:
                        if case == 'font_outside_resources':
                            relative = 'original-fonts/existing-font.ttc'
                        path = source / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        # An opaque snapshot fixture; this is not a renderable font.
                        path.write_bytes(b'existing font bytes are preserved without format conversion')
                        material['font_path'] = str(path)
                        if case == 'mixed_fonts':
                            obj['styles'].append(deepcopy(obj['styles'][0]))
                        obj['styles'][0]['font'] = {'path': str(path), 'id': 'preserved-font-id'}
                        material['font_id'] = 'preserved-font-id'
                    material['content'] = json.dumps(obj)
                    for path in edit.mirrors(source, original['id']):
                        path.write_bytes(j.nd.packed(original))
                    before = j.files_manifest(source)
                    edit_plan = {'schema': edit.SCHEMA, 'name': 'copy-' + case,
                                 'source': {'draft_path': str(source), 'draft_id': record['draft_id'],
                                            'timeline_sha256': before['draft_info.json']['sha256']},
                                 'operations': [{'op': 'rename_track', 'id': original['tracks'][0]['id'], 'name': 'renamed'}]}
                    plan_path = self.folder / (case + '.json'); j.write(plan_path, edit_plan)
                    edited = self.folder / ('edited-' + case)
                    with patch.object(helper, '_ensure_editor_closed', create=True, return_value=None), \
                            patch.object(fonts, 'inspect_font', side_effect=AssertionError('Reparsed existing font')):
                        edit.build(plan_path, edited)
                        checked = edit.verify_build(edited)
                        target = Path(checked['target'])
                        expected = j.read_json(edited / 'expected-timeline.json')
                        changed = expected['materials']['texts'][0]
                        self.assertEqual(changed.get('font_id'), material.get('font_id'))
                        if case in ('empty_styles', 'missing_styles'):
                            self.assertEqual(changed['content'], material['content'])
                        else:
                            self.assertEqual(changed['font_path'], str(target / relative))
                            styles = json.loads(changed['content'])['styles']
                            self.assertEqual(styles[0]['font'], {'path': str(target / relative), 'id': 'preserved-font-id'})
                            if case == 'mixed_fonts':
                                self.assertEqual(styles[1], obj['styles'][1])
                                staged, _ = export.stage_timeline(expected, checked, edited / 'draft', edited / 'export')
                                staged_styles = json.loads(staged['materials']['texts'][0]['content'])['styles']
                                self.assertEqual(staged_styles[0]['font']['path'], str(edited / 'export' / relative))
                                self.assertEqual(staged_styles[1], obj['styles'][1])
                        shutil.copytree(edited / 'draft', target)
                        saved = deepcopy(expected)
                        if checked['font_assets']:
                            saved_material = saved['materials']['texts'][0]
                            saved_material['font_path'] = j.DRAFT_PATH_TOKEN + relative
                            saved_content = json.loads(saved_material['content'])
                            saved_content['styles'][0]['font']['path'] = './' + relative
                            saved_material['content'] = json.dumps(saved_content)
                        for path in edit.mirrors(target, saved['id']):
                            path.write_bytes(j.nd.packed(saved))
                        (target.parent / 'root_meta_info.json').write_bytes(j.nd.packed({'all_draft_store': [
                            {'draft_id': checked['draft_id'], 'draft_fold_path': str(target)}]}))
                        self.assertEqual(edit.verify_live(edited)['status'], 'verified')
                        if checked['font_assets']:
                            saved_content['styles'][0]['font']['id'] = 'unexpected-font-id'
                            saved_material['content'] = json.dumps(saved_content)
                            for path in edit.mirrors(target, saved['id']):
                                path.write_bytes(j.nd.packed(saved))
                            with self.assertRaisesRegex(ValueError, 'Preserved value changed'):
                                edit.verify_live(edited)
                    self.assertEqual(j.files_manifest(source), before)

    def new_plan(self):
        picture = self.folder / 'picture.png'
        picture.write_bytes(b'offline media fixture; probe is mocked')
        source = self.folder / FONT.name
        shutil.copyfile(FONT, source)
        media = {'source': str(picture), 'sha256': j.nd.digest(picture), 'size': picture.stat().st_size,
                 'duration_us': j.STILL_CAPACITY_US, 'kind': 'video', 'media_type': 'photo',
                 'width': 640, 'height': 360, 'has_audio': False}
        plan = {'schema': j.SCHEMA, 'name': 'font-plan-fixture',
                'canvas': {'width': 640, 'height': 360, 'fps': 24}, 'tracks': [
            {'type': 'video', 'segments': [{'source': str(picture), 'duration_us': 4000000}]},
            {'type': 'text', 'segments': [
                {'text': '直接指定字体 😀', 'duration_us': 2000000, 'font_path': str(source)},
                {'text': '下一条字幕', 'start_us': 2000000, 'duration_us': 2000000, 'font_path': str(source)}]}]}
        return plan, media, source

    @contextmanager
    def built_plan(self, custom_font=True):
        """Exercise the real build/verify pipeline with IO at the codec boundary mocked.

        These JSON fixtures are deliberately not native encrypted drafts or media.
        They must never be published or used as native acceptance evidence.
        """
        plan, media, source = self.new_plan()
        if not custom_font:
            for spec in plan['tracks'][1]['segments']:
                del spec['font_path']
        plan_path, out = self.folder / 'plan.json', self.folder / 'build'
        j.write(plan_path, plan)
        helper = SimpleNamespace(
            _encrypt_metadata_from_memory=lambda data, path: path.write_bytes(data),
            _decrypt_metadata_in_memory=j.read_json,
            _parse_strict_json=runtime_io._parse_strict_json)
        def cover(command, **kwargs):
            Path(command[-1]).write_bytes(b'offline cover fixture')
        with patch.object(j.nd, 'DRAFT_ROOT', self.folder / 'live-fixture'), \
                patch.object(j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.5.0'}), \
                patch.object(j.nd, 'helper', return_value=helper), \
                patch.object(j, 'probe', return_value=media), patch.object(j.subprocess, 'run', side_effect=cover):
            result = j.build(plan_path, out)
            record = j.read_json(out / 'build.json')
            timeline = j.read_json(out / 'draft/draft_info.json')
            yield out, plan, record, timeline, source, result

    def test_new_plan_font_is_bound_before_first_encode_and_parsed_once(self):
        with patch.object(fonts, 'inspect_font', wraps=fonts.inspect_font) as inspect, \
                self.built_plan() as (out, plan, record, timeline, source, result):
            self.assertEqual(inspect.call_count, 1)
            self.assertEqual(result['font_files'], 1)
            self.assertEqual(len(record['assets']), 1)
            self.assertEqual(len(record['font_assets']), 1)
            asset = record['font_assets'][0]
            self.assertEqual((out / 'draft' / asset['relative']).read_bytes(), source.read_bytes())
            self.assertEqual(len(list((out / 'draft/Resources/headless-fonts').iterdir())), 1)
            for material in timeline['materials']['texts']:
                self.assertEqual(material['font_path'], str(Path(record['target']) / asset['relative']))
                for style in json.loads(material['content'])['styles']:
                    self.assertEqual(style['font'], {'id': '', 'path': material['font_path']})
            metadata = j.read_json(out / 'draft/draft_meta_info.json')
            library = next(g['value'] for g in metadata['draft_materials'] if g['type'] == 0)
            self.assertEqual(len(library), 1)
            source.unlink()
            self.assertEqual(j.verify_build(out), record)  # No source-font dependency after build.

    def test_new_plan_accepts_font_directory_alias(self):
        plan, media, source = self.new_plan()
        directory = self.folder / 'font-directory-alias'
        directory.symlink_to(source.parent, target_is_directory=True)
        alias = directory / source.name
        self.assertFalse(alias.is_symlink())
        plan['tracks'][1]['segments'][0]['font_path'] = str(alias)
        with patch.object(self, 'new_plan', return_value=(plan, media, source)), \
                self.built_plan() as (out, _, record, timeline, _, result):
            self.assertEqual(result['font_files'], 1)
            self.assertEqual({a['source'] for a in record['font_assets']}, {str(alias), str(source)})
            self.assertEqual(len(list((out / 'draft/Resources/headless-fonts').iterdir())), 1)
            source.unlink()
            self.assertEqual(j.verify_build(out), record)
            staged, files = export.stage_timeline(timeline, record, out / 'draft', out / 'staged')
            relative = record['font_assets'][0]['relative']
            self.assertEqual(files[relative], record['files'][relative])
            self.assertEqual((out / 'staged' / relative).read_bytes(), FONT.read_bytes())
            for material in staged['materials']['texts']:
                fonts.verify_binding(material, record['font_assets'][0], out / 'staged')

    def test_font_edit_accepts_font_directory_alias(self):
        with self.font_edit_plan() as (plan, _, _):
            source = Path(plan['operations'][0]['source'])
            directory = self.folder / 'font-directory-alias'
            directory.symlink_to(source.parent, target_is_directory=True)
            alias = directory / source.name
            self.assertFalse(alias.is_symlink())
            plan['operations'][0]['source'] = str(alias)
            out, record, timeline = self.build_font_edit(plan)
            self.assertEqual(len(record['font_assets']), 1)
            source.unlink()
            self.assertEqual(edit.verify_build(out), record)
            staged, files = export.stage_timeline(timeline, record, out / 'draft', out / 'staged')
            relative = record['font_assets'][0]['relative']
            self.assertEqual(files[relative], record['files'][relative])
            self.assertEqual((out / 'staged' / relative).read_bytes(), FONT.read_bytes())
            for material in staged['materials']['texts']:
                fonts.verify_binding(material, record['font_assets'][0], out / 'staged')

    def test_new_plan_font_change_after_validation_is_rejected_when_copied(self):
        prepare = j.resources.prepare
        def change_after_validation(plan, folder, runtime):
            result = prepare(plan, folder, runtime)
            source = Path(plan['tracks'][1]['segments'][0]['font_path'])
            source.write_bytes(b'font changed after validation')
            return result
        with patch.object(j.resources, 'prepare', side_effect=change_after_validation), \
                patch.object(fonts, 'inspect_font', wraps=fonts.inspect_font) as inspect:
            with self.assertRaisesRegex(ValueError, 'Font changed while copying'):
                with self.built_plan():
                    self.fail('Changed font was accepted')
            self.assertEqual(inspect.call_count, 1)

    def test_new_plan_with_omitted_font_keeps_native_default(self):
        plan, media, _ = self.new_plan()
        for spec in plan['tracks'][1]['segments']:
            del spec['font_path']
        with patch.object(j, 'probe', return_value=media):
            j.validate_plan(plan)
        plan['tracks'] = plan['tracks'][1:]
        timeline, _ = j.timeline_for(plan, {}, self.target, j.identifier(), j.blueprint())
        self.assertEqual(fonts.collect_plan(plan), {})
        for material in timeline['materials']['texts']:
            self.assertEqual(material['font_path'], self.material['font_path'])
            self.assertEqual(json.loads(material['content'])['styles'][0]['font'],
                             json.loads(self.material['content'])['styles'][0]['font'])

    def test_legacy_build_without_font_inventory_still_verifies(self):
        with self.built_plan(custom_font=False) as (out, plan, record, timeline, source, result):
            self.assertEqual(result['font_files'], 0)
            self.assertEqual(record.pop('font_assets'), [])
            (out / 'build.json').write_bytes(j.nd.packed(record))
            self.assertEqual(j.verify_build(out), record)

    @contextmanager
    def legacy_edit_build(self):
        """Recreate the v1 record emitted by baseline bbc46d3, including local fonts.

        Construct the old record directly, without running the new font editor or
        removing its inventory. The baseline's real build/readback was checked
        separately against this relative-path, track-rename case.
        """
        with self.built_plan(custom_font=False) as (built, _, original_record, timeline, font, _):
            source = Path(original_record['target'])
            shutil.copytree(built / 'draft', source)
            relative = 'Resources/existing-font.ttf'
            shutil.copyfile(font, source / relative)
            for material in timeline['materials']['texts']:
                material['font_path'] = './' + relative
                obj = json.loads(material['content'])
                for style in obj['styles']:
                    style['font']['path'] = './' + relative
                material['content'] = json.dumps(obj)
            for path in edit.mirrors(source, timeline['id']):
                path.write_bytes(j.nd.packed(timeline))
            before = j.files_manifest(source)
            target = source.parent / 'legacy-edit-copy'
            out = self.folder / 'legacy-edit-build'
            folder = out / 'draft'
            shutil.copytree(source, folder)
            expected = edit.rebase(deepcopy(timeline), source, target)
            operation = {'op': 'rename_track', 'id': expected['tracks'][0]['id'], 'name': 'Legacy rename'}
            expected['tracks'][0]['name'] = operation['name']
            metadata = edit.rebase(j.read_json(folder / 'draft_meta_info.json'), source, target)
            metadata.update(draft_id=j.identifier(), draft_name=target.name)
            for path in edit.mirrors(folder, expected['id']):
                path.write_bytes(j.nd.packed(expected))
            (folder / 'draft_meta_info.json').write_bytes(j.nd.packed(metadata))
            plan = {'schema': edit.SCHEMA, 'name': target.name,
                    'source': {'draft_path': str(source), 'draft_id': original_record['draft_id'],
                               'timeline_sha256': before['draft_info.json']['sha256']},
                    'operations': [operation]}
            j.write(out / 'plan.json', plan)
            j.write(out / 'source-timeline.json', timeline)
            j.write(out / 'expected-timeline.json', expected)
            dependencies = [{'path': str(target / asset['relative']), 'sha256': asset['sha256'],
                             'size': asset['size']} for asset in original_record['assets']]
            record = {'schema': edit.BUILD_SCHEMA, 'runtime_manifest': j.nd.MANIFEST_SHA,
                      'runtime_profile': original_record['runtime_profile'], 'name': target.name,
                      'target': str(target), 'draft_id': metadata['draft_id'], 'timeline_id': expected['id'],
                      'project_id': j.read_json(folder / 'Timelines/project.json')['id'],
                      'duration_us': expected['duration'],
                      'source': str(source), 'source_files': before, 'files': j.files_manifest(folder),
                      'resources': {name: info['sha256'] for name, info in j.files_manifest(folder).items()
                                    if name.startswith('Resources/')},
                      'plan_sha256': j.nd.digest(out / 'plan.json'),
                      'expected_timeline_sha256': j.nd.digest(out / 'expected-timeline.json'),
                      'operations': [operation], 'new_assets': [], 'media_dependencies': dependencies,
                      'compound_sidecars': [], 'preserved_native_padding': [],
                      'unknown_fields_preserved_before_native_save': True,
                      'internal_ids_policy': 'preserved within a new project identity',
                      'private_copy_not_a_distributable_package': True}
            j.write(out / 'build.json', record)
            shutil.copytree(folder, target)
            j.write(target.parent / 'root_meta_info.json', {'all_draft_store': [
                {'draft_id': record['draft_id'], 'draft_fold_path': str(target)}]})
            yield out, record, expected, relative
            self.assertEqual(j.files_manifest(source), before)

    def test_legacy_edit_with_local_font_keeps_original_checks(self):
        with self.legacy_edit_build() as (out, record, expected, relative):
            self.assertNotIn('font_assets', record)
            self.assertEqual(edit.verify_build(out), record)
            self.assertEqual(edit.verify_live(out)['status'], 'verified')
            # The old exporter could not stage this font; compatibility does not
            # invent an inventory or relax the original resource-path checks.
            with self.assertRaisesRegex(ValueError, 'Unstaged/unsupported export resource path'):
                export.stage_timeline(expected, record, out / 'draft', out / 'staged')
            (out / 'draft' / relative).write_bytes(b'changed snapshot font')
            with self.assertRaisesRegex(ValueError, 'Edited copy changed'):
                edit.verify_build(out)
            (Path(record['target']) / relative).write_bytes(b'changed saved font')
            with self.assertRaisesRegex(ValueError, 'Copied resource changed'):
                edit.verify_live(out)

    def test_present_inventory_is_never_treated_as_a_legacy_record(self):
        with self.legacy_edit_build() as (out, record, expected, relative):
            for value in ([], None, {}, '', [{}]):
                with self.subTest(inventory=value):
                    (out / 'build.json').write_bytes(j.nd.packed(dict(record, font_assets=value)))
                    message = 'no verified dependency' if value == [] else 'Invalid font asset inventory'
                    for verify in (edit.verify_build, edit.verify_live):
                        with self.assertRaisesRegex(ValueError, message):
                            verify(out)
                    with self.assertRaisesRegex(ValueError, message):
                        export.stage_timeline(expected, dict(record, font_assets=value), out / 'draft', out / 'invalid-stage')

    def test_explicit_font_plans_cannot_lose_their_inventory(self):
        with self.built_plan() as (out, plan, record, timeline, font, _):
            record.pop('font_assets')
            (out / 'build.json').write_bytes(j.nd.packed(record))
            target = Path(record['target'])
            shutil.copytree(out / 'draft', target)
            for verify in (j.verify_build, j.verify_live):
                with self.assertRaisesRegex(ValueError, 'Font operations require a font asset inventory'):
                    verify(out)

    def test_explicit_font_edits_cannot_lose_their_inventory(self):
        with self.font_edit_plan() as (plan, source, relative):
            out, record, timeline = self.build_font_edit(plan)
            record.pop('font_assets')
            (out / 'build.json').write_bytes(j.nd.packed(record))
            target = Path(record['target'])
            shutil.copytree(out / 'draft', target)
            for verify in (edit.verify_build, edit.verify_live):
                with self.assertRaisesRegex(ValueError, 'Font operations require a font asset inventory'):
                    verify(out)
            with self.assertRaisesRegex(ValueError, 'Font operations require a font asset inventory'):
                export.stage_timeline(timeline, record, out / 'draft', out / 'missing-inventory')

    def test_invalid_explicit_font_never_falls_back(self):
        plan, media, _ = self.new_plan()
        corrupt = self.folder / 'corrupt.otf'
        corrupt.write_bytes(b'not a font' * 10)
        for value in (None, '', 42, 'relative.otf', str(self.folder / 'missing.otf'), str(corrupt)):
            with self.subTest(value=value), patch.object(j, 'probe', return_value=media):
                plan['tracks'][1]['segments'][0]['font_path'] = value
                with self.assertRaises(ValueError):
                    j.validate_plan(plan)

    def test_same_bytes_from_different_paths_share_one_copied_font(self):
        plan, _, source = self.new_plan()
        alias = self.folder / ('second' + source.suffix)
        shutil.copyfile(source, alias)
        plan['tracks'][1]['segments'][1]['font_path'] = str(alias)
        assets = fonts.collect_plan(plan)
        fonts.copy_assets(assets.values(), self.target)
        self.assertEqual(len(assets), 2)  # Both plan source bindings remain verifiable.
        self.assertEqual(len({a['relative'] for a in assets.values()}), 1)
        self.assertEqual(len(list((self.target / 'Resources/headless-fonts').iterdir())), 1)

    def test_new_plan_verification_detects_both_fields_reset_to_default(self):
        with self.built_plan() as (out, plan, record, timeline, source, result):
            changed = timeline['materials']['texts'][0]
            changed['font_path'] = self.material['font_path']
            content = json.loads(changed['content'])
            content['styles'][0]['font'] = json.loads(self.material['content'])['styles'][0]['font']
            changed['content'] = json.dumps(content)
            metadata = j.read_json(out / 'draft/draft_meta_info.json')
            with self.assertRaisesRegex(ValueError, 'Planned font binding changed'):
                j.verify_structure(timeline, metadata, plan, record['assets'], Path(record['target']),
                                   font_assets=record['font_assets'])
            with self.assertRaisesRegex(ValueError, 'no verified dependency'):
                j.verify_structure(timeline, metadata, plan, record['assets'], Path(record['target']))

    def test_new_build_stages_font_and_rich_text_without_original_font(self):
        with self.built_plan() as (out, plan, record, timeline, source, result):
            source.unlink()
            before = deepcopy(timeline)
            staged, files = export.stage_timeline(timeline, record, out / 'draft', out / 'staged')
            relative = record['font_assets'][0]['relative']
            self.assertEqual(files[relative], record['files'][relative])
            self.assertEqual(j.nd.digest(out / 'staged' / relative), record['font_assets'][0]['sha256'])
            for material in staged['materials']['texts']:
                self.assertEqual(material['font_path'], str(out / 'staged' / relative))
                content = json.loads(material['content'])
                self.assertEqual(content['styles'][0]['font']['path'], material['font_path'])
            self.assertEqual(timeline, before)
            self.assertEqual(j.verify_build(out), record)
            for index, prefix in enumerate(('./', j.DRAFT_PATH_TOKEN)):
                saved = deepcopy(timeline)
                for material in saved['materials']['texts']:
                    material['font_path'] = prefix + relative
                    content = json.loads(material['content'])
                    content['text'] = prefix + relative  # Path-shaped literal text must stay literal.
                    for style in content['styles']:
                        style['font']['path'] = prefix + relative
                    material['content'] = json.dumps(content)
                destination = out / ('saved-' + str(index))
                value, _ = export.stage_timeline(saved, record, out / 'draft', destination)
                for material in value['materials']['texts']:
                    content = json.loads(material['content'])
                    self.assertEqual(material['font_path'], str(destination / relative))
                    self.assertEqual(content['styles'][0]['font']['path'], material['font_path'])
                    self.assertEqual(content['text'], prefix + relative)

    def test_edit_build_uses_same_font_export_staging(self):
        asset = fonts.set_text_font(self.material, FONT, self.target)
        folder = self.folder / 'edited/draft'
        fonts.copy_assets([asset], folder)
        record = {'schema': edit.BUILD_SCHEMA, 'target': str(self.target),
                  'font_assets': [asset], 'files': j.files_manifest(folder)}
        parser = SimpleNamespace(_parse_strict_json=runtime_io._parse_strict_json)
        with patch.object(j.nd, 'helper', return_value=parser):
            value, files = export.stage_timeline(self.timeline, record, folder, self.folder / 'export')
        self.assertEqual(len(files), 1)
        self.assertEqual(value['materials']['texts'][0]['font_path'], str(self.folder / 'export' / asset['relative']))

    def test_export_rejects_missing_inventory_and_tampering(self):
        with self.built_plan() as (out, plan, record, timeline, source, result):
            missing = dict(record, font_assets=[])
            with self.assertRaisesRegex(ValueError, 'no verified dependency'):
                export.stage_timeline(timeline, missing, out / 'draft', out / 'missing-inventory')
            bad = deepcopy(timeline)
            bad['materials']['texts'][0]['font_path'] = self.material['font_path']
            metadata = j.read_json(out / 'draft/draft_meta_info.json')
            with self.assertRaisesRegex(ValueError, 'paths disagree'):
                j.verify_structure(bad, metadata, plan, record['assets'], Path(record['target']),
                                   font_assets=record['font_assets'])
            asset = record['font_assets'][0]
            (out / 'draft' / asset['relative']).write_bytes(b'damaged font bytes')
            with self.assertRaises(ValueError):
                export.stage_timeline(timeline, record, out / 'draft', out / 'tampered')
            self.assertFalse((out / 'tampered' / asset['relative']).exists())

    def test_new_plan_verify_live_accepts_equivalent_native_path_spellings(self):
        with self.built_plan() as (out, plan, record, timeline, source, result):
            target = Path(record['target'])
            shutil.copytree(out / 'draft', target)
            relative = record['font_assets'][0]['relative']
            for material in timeline['materials']['texts']:
                material['font_path'] = j.DRAFT_PATH_TOKEN + relative
                content = json.loads(material['content'])
                for style in content['styles']:
                    style['font']['path'] = './' + relative
                material['content'] = json.dumps(content)
            for path in edit.mirrors(target, timeline['id']):
                path.write_bytes(j.nd.packed(timeline))
            j.write(target.parent / 'root_meta_info.json', {'all_draft_store': [
                {'draft_id': record['draft_id'], 'draft_fold_path': str(target)}]})
            source.unlink()
            checked = j.verify_live(out)
            self.assertEqual(checked['font_files'], 1)
            self.assertTrue(checked['four_mirrors_equal'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--font', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    FONT, WORK = args.font.resolve(strict=True), args.work.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LocalFontTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(), 'live_written': False,
                                 'build_fixture_codec_mocked': True, 'native_renderer_started': False})
    raise SystemExit(not result.wasSuccessful())
