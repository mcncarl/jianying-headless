"""Version selection and fail-closed guards, without changing the installed app."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
import runtime_profiles as profiles


class RuntimeProfiles(unittest.TestCase):
    def info(self, version, build=None):
        return {'CFBundleShortVersionString': version, 'CFBundleVersion': build or version,
                'CFBundleIdentifier': 'com.lemon.lvpro'}

    def test_primary_and_legacy_identities(self):
        self.assertEqual(profiles.PRIMARY_VERSION, '11.5.0')
        for profile, h in profiles.PROFILES.items():
            version, build, expected = profiles.RUNTIME_IDENTITIES[profile]
            self.assertEqual(h, expected)
            self.assertEqual(profiles.validate_identity(self.info(version, build), h), profile)

    def test_exact_appstore_1140_build_481_identity_is_resolved(self):
        profile = profiles.APPSTORE_1140_PROFILE
        info = self.info('11.4.0', '481')
        fingerprint = profiles.PROFILES[profile]

        self.assertEqual(profiles.validate_identity(info, fingerprint), profile)
        self.assertEqual(profiles.resolve_identity(info, fingerprint), {
            'profile_id': profile,
            'app_version': '11.4.0',
            'app_build': '481',
            'library_sha256': fingerprint,
            'runtime_profile': profiles.PROFILE_PREFIX + profile,
        })

    def test_appstore_1140_identity_rejects_nearby_builds_and_versions(self):
        fingerprint = profiles.PROFILES[profiles.APPSTORE_1140_PROFILE]
        for version, build in (('11.4.0', '480'), ('11.4.0', '482'),
                               ('11.4.1', '481'), ('11.4.0', '11.4.0')):
            with self.subTest(version=version, build=build), self.assertRaises(ValueError):
                profiles.validate_identity(self.info(version, build), fingerprint)

    def test_exact_1142_fingerprint_is_preserved(self):
        self.assertEqual(profiles.PROFILES['11.4.2'],
                         '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1')

    def test_mismatched_hash_build_bundle_and_unknown_version_rejected(self):
        for profile in ('11.5.0', '11.4.2', '11.4.0', profiles.APPSTORE_1140_PROFILE):
            version, build, h = profiles.RUNTIME_IDENTITIES[profile]
            bad=[(self.info(version, build),'0'*64)]
            for key,val in [('CFBundleVersion','unexpected'),('CFBundleIdentifier','com.other')]:
                info=self.info(version, build);info[key]=val;bad.append((info,h))
            for info,sha in bad:
                with self.subTest(profile=profile,info=info),self.assertRaises(ValueError):
                    profiles.validate_identity(info,sha)
        with self.assertRaises(ValueError):
            profiles.validate_identity(self.info('11.5.1'),profiles.PROFILES['11.5.0'])

    def test_export_only_allows_same_reviewed_runtime(self):
        for v in ('11.5.0','11.4.2'):
            p=profiles.PROFILE_PREFIX+v
            profiles.validate_export_profiles(p,p)
        for a,b in [('11.4.2','11.5.0'),('11.5.0','11.4.2'),('11.4.0','11.4.0'),
                    (profiles.APPSTORE_1140_PROFILE, profiles.APPSTORE_1140_PROFILE),
                    ('11.5.1','11.5.1')]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):
                profiles.validate_export_profiles(profiles.PROFILE_PREFIX+a,profiles.PROFILE_PREFIX+b)

    def test_resource_pairing_keeps_capture_provenance(self):
        for p in profiles.EXPORT_PROFILES:
            profiles.validate_resource_profile(p,profiles.RESOURCE_CAPTURE_PROFILE)
        with self.assertRaises(ValueError):
            profiles.validate_resource_profile(profiles.PROFILE_PREFIX+'11.4.0',profiles.RESOURCE_CAPTURE_PROFILE)
        with self.assertRaises(ValueError):
            profiles.validate_resource_profile(profiles.PROFILE_PREFIX+'11.5.0',profiles.PROFILE_PREFIX+'11.5.0')

    def test_timeline_schema_is_version_scoped(self):
        old = {'new_version':'185.0.0','version':360000}
        new = {'new_version':'187.0.0','version':360000}
        for version in ('11.4.0','11.4.2','11.5.0', profiles.APPSTORE_1140_PROFILE):
            profiles.validate_timeline_schema(old,profiles.PROFILE_PREFIX+version)
        profiles.validate_timeline_schema(new,profiles.PROFILE_PREFIX+'11.5.0')
        for version in ('11.4.0','11.4.2', profiles.APPSTORE_1140_PROFILE, '11.5.1'):
            with self.assertRaises(ValueError):
                profiles.validate_timeline_schema(new,profiles.PROFILE_PREFIX+version)
        for bad in [dict(new,new_version='188.0.0'),dict(new,version=360001),dict(new,version=360000.0)]:
            with self.assertRaises(ValueError): profiles.validate_timeline_schema(bad)

    def test_ui_upgrade_is_reported_without_mutating_evidence(self):
        old={'id':'sample','new_version':'185.0.0','version':360000}
        new=dict(old,new_version='187.0.0',last_modified_platform={'app_version':'11.5.0'})
        runtime=profiles.PROFILE_PREFIX+'11.5.0'
        change=profiles.saved_schema_upgrade(old,new,runtime)
        self.assertEqual(change,{'timeline_id':'sample','before':'185.0.0','after':'187.0.0'})
        self.assertEqual(new['new_version'],'187.0.0')
        self.assertIsNone(profiles.saved_schema_upgrade(new,new,runtime))
        with self.assertRaises(ValueError): profiles.saved_schema_upgrade(new,old,runtime)
        with self.assertRaises(ValueError):
            profiles.saved_schema_upgrade(old,dict(new,last_modified_platform={}),runtime)


if __name__=='__main__':
    unittest.main()
