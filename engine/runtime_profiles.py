"""Reviewed application identities; never infer compatibility from a version prefix."""
PRIMARY_VERSION = '11.5.0'
PROFILE_PREFIX = 'jy14-headless-macos-'

# Profile IDs are stable project identifiers.  They intentionally do not have
# to equal either Info.plist version field: preview builds can expose a legacy
# marketing version while carrying a distinct build identity.
RUNTIME_IDENTITIES = {
    '11.5.0': {
        'app_version': '11.5.0',
        'app_build': '11.5.0',
        'library_sha256': '2041482a1aaeffa4d8bd69b836f8cf38807aaad8021bca410d567c59af3bccfa',
    },
    '11.4.2': {
        'app_version': '11.4.2',
        'app_build': '11.4.2',
        'library_sha256': '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1',
    },
    '11.4.0': {
        'app_version': '11.4.0',
        'app_build': '11.4.0',
        'library_sha256': 'a1693070036a6678bb5db35f71d2105812ad24a2370e7e91c78712cc0d6455f3',
    },
    '11.5.3-beta2': {
        'app_version': '11.4.13224',
        'app_build': '11.5.3-beta2',
        'library_sha256': 'c6298ecd7e2ef33fdd0caa60ef48bb6358ac50ab1fca9706717b282b293b9976',
    },
}

# Backward-compatible profile-to-library view used by existing callers.
PROFILES = {profile_id: identity['library_sha256']
            for profile_id, identity in RUNTIME_IDENTITIES.items()}

LEGACY_CODEC = {
    'filename': 'jy14_codec_hardened_11_4',
    'sha256': 'b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d',
    'toolchain': {
        'compiler': 'Apple clang 21.0.0 (clang-2100.1.1.101)',
        'macos_sdk': '26.5',
        'binary_minimum_macos': '26.0',
        'linker': '1267',
    },
}
CODEC_PROFILES = {
    profile_id: dict(LEGACY_CODEC) for profile_id in ('11.5.0', '11.4.2', '11.4.0')
}
CODEC_PROFILES['11.5.3-beta2'] = {
    'filename': 'jy14_codec_hardened_11_5_3_beta2',
    'sha256': '4946c69786eafe97f0f237716ce3dfad9eb45911be50fef970210d6778551d9d',
    'toolchain': {
        'compiler': 'Apple clang 21.0.0 (clang-2100.3.34.2)',
        'macos_sdk': '26.5',
        'binary_minimum_macos': '26.0',
        'linker': '27037.1',
    },
}

EXPORT_PROFILES = frozenset(PROFILE_PREFIX + v for v in ('11.5.0', '11.4.2'))
RESOURCE_RUNTIME_PROFILES = frozenset(PROFILE_PREFIX + v for v in ('11.5.0', '11.4.2'))
RESOURCE_CAPTURE_PROFILE = PROFILE_PREFIX + '11.4.2'
BETA_RESOURCE_KEYS = frozenset(('transition/dissolve', 'effect/light-shake'))
TIMELINE_SCHEMAS = frozenset((('185.0.0', 360000), ('187.0.0', 360000)))
SCHEMA_187_PROFILES = frozenset(PROFILE_PREFIX + v for v in ('11.5.0', '11.5.3-beta2'))
SAVED_APP_VERSIONS = {
    PROFILE_PREFIX + '11.5.0': '11.5.0',
    PROFILE_PREFIX + '11.5.3-beta2': '11.5.3-beta2',
}


def identity_for_info(info):
    if info.get('CFBundleIdentifier') != 'com.lemon.lvpro':
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    candidates = [(profile_id, identity) for profile_id, identity in RUNTIME_IDENTITIES.items()
                  if info.get('CFBundleShortVersionString') == identity['app_version']
                  and info.get('CFBundleVersion') == identity['app_build']]
    if len(candidates) != 1:
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    profile_id, identity = candidates[0]
    return dict(identity, profile_id=profile_id,
                runtime_profile=PROFILE_PREFIX + profile_id)


def resolve_identity(info, fingerprint):
    identity = identity_for_info(info)
    if fingerprint != identity['library_sha256']:
        raise ValueError('Editor library differs from its exact headless runtime profile; profile='
                         + identity['profile_id'] + '; actual=' + str(fingerprint) + '; expected='
                         + identity['library_sha256']
                         + '. Collect a redacted report with python3 tools/runtime_report.py. '
                         'This build needs review; do not replace the expected hash.')
    return identity


def validate_timeline_schema(timeline, runtime_profile=None):
    schema = (timeline.get('new_version'), timeline.get('version'))
    if type(schema[1]) is not int or schema not in TIMELINE_SCHEMAS:
        raise ValueError('Unexpected native timeline version')
    if runtime_profile is not None:
        known = {PROFILE_PREFIX + version for version in PROFILES}
        if runtime_profile not in known or (schema[0] == '187.0.0' and
                                           runtime_profile not in SCHEMA_187_PROFILES):
            raise ValueError('Native timeline schema is incompatible with this runtime profile')
    return schema


def saved_schema_upgrade(expected, actual, runtime_profile):
    """Recognize only the exact observed UI upgrade; never normalize other fields."""
    before = validate_timeline_schema(expected, runtime_profile)
    after = validate_timeline_schema(actual, runtime_profile)
    if before == after:
        return None
    if (runtime_profile in SAVED_APP_VERSIONS and before == ('185.0.0', 360000)
            and after == ('187.0.0', 360000)
            and actual.get('last_modified_platform', {}).get('app_version')
            == SAVED_APP_VERSIONS[runtime_profile]):
        return {'timeline_id': actual['id'], 'before': before[0], 'after': after[0]}
    raise ValueError('Unreviewed native schema migration')


def validate_identity(info, fingerprint):
    return resolve_identity(info, fingerprint)['profile_id']


def validate_export_profiles(build_profile, runtime_profile):
    if build_profile not in EXPORT_PROFILES or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('No reviewed native export ABI for this build/runtime profile')
    if build_profile != runtime_profile:
        raise ValueError('Build runtime differs from the installed editor; rebuild or edit a copy on the current runtime')


def validate_resource_profile(runtime_profile, capture_profile, resource_key=None):
    beta_allowed = (runtime_profile == PROFILE_PREFIX + '11.5.3-beta2'
                    and resource_key in BETA_RESOURCE_KEYS)
    if (capture_profile != RESOURCE_CAPTURE_PROFILE
            or (runtime_profile not in RESOURCE_RUNTIME_PROFILES and not beta_allowed)):
        raise ValueError('Native resources need a reviewed runtime profile/capture pairing')
