import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from acmanga import reader
from acmanga.settings import default_settings, _normalized


class AndroidDetectionTests(unittest.TestCase):
    def test_termux_version_detects_android(self):
        self.assertTrue(reader.is_android_termux({'TERMUX_VERSION': '0.118', 'PREFIX': '/x'}))

    def test_native_termux_prefix_detects_android(self):
        self.assertTrue(reader.is_android_termux({'PREFIX': '/data/data/com.termux/files/usr'}))

    def test_linux_environment_stays_desktop(self):
        self.assertFalse(reader.is_android_termux({'PREFIX': '/usr', 'HOME': '/home/example'}))


class AndroidScriptTests(unittest.TestCase):
    def test_mobile_script_is_separate_and_desktop_script_is_unchanged_selection(self):
        desktop = reader._lua_reader_script(mobile=False)
        mobile = reader._lua_reader_script(mobile=True)
        self.assertIn("toggle-fit", desktop)
        self.assertIn("android-double-tap", mobile)
        self.assertIn("android-drag-move", mobile)
        self.assertNotEqual(desktop, mobile)

    def test_write_mpv_files_can_force_android_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            _playlist, _conf, script = reader.write_mpv_files(tmp, [], mobile=True)
            text = script.read_text(encoding='utf-8')
            self.assertIn("manga-cli 0.8.3 Android/Termux reader profile", text)


class AndroidMpvCommandTests(unittest.TestCase):
    def fake_supported(self, _path):
        return {'background-color', 'window-maximized', 'auto-window-resize',
                'video-recenter', 'input-builtin-dragging', 'input-touch-emulate-mouse'}

    def test_android_command_is_fullscreen_x11_and_geometry_stable(self):
        with mock.patch.object(reader, '_mpv_supported_options', side_effect=self.fake_supported):
            cmd = reader.mpv_command('/usr/bin/mpv', 'p', 'i', 's', 'sock', mobile=True)
        self.assertIn('--vo=x11', cmd)
        self.assertIn('--fs=yes', cmd)
        self.assertIn('--border=no', cmd)
        self.assertIn('--force-window=immediate', cmd)
        self.assertIn('--auto-window-resize=no', cmd)
        self.assertIn('--geometry=100%x100%+0+0', cmd)
        self.assertNotIn('--window-maximized=yes', cmd)
        self.assertIn('--video-recenter=yes', cmd)
        self.assertIn('--input-builtin-dragging=no', cmd)
        self.assertIn('--input-touch-emulate-mouse=yes', cmd)
        self.assertIn('--input-doubleclick-time=280', cmd)
        self.assertIn('--video-unscaled=no', cmd)
        self.assertIn('--panscan=0', cmd)

    def test_desktop_command_contract_stays_windowed(self):
        with mock.patch.object(reader, '_mpv_supported_options', side_effect=self.fake_supported):
            cmd = reader.mpv_command('/usr/bin/mpv', 'p', 'i', 's', 'sock', video_output='gpu', mobile=False)
        self.assertIn('--vo=gpu', cmd)
        self.assertIn('--fs=no', cmd)
        self.assertIn('--border=yes', cmd)
        self.assertIn('--window-maximized=yes', cmd)
        self.assertIn('--video-recenter=no', cmd)
        self.assertIn('--input-doubleclick-time=0', cmd)

    def test_android_output_candidates_skip_gpu(self):
        with mock.patch.object(reader, '_mpv_video_outputs', return_value={'gpu', 'x11'}):
            self.assertEqual(reader._mpv_output_candidates('/usr/bin/mpv', mobile=True), ['x11'])
        with mock.patch.object(reader, '_mpv_video_outputs', return_value={'gpu', 'x11'}):
            self.assertEqual(reader._mpv_output_candidates('/usr/bin/mpv', mobile=False), ['gpu', 'x11'])


class AndroidSettingsTests(unittest.TestCase):
    def test_default_mobile_zoom(self):
        self.assertEqual(default_settings()['mobile_double_tap_zoom'], 2.0)

    def test_mobile_zoom_is_clamped(self):
        self.assertEqual(_normalized({'mobile_double_tap_zoom': 99})['mobile_double_tap_zoom'], 3.0)
        self.assertEqual(_normalized({'mobile_double_tap_zoom': 0.1})['mobile_double_tap_zoom'], 1.25)

    def test_mobile_zoom_accepts_normal_value(self):
        self.assertEqual(_normalized({'mobile_double_tap_zoom': 1.5})['mobile_double_tap_zoom'], 1.5)


class AndroidSourceContractTests(unittest.TestCase):
    def test_mobile_lua_has_expected_safety_contract(self):
        source = Path(reader.__file__).with_name('reader_android.lua').read_text(encoding='utf-8')
        required = [
            "MBTN_LEFT_DBL", "MOUSE_MOVE", "DOUBLE_TAP_DELAY", "tap_guard_until",
            "pointer_pos", "touch-pos", "video-zoom", "video-align-x", "video-align-y",
            "osd-dimensions", "Refit after rotation", "manual_quit", "quit', '4'",
            "show_page_indicator", "mobile_double_tap_zoom", "persist_fit",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
