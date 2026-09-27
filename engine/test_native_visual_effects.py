"""Current captured visual support and fail-closed retirement checks.

Portable metadata tests; no paid packages, native writes or export jobs required.
"""
from copy import deepcopy
from pathlib import Path
import unittest

import native_resources as resources
import native_visual_effects as visual
import native_export as export


class VisualEffectTests(unittest.TestCase):
    def test_catalog_contains_only_remaining_resources(self):
        keys = set(resources.catalog()['resources'])
        self.assertEqual(keys, {'mask/' + s for s in resources.SHAPES} |
                         {'transition/dissolve', 'effect/light-shake'})

    def test_retired_resource_lookup_rejected(self):
        for key in ('filter/hd-monochrome', 'text-effect/orange-outline'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'no verified local capture'):
                resources.definition(key)

    def test_retired_plans_rejected(self):
        for kind, spec in [('filter', {'name': 'hd-monochrome'}),
                           ('text', {'text_effect': {'name': 'orange-outline'}})]:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'no current native capture'):
                visual.validate(spec, kind)

    def test_plain_text_color_and_border_remain_allowed(self):
        visual.validate({'text': 'Caption', 'color': '#FFFFFF', 'border_color': '#000000'}, 'text')

    def test_light_shake_controls(self):
        visual.validate({'name': 'light-shake'}, 'effect')
        visual.validate({'name': 'light-shake', 'params': {'range': .25, 'speed': .5}}, 'effect')
        for params in ({'frequency': .5}, {'range': False}, {'speed': float('nan')}, [], {'range': 1.01}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                visual.validate({'name': 'light-shake', 'params': params}, 'effect')

    def test_light_shake_material_bindings_and_controls(self):
        target = Path('/placeholder/draft')
        spec = {'name': 'light-shake', 'duration_us': 2000000, 'params': {'range': .25, 'speed': .5}}
        segment, node = visual.overlay_segment('effect', spec, target, 1)
        index = {node['id']: ('video_effects', node)}
        bindings = visual.verify(segment, index, spec, 'effect', target, lambda value, _: Path(value).resolve())
        self.assertEqual(bindings, [{'key': 'effect/light-shake', 'location': 'draft-owned'}])
        self.assertNotIn('source_timerange', segment)
        node['adjust_params'][0]['value'] = .9
        with self.assertRaisesRegex(ValueError, 'parameter changed'):
            visual.verify(segment, index, spec, 'effect', target, lambda value, _: Path(value).resolve())

    def test_light_shake_export_retains_usage_warning(self):
        node = deepcopy(resources.definition('effect/light-shake')['material'])
        node['id'] = 'effect'
        result = export.supported_features({'materials': {'video_effects': [node]},
            'tracks': [{'type': 'effect', 'segments': [{'material_id': 'effect'}]}]})
        self.assertEqual([w['resource'] for w in result['warnings']], ['effect/light-shake'])

    def test_retired_snapshot_export_rejected(self):
        for kind in ('filter', 'text_effect'):
            with self.assertRaisesRegex(ValueError, 'support has been removed'):
                export.supported_features({'materials': {'effects': [{'type': kind}]}})

    def test_cache_integrity_still_rejects_unknown_or_changed_files(self):
        entry = resources.definition('effect/light-shake')
        resources.verify_cached_manifest(entry, entry['files'], 'Cache changed')
        actual = dict(entry['files'], **entry.get('native_generated_cache_files', {}))
        resources.verify_cached_manifest(entry, actual, 'Cache changed')
        actual['unknown.metallib'] = {'sha256': '0' * 64, 'size': 1}
        with self.assertRaisesRegex(ValueError, 'Cache changed'):
            resources.verify_cached_manifest(entry, actual, 'Cache changed')
        missing = dict(entry['files'])
        missing.pop(next(iter(missing)))
        with self.assertRaisesRegex(ValueError, 'Cache changed'):
            resources.verify_cached_manifest(entry, missing, 'Cache changed')


if __name__ == '__main__':
    unittest.main()
