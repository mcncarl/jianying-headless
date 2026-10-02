"""通过隔离文件与替身直接验证运行时入口；不加载官方库。"""
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'engine'), str(ROOT / 'tools')]
import headless_runtime as runtime
import runtime_profiles as profiles
import build_native_codec as builder
import native_edit as edit
import native_export as export


@contextmanager
def fixture(profile):
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        app = root / 'App.app'
        bridge = root / 'bridge'
        bridge.mkdir()
        (app / 'Contents/Frameworks').mkdir(parents=True)
        version, build, fingerprint = profiles.RUNTIME_IDENTITIES[profile]
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString': version, 'CFBundleVersion': build,
            'CFBundleIdentifier': 'com.lemon.lvpro'}))
        manifest = json.loads((ROOT / 'bridge/SOURCE_MANIFEST.json').read_text())
        (bridge / 'SOURCE_MANIFEST.json').write_text(json.dumps(manifest))
        for name in manifest['source_files']:
            (bridge / name).write_bytes(b'fixture')
        codec = profiles.CODEC_PROFILES[profile]
        (bridge / codec['filename']).write_bytes(b'fixture')
        (bridge / codec['filename']).chmod(0o700)
        def digest(path):
            name = Path(path).name
            if name == 'libvideoeditor.dylib':
                return fingerprint
            if name == 'SOURCE_MANIFEST.json':
                return runtime.IO_MANIFEST_SHA
            if name == codec['filename']:
                return codec['sha256']
            return manifest['source_files'][name]
        for module in (runtime, builder):
            stack.enter_context(patch.object(module, 'APP', app))
            stack.enter_context(patch.object(module, 'digest', side_effect=digest))
        stack.enter_context(patch.object(runtime, 'BACKEND', bridge))
        stack.enter_context(patch.object(builder, 'BRIDGE', bridge))
        stack.enter_context(patch.object(builder, 'ROOT', root))
        stack.enter_context(patch.object(runtime.platform, 'system', return_value='Darwin'))
        stack.enter_context(patch.object(runtime.platform, 'machine', return_value='arm64'))
        stack.enter_context(patch.object(runtime.platform, 'mac_ver', return_value=('15.6.1', (), '')))
        stack.enter_context(patch.object(runtime.shutil, 'which', return_value='/fake/tool'))
        yield root, app, bridge, codec


class RuntimeEntryTests(unittest.TestCase):
    def test_doctor_and_helper_select_exact_components_for_all_profiles(self):
        for profile in profiles.PROFILES:
            with self.subTest(profile=profile), fixture(profile) as (_, _, bridge, codec):
                result = runtime.doctor()
                self.assertEqual(result['codec_filename'], codec['filename'])
                self.assertEqual(result['codec_sha256'], codec['sha256'])
                helper = SimpleNamespace()
                for name in ('_decrypt_metadata_in_memory', '_encrypt_metadata_from_memory',
                             '_ensure_editor_closed', '_snapshot_file', '_parse_strict_json',
                             '_revalidate_snapshot', '_acquire_directory_transaction_lock',
                             '_release_directory_transaction_lock'):
                    setattr(helper, name, Mock())
                name = '_jy14_headless_pinned_io_' + hashlib.sha256(str(bridge).encode()).hexdigest()[:16]
                with patch.dict(sys.modules, {name: helper}):
                    runtime.helper()
                    self.assertEqual(helper.CODEC_PATH, bridge / codec['filename'])
                    self.assertEqual(helper.REQUIRED_CODEC_SHA256, codec['sha256'])
                    for value in vars(helper).values():
                        if isinstance(value, Mock):
                            value.assert_not_called()

    def test_helper_rejects_bad_identity_codec_or_environment_before_import(self):
        for failure in ('build', 'codec', 'system', 'architecture', 'macos'):
            with self.subTest(failure=failure), fixture(profiles.APPSTORE_1140_PROFILE) as (_, app, bridge, codec), ExitStack() as stack:
                if failure == 'build':
                    path = app / 'Contents/Info.plist'
                    info = plistlib.loads(path.read_bytes()); info['CFBundleVersion'] = '482'
                    path.write_bytes(plistlib.dumps(info))
                elif failure == 'codec':
                    (bridge / codec['filename']).unlink()
                else:
                    function, value = {'system': ('system', 'Linux'), 'architecture': ('machine', 'x86_64'),
                                       'macos': ('mac_ver', ('15.6.2', (), ''))}[failure]
                    stack.enter_context(patch.object(runtime.platform, function, return_value=value))
                with patch.object(runtime.importlib.util, 'spec_from_file_location') as loader, \
                        patch.object(runtime.subprocess, 'run') as process, self.assertRaises(ValueError):
                    runtime.helper()
                loader.assert_not_called(); process.assert_not_called()

    def test_wrong_codec_fingerprint_rejected_before_import(self):
        with fixture(profiles.APPSTORE_1140_PROFILE), patch.object(runtime, 'digest', wraps=runtime.digest) as digest:
            original = digest._mock_wraps
            digest.side_effect = lambda path: '0' * 64 if Path(path).name.endswith('_b481') else original(path)
            with patch.object(runtime.importlib.util, 'spec_from_file_location') as loader, self.assertRaises(ValueError):
                runtime.helper()
            loader.assert_not_called()

    def test_builder_selects_profile_toolchain_without_compiling(self):
        for profile in profiles.PROFILES:
            with self.subTest(profile=profile), fixture(profile) as (_, _, _, codec), \
                    patch.object(builder, 'select_toolchain', return_value=({}, {})) as select, \
                    patch.object(builder, 'run') as process, redirect_stdout(io.StringIO()):
                builder.main(['--check-toolchain'])
                select.assert_called_once_with(codec['toolchain'], None)
                process.assert_not_called()

    def test_wrong_toolchain_rejected_before_build_directory_or_install(self):
        with fixture(profiles.APPSTORE_1140_PROFILE) as (root, _, _, _), \
                patch.object(builder, 'select_toolchain', side_effect=ValueError('wrong toolchain')), \
                patch.object(builder, 'run', return_value=SimpleNamespace(stderr='TeamIdentifier=X2JNK7LY8J\n')) as process:
            with self.assertRaisesRegex(ValueError, 'wrong toolchain'):
                builder.main(['--rebuild-check'])
            self.assertFalse((root / 'work').exists())
            self.assertTrue(all(call.args[0][0] == '/usr/bin/codesign' for call in process.call_args_list))

    def test_builder_rebuild_uses_exact_filename_and_fingerprint(self):
        for profile in profiles.PROFILES:
            with self.subTest(profile=profile), fixture(profile) as (root, _, bridge, codec):
                command = ['fake-clang', str(root / 'unused')]
                def compile_args(source, frameworks, output, toolchain):
                    self.assertEqual(output.name, codec['filename'])
                    output.write_bytes(b'fixture')
                    return command
                with patch.object(builder, 'select_toolchain', return_value=({}, {'profile': codec['toolchain']})) as select, \
                        patch.object(builder, 'compile_command', side_effect=compile_args), \
                        patch.object(builder, 'run', return_value=SimpleNamespace(stderr='TeamIdentifier=X2JNK7LY8J\n', stdout='fake clang')) as process, \
                        redirect_stdout(io.StringIO()) as output:
                    builder.main(['--rebuild-check'])
                select.assert_called_once_with(codec['toolchain'], None)
                report = json.loads(output.getvalue())
                self.assertEqual(report['codec_sha256'], codec['sha256'])
                self.assertFalse(report['installed'])
                self.assertIn(command, [call.args[0] for call in process.call_args_list])
                self.assertEqual((bridge / codec['filename']).read_bytes(), b'fixture')

    def test_builder_rejects_identity_and_platform_before_toolchain_or_compile(self):
        for failure in ('build', 'system', 'architecture'):
            with self.subTest(failure=failure), fixture(profiles.APPSTORE_1140_PROFILE) as (root, app, _, _), ExitStack() as stack:
                if failure == 'build':
                    path = app / 'Contents/Info.plist'
                    info = plistlib.loads(path.read_bytes())
                    info['CFBundleVersion'] = '482'
                    path.write_bytes(plistlib.dumps(info))
                else:
                    stack.enter_context(patch.object(builder.platform, 'system' if failure == 'system' else 'machine',
                                                     return_value='Linux' if failure == 'system' else 'x86_64'))
                with patch.object(builder, 'select_toolchain') as select, patch.object(builder, 'run') as process:
                    with self.assertRaises(ValueError):
                        builder.main(['--rebuild-check'])
                select.assert_not_called()
                process.assert_not_called()
                self.assertFalse((root / 'work').exists())

    def test_export_run_rejects_before_compilation_staging_or_directory_creation(self):
        profile = profiles.PROFILE_PREFIX + profiles.APPSTORE_1140_PROFILE
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(export, 'verified_build', return_value=(root / 'build', {'runtime_profile': profile}, {})), \
                    patch.object(export, 'supported_features', return_value={}), \
                    patch.object(export, 'settings_for', return_value={}), \
                    patch.object(export.j.nd, 'validate_runtime', return_value={'runtime_profile': profile}), \
                    patch.object(export.j.nd, 'fresh_directory') as fresh, \
                    patch.object(export, 'stage_timeline') as stage, \
                    patch.object(export.subprocess, 'run') as process, self.assertRaises(ValueError):
                export.run(root / 'build', root / 'work/output')
            fresh.assert_not_called()
            stage.assert_not_called()
            process.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_edit_publish_resume_reject_before_editor_or_draft_side_effects(self):
        profile = profiles.PROFILE_PREFIX + profiles.APPSTORE_1140_PROFILE
        for operation in ('build', 'publish', 'resume-publish'):
            with self.subTest(operation=operation), \
                    patch.object(edit.j.nd, 'doctor', return_value={'runtime_profile': profile}), \
                    patch.object(edit.j.nd, 'helper') as helper, patch.object(edit.j, 'publish') as publish:
                # 使用各入口真实支持的 CLI 参数。
                argv = ['native_edit', operation]
                argv += ['--plan', '/unused', '--out', '/unused'] if operation == 'build' else ['--build', '/unused', '--audit', '/unused']
                with patch.object(sys, 'argv', argv), self.assertRaises(ValueError):
                    edit.main()
                helper.assert_not_called(); publish.assert_not_called()

    def test_export_rejects_before_staging_files(self):
        profile = profiles.PROFILE_PREFIX + profiles.APPSTORE_1140_PROFILE
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with self.assertRaisesRegex(ValueError, 'No reviewed native export ABI'):
                export.stage_timeline({}, {'target': str(folder), 'runtime_profile': profile}, folder, folder / 'output')
            self.assertEqual(list(folder.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
