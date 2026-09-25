"""Reviewed application identities; never infer compatibility from a version prefix."""
PRIMARY_VERSION = '11.5.0'
PROFILE_PREFIX = 'jy14-headless-macos-'
APPSTORE_1140_PROFILE = '11.4.0-b481'
RUNTIME_IDENTITIES = {
    '11.5.0': ('11.5.0', '11.5.0', '2041482a1aaeffa4d8bd69b836f8cf38807aaad8021bca410d567c59af3bccfa'),
    '11.4.2': ('11.4.2', '11.4.2', '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1'),
    '11.4.0': ('11.4.0', '11.4.0', 'a1693070036a6678bb5db35f71d2105812ad24a2370e7e91c78712cc0d6455f3'),
    APPSTORE_1140_PROFILE: ('11.4.0', '481', 'aea79715de6097394c2f38153e11565f02a823678801cd1eafe90bcccb20c086'),
}
PROFILES = {profile: identity[2] for profile, identity in RUNTIME_IDENTITIES.items()}
LEGACY_CODEC = {
    'filename': 'jy14_codec_hardened_11_4',
    'sha256': 'b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d',
    'toolchain': {
        'compiler': 'Apple clang 21.0.0 (clang-2100.1.1.101)',
        'macos_sdk': '26.5', 'binary_minimum_macos': '26.0', 'linker': '1267',
    },
}
CODEC_PROFILES = {profile: LEGACY_CODEC for profile in ('11.5.0', '11.4.2', '11.4.0')}
CODEC_PROFILES[APPSTORE_1140_PROFILE] = {
    'filename': 'jy14_codec_hardened_11_4_b481',
    'sha256': '1fe5819a5bd95051d9adecc9d19ad15ff1e62d8529028307035f181a576ec663',
    'toolchain': {
        'compiler': 'Apple clang 17.0.0 (clang-1700.4.4.1)',
        'macos_sdk': '26.1', 'binary_minimum_macos': '15.0', 'linker': '1230.1',
    },
}
EXPORT_PROFILES = frozenset(PROFILE_PREFIX + v for v in ('11.5.0', '11.4.2'))
EDIT_PROFILES = frozenset(PROFILE_PREFIX + v for v in ('11.5.0', '11.4.2', '11.4.0'))
RESOURCE_CAPTURE_PROFILE = PROFILE_PREFIX + '11.4.2'
TIMELINE_SCHEMAS = frozenset((('185.0.0', 360000), ('187.0.0', 360000)))


def validate_timeline_schema(timeline, runtime_profile=None):
    schema = (timeline.get('new_version'), timeline.get('version'))
    if type(schema[1]) is not int or schema not in TIMELINE_SCHEMAS:
        raise ValueError('Unexpected native timeline version')
    if runtime_profile is not None:
        known = {PROFILE_PREFIX + version for version in PROFILES}
        if runtime_profile not in known or (schema[0] == '187.0.0' and
                                           runtime_profile != PROFILE_PREFIX + '11.5.0'):
            raise ValueError('Native timeline schema is incompatible with this runtime profile')
    return schema


def saved_schema_upgrade(expected, actual, runtime_profile):
    """Recognize only the exact observed UI upgrade; never normalize other fields."""
    before = validate_timeline_schema(expected, runtime_profile)
    after = validate_timeline_schema(actual, runtime_profile)
    if before == after:
        return None
    if (runtime_profile == PROFILE_PREFIX + '11.5.0' and before == ('185.0.0', 360000)
            and after == ('187.0.0', 360000)
            and actual.get('last_modified_platform', {}).get('app_version') == '11.5.0'):
        return {'timeline_id': actual['id'], 'before': before[0], 'after': after[0]}
    raise ValueError('Unreviewed native schema migration')


def identity_for_info(info):
    if info.get('CFBundleIdentifier') != 'com.lemon.lvpro':
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    matching = [(profile, values) for profile, values in RUNTIME_IDENTITIES.items()
                if values[:2] == (info.get('CFBundleShortVersionString'), info.get('CFBundleVersion'))]
    if len(matching) != 1:
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    profile, (version, build, fingerprint) = matching[0]
    return {'profile_id': profile, 'app_version': version, 'app_build': build,
            'library_sha256': fingerprint, 'runtime_profile': PROFILE_PREFIX + profile}


def resolve_identity(info, fingerprint):
    identity = identity_for_info(info)
    if fingerprint != identity['library_sha256']:
        raise ValueError('Editor library differs from its exact headless runtime profile; profile='
                         + identity['profile_id'] + '; actual=' + str(fingerprint)
                         + '; expected=' + identity['library_sha256']
                         + '. Collect a redacted report with python3 tools/runtime_report.py. '
                         'This build needs review; do not replace the expected hash.')
    return identity


def validate_identity(info, fingerprint):
    return resolve_identity(info, fingerprint)['profile_id']


def validate_export_profiles(build_profile, runtime_profile):
    if build_profile not in EXPORT_PROFILES or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('No reviewed native export ABI for this build/runtime profile')
    if build_profile != runtime_profile:
        raise ValueError('Build runtime differs from the installed editor; rebuild or edit a copy on the current runtime')


def validate_resource_profile(runtime_profile, capture_profile):
    if capture_profile != RESOURCE_CAPTURE_PROFILE or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('Native resources need a reviewed runtime profile/capture pairing')
