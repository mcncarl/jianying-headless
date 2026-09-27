"""Reviewed application identities; never infer compatibility from a version prefix."""
PRIMARY_VERSION = '11.5.0'
PROFILE_PREFIX = 'jy14-headless-macos-'
#: Every host that may report a runtime profile. The timeline payload is shared
#: across them, so schema compatibility is gated on the *version* rather than on
#: the host prefix. Native export remains macOS-only; see EXPORT_PROFILES.
RUNTIME_PROFILE_PREFIXES = (PROFILE_PREFIX, 'jy14-headless-win-')
PROFILES = {
    '11.5.0': '2041482a1aaeffa4d8bd69b836f8cf38807aaad8021bca410d567c59af3bccfa',
    '11.4.2': '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1',
    '11.4.0': 'a1693070036a6678bb5db35f71d2105812ad24a2370e7e91c78712cc0d6455f3',
}
EXPORT_PROFILES = frozenset(PROFILE_PREFIX + v for v in ('11.5.0', '11.4.2'))
RESOURCE_CAPTURE_PROFILE = PROFILE_PREFIX + '11.4.2'
TIMELINE_SCHEMAS = frozenset((('185.0.0', 360000), ('187.0.0', 360000)))


def runtime_profile_version(runtime_profile):
    """Reviewed editor version behind a runtime profile name, or ``None``.

    A profile is only recognized when both its host prefix and its version are
    reviewed, so an unknown host or an unreviewed build can never be upgraded
    into acceptance by string comparison.
    """
    if not isinstance(runtime_profile, str):
        return None
    for prefix in RUNTIME_PROFILE_PREFIXES:
        if runtime_profile.startswith(prefix):
            version = runtime_profile[len(prefix):]
            return version if version in PROFILES else None
    return None


def validate_timeline_schema(timeline, runtime_profile=None):
    schema = (timeline.get('new_version'), timeline.get('version'))
    if type(schema[1]) is not int or schema not in TIMELINE_SCHEMAS:
        raise ValueError('Unexpected native timeline version')
    if runtime_profile is not None:
        version = runtime_profile_version(runtime_profile)
        if version is None or (schema[0] == '187.0.0' and version != '11.5.0'):
            raise ValueError('Native timeline schema is incompatible with this runtime profile')
    return schema


def saved_schema_upgrade(expected, actual, runtime_profile):
    """Recognize only the exact observed UI upgrade; never normalize other fields."""
    before = validate_timeline_schema(expected, runtime_profile)
    after = validate_timeline_schema(actual, runtime_profile)
    if before == after:
        return None
    if (runtime_profile_version(runtime_profile) == '11.5.0' and before == ('185.0.0', 360000)
            and after == ('187.0.0', 360000)
            and actual.get('last_modified_platform', {}).get('app_version') == '11.5.0'):
        return {'timeline_id': actual['id'], 'before': before[0], 'after': after[0]}
    raise ValueError('Unreviewed native schema migration')


def validate_identity(info, fingerprint):
    version = info.get('CFBundleShortVersionString')
    if (version not in PROFILES or info.get('CFBundleVersion') != version
            or info.get('CFBundleIdentifier') != 'com.lemon.lvpro'):
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    if fingerprint != PROFILES[version]:
        raise ValueError('Editor library differs from its exact headless runtime profile; version='
                         + version + '; actual=' + str(fingerprint) + '; expected=' + PROFILES[version]
                         + '. Collect a redacted report with python3 tools/runtime_report.py. '
                         'This build needs review; do not replace the expected hash.')
    return version


def validate_export_profiles(build_profile, runtime_profile):
    if build_profile not in EXPORT_PROFILES or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('No reviewed native export ABI for this build/runtime profile')
    if build_profile != runtime_profile:
        raise ValueError('Build runtime differs from the installed editor; rebuild or edit a copy on the current runtime')


def validate_resource_profile(runtime_profile, capture_profile):
    if capture_profile != RESOURCE_CAPTURE_PROFILE or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('Native resources need a reviewed runtime profile/capture pairing')
