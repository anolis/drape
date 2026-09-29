import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import desktop


class WindowManagerDetectionTest(unittest.TestCase):
    def test_reads_actual_wm_and_refreshes_after_switch(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":99", "XDG_SESSION_TYPE": "x11"}), \
                mock.patch.object(desktop, "_wm_cache", (None, 0)), \
                mock.patch.object(desktop.subprocess, "run") as run:
            run.side_effect = [mock.Mock(stdout="window id # 0xABC"), mock.Mock(stdout='_NET_WM_NAME = "Marco"'),
                               mock.Mock(stdout="window id # 0x123"), mock.Mock(stdout='_NET_WM_NAME = "Compiz"')]
            self.assertEqual(desktop.running_wm(), "Marco")
            self.assertEqual(desktop.running_wm(), "Marco")
            self.assertEqual(run.call_count, 2)
            self.assertEqual(desktop.running_wm(refresh=True), "Compiz")

    def test_wayland_does_not_mistake_xwayland_for_the_compositor(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":99", "XDG_SESSION_TYPE": "wayland"}), \
                mock.patch.object(desktop, "_wm_cache", (None, 0)), \
                mock.patch.object(desktop.subprocess, "run") as run:
            self.assertIsNone(desktop.running_wm())
            run.assert_not_called()


class MateDesktopTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "MATE"})
        env.start()
        self.addCleanup(env.stop)
        schemas = mock.patch.object(desktop, "_schema_exists", return_value=True)
        schemas.start()
        self.addCleanup(schemas.stop)
        settings = mock.patch.object(desktop.Gio, "Settings")
        self.settings = settings.start()
        self.addCleanup(settings.stop)
        wm = mock.patch.object(desktop, "running_wm", return_value="Marco")
        wm.start()
        self.addCleanup(wm.stop)

    def test_mate_wins_when_gnome_schemas_are_also_installed(self):
        for session in ("MATE", "mate", "MATE:GNOME"):
            with self.subTest(session=session), mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": session}):
                self.assertEqual(desktop.current_desktop(), "mate")

    def test_missing_mate_schemas_do_not_write_to_gnome(self):
        with mock.patch.object(desktop, "_schema_exists", side_effect=lambda s: s.startswith("org.gnome.")):
            self.assertFalse(desktop.set_("gtk", "Test"))
        self.settings.new.assert_not_called()

    def test_theme_components_target_mate(self):
        expected = {
            "gtk": ("org.mate.interface", "gtk-theme"),
            "icons": ("org.mate.interface", "icon-theme"),
            "cursors": ("org.mate.peripherals-mouse", "cursor-theme"),
            "wm": ("org.mate.Marco.general", "theme"),
        }
        for part, (schema, key) in expected.items():
            with self.subTest(part=part):
                self.settings.reset_mock()
                self.assertTrue(desktop.set_(part, "Test"))
                self.settings.new.assert_called_once_with(schema)
                self.settings.new.return_value.set_string.assert_called_once_with(key, "Test")

    def test_cinnamon_desktop_themes_are_unsupported(self):
        self.assertFalse(desktop.supported("desktop"))
        self.assertFalse(desktop.set_("desktop", "Test"))
        self.settings.new.assert_not_called()

    def test_wallpaper_filename_round_trip(self):
        path = "/tmp/Wall paper #1 café.png"
        uri = Path(path).as_uri()
        self.assertTrue(desktop.set_("wallpapers", uri))
        self.settings.new.assert_called_with("org.mate.background")
        self.settings.new.return_value.set_string.assert_called_once_with("picture-filename", path)
        self.settings.new.return_value.get_string.return_value = path
        self.assertEqual(desktop.get("wallpapers"), uri)
        self.settings.new.return_value.get_string.return_value = ""
        self.assertEqual(desktop.get("wallpapers"), "")

    def test_cinnamon_and_gnome_detection_is_preserved(self):
        for session, expected in (("X-Cinnamon", "cinnamon"), ("GNOME", "gnome")):
            with self.subTest(session=session), mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": session}):
                self.assertEqual(desktop.current_desktop(), expected)

    def test_unknown_desktops_do_not_fall_back_to_gnome(self):
        for session in ("sway", "", "LXQt"):
            with self.subTest(session=session), mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": session}):
                self.assertIsNone(desktop.current_desktop())
                self.assertFalse(desktop.supported("gtk"))

    def test_active_window_manager_controls_border_formats(self):
        for wm, expected in (("Marco", "wm"), ("Mutter (Muffin)", "wm"), ("Metacity", "wm"),
                             ("Xfwm4", "xfwm"), ("Compiz", None), ("GNOME Shell", None),
                             ("KWin", "aurorae"), (None, None)):
            with self.subTest(wm=wm), mock.patch.object(desktop, "running_wm", return_value=wm), \
                    mock.patch.object(desktop.shutil, "which", return_value="/usr/bin/xfconf-query"):
                self.assertEqual(desktop.border_part(), expected)
                self.assertEqual(desktop.supported("wm"), expected is not None)
                self.assertEqual(desktop.scope("wm")[0], "125" if expected == "wm" else
                                 "138" if expected == "xfwm" else
                                 ("114,717" if desktop.kde.major_version() >= 6 else "114") if expected == "aurorae" else "")
                self.assertTrue(desktop.supported("gtk"))  # MATE controls still work under Compiz

    def test_filter_rejects_wrong_format_but_keeps_unknown_downloads(self):
        self.assertFalse(desktop.archive_compatible({"xfwm"}, True, "wm"))
        self.assertTrue(desktop.archive_compatible({"wm", "xfwm"}, True, "wm"))
        self.assertFalse(desktop.archive_compatible({"gtk", "gtk-4.0"}, True, "gtk"))
        self.assertTrue(desktop.archive_compatible({"gtk", "gtk-3.0"}, True, "gtk"))
        self.assertTrue(desktop.archive_compatible(set(), False, "gtk"))
        self.assertFalse(desktop.archive_compatible({"desktop"}, True, "desktop"))

    def test_mixed_pack_applies_only_compatible_parts(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(desktop, "set_", return_value=True) as set_:
            (Path(t) / "gtk-3.0").mkdir()
            c = {"path": t, "name": "Mixed", "provides": ["gtk", "wm", "xfwm", "desktop"]}
            self.assertEqual(desktop.apply_component(c), ["gtk", "wm"])
            self.assertEqual(set_.call_args_list, [mock.call("gtk", "Mixed"), mock.call("wm", "Mixed")])
            set_.reset_mock()
            self.assertEqual(desktop.apply_component(c, only=[]), [])
            set_.assert_not_called()

    def test_xfce_uses_xfconf_and_reports_failure(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}), \
                mock.patch.object(desktop, "running_wm", return_value="Xfwm4"), \
                mock.patch.object(desktop.shutil, "which", return_value="/usr/bin/xfconf-query"), \
                mock.patch.object(desktop.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(desktop.set_("wm", "Test"))
            self.assertEqual(run.call_args.args[0], ["xfconf-query", "-c", "xfwm4", "-p", "/general/theme",
                                                    "--create", "--type", "string", "--set", "Test"])
            self.assertTrue(desktop.set_("gtk", "Test"))
            self.assertIn("/Net/ThemeName", run.call_args.args[0])
            run.return_value.returncode = 1
            self.assertFalse(desktop.set_("gtk", "Test"))
            self.settings.new.assert_not_called()
