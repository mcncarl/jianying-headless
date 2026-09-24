"""Portable negative tests for Windows native-load and live-write gates."""
import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'bridge'), str(ROOT / 'tools')]
import runtime_io_windows as io
import runtime_report_windows as report
import windows_codec as codec
import windows_platform as platform


class WindowsGuardTests(unittest.TestCase):
    def test_override_cannot_select_another_engine(self):
        selected = platform.Installation(Path('/reviewed/11.5.0.14471'),
                                         '11.5.0', '14471', 'reviewed-hash')
        with patch.object(io, 'APP_BUNDLE', Path('/other/11.5.0.14471')), \
             patch.object(platform, 'discover', return_value=selected), \
             patch.object(platform, 'review_status') as review, \
             patch.object(codec, 'codec_for') as load:
            with self.assertRaisesRegex(io.ApplyError, 'differs'):
                io._codec()
            review.assert_not_called()
            load.assert_not_called()

    def test_unreviewed_engine_cannot_load(self):
        selected = platform.Installation(Path('/unreviewed/11.5.0.14471'),
                                         '11.5.0', '14471', 'unreviewed-hash')
        with patch.object(io, 'APP_BUNDLE', None), \
             patch.object(platform, 'discover', return_value=selected), \
             patch.object(platform, 'review_status', return_value={
                 'reviewed': False, 'reason': 'hash mismatch'}), \
             patch.object(codec, 'codec_for') as load:
            with self.assertRaisesRegex(io.ApplyError, 'not reviewed'):
                io._codec()
            load.assert_not_called()

    def test_diagnostic_never_loads_unreviewed_engine(self):
        selected = platform.Installation(Path('/unreviewed/11.5.0.14471'),
                                         '11.5.0', '14471', 'unreviewed-hash')
        with patch.object(platform, 'candidate_directories', return_value=[selected.directory]), \
             patch.object(platform, 'inspect', return_value=selected), \
             patch.object(platform, 'review_status', return_value={
                 'reviewed': False, 'reason': 'hash mismatch'}), \
             patch.object(platform, 'draft_root', return_value=Path('/missing')), \
             patch.object(report.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
             patch.object(codec, 'codec_for') as load:
            for verify in (False, True):
                result = report.collect(verify)
                self.assertFalse(result['review']['reviewed'])
            load.assert_not_called()

    def test_diagnostic_default_does_not_load_reviewed_engine(self):
        selected = platform.Installation(Path('/reviewed/11.5.0.14471'),
                                         '11.5.0', '14471', 'reviewed-hash')
        with patch.object(platform, 'candidate_directories', return_value=[selected.directory]), \
             patch.object(platform, 'inspect', return_value=selected), \
             patch.object(platform, 'review_status', return_value={'reviewed': True}), \
             patch.object(platform, 'draft_root', return_value=Path('/missing')), \
             patch.object(report.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
             patch.object(codec, 'codec_for') as load:
            report.collect(False)
            load.assert_not_called()

    def test_native_result_is_bounded_before_pointer_read(self):
        value = codec.MSVCString()
        value.size = codec.MAX_CODEC_OUTPUT_BYTES + 1
        value.capacity = value.size
        value.storage.pointer = 1
        with patch.object(ctypes, 'string_at') as read:
            with self.assertRaises(codec.CodecUnavailable):
                value.to_bytes()
            read.assert_not_called()
        value.size = 16
        value.capacity = 16
        value.storage.pointer = None
        with self.assertRaises(codec.CodecUnavailable):
            value.to_bytes()

    def test_process_enumeration_errors_are_not_editor_closed(self):
        kernel = MagicMock()
        kernel.CreateToolhelp32Snapshot.return_value = 99
        last_error = [0]

        def set_error(value):
            last_error[0] = value

        def fail(_snapshot, _entry):
            last_error[0] = 5
            return False

        with patch.object(ctypes, 'WinDLL', create=True, return_value=kernel), \
             patch.object(ctypes, 'set_last_error', create=True, side_effect=set_error), \
             patch.object(ctypes, 'get_last_error', create=True,
                          side_effect=lambda: last_error[0]):
            kernel.Process32FirstW.side_effect = fail
            with self.assertRaisesRegex(io.ApplyError, 'enumerate'):
                io._running_image_names()
            kernel.Process32FirstW.side_effect = lambda _snap, _entry: True
            kernel.Process32NextW.side_effect = fail
            with self.assertRaisesRegex(io.ApplyError, 'enumerate'):
                io._running_image_names()
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_empty_process_snapshot_is_not_an_error(self):
        kernel = MagicMock()
        kernel.CreateToolhelp32Snapshot.return_value = 101
        last_error = [0]

        def empty(_snapshot, _entry):
            last_error[0] = io.ERROR_NO_MORE_FILES
            return False

        with patch.object(ctypes, 'WinDLL', create=True, return_value=kernel), \
             patch.object(ctypes, 'set_last_error', create=True,
                          side_effect=lambda value: last_error.__setitem__(0, value)), \
             patch.object(ctypes, 'get_last_error', create=True,
                          side_effect=lambda: last_error[0]):
            kernel.Process32FirstW.side_effect = empty
            self.assertEqual(io._running_image_names(), [])
        kernel.CloseHandle.assert_called_once_with(101)


if __name__ == '__main__':
    unittest.main()
