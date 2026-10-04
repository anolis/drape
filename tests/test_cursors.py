import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import cursors, desktop


class CursorSettingsTest(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {"ZDOTDIR": ""})
        environment.start()
        self.addCleanup(environment.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = self.home / ".config"
        for key, value in (("HOME", self.home), ("CONFIG_HOME", self.config)):
            patch = mock.patch.object(cursors, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {"DISPLAY": ":test"}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.real_publish = cursors._publish
        patch = mock.patch.object(cursors, "_publish")
        self.publish = patch.start()
        self.addCleanup(patch.stop)

    def test_publishes_cursor_without_clobbering_other_settings(self):
        resources = self.home / ".Xresources"
        resources.write_text("! DPI\nXft.dpi: 144\nXcursor.theme: Old\nXcursor.size: 16\n")
        profile = self.home / ".profile"
        profile.write_text(
            "export OTHER=value\n# BEGIN DRAPE QT STYLE\nexport QT_STYLE_OVERRIDE=kvantum\n# END DRAPE QT STYLE\n"
        )
        index = self.home / ".icons/default/index.theme"
        index.parent.mkdir(parents=True)
        index.write_text("[Icon Theme]\nName=My Default\nInherits=Old\n[Extra]\nKeep=yes\n")
        cursors.apply("Oxygen 02 Vibrant Orange", 32)
        self.assertIn("Xft.dpi: 144", resources.read_text())
        self.assertIn("! DPI", resources.read_text())
        self.assertNotIn("Old", resources.read_text())
        self.assertIn("Xcursor.theme: Oxygen 02 Vibrant Orange", resources.read_text())
        self.assertIn("Keep=yes", index.read_text())
        self.assertIn("Name=My Default", index.read_text())
        self.assertIn("export OTHER=value", profile.read_text())
        self.assertIn("export QT_STYLE_OVERRIDE=kvantum", profile.read_text())
        self.assertIn("export XCURSOR_THEME='Oxygen 02 Vibrant Orange'", profile.read_text())
        self.assertEqual(os.environ["XCURSOR_THEME"], "Oxygen 02 Vibrant Orange")
        self.assertEqual(os.environ["XCURSOR_SIZE"], "32")
        self.publish.assert_called_once_with(
            "Xcursor.theme: Oxygen 02 Vibrant Orange\nXcursor.size: 32\n",
            "Oxygen 02 Vibrant Orange",
            32,
        )

    def test_repeated_apply_replaces_old_block_and_keeps_spaces(self):
        cursors.apply("First", 24)
        cursors.apply("New Theme", 48)
        profile = (self.home / ".profile").read_text()
        self.assertEqual(profile.count("BEGIN DRAPE CURSOR"), 1)
        self.assertNotIn("First", profile)
        self.assertIn(
            'XCURSOR_THEME="New Theme"',
            (self.config / "environment.d/90-drape-cursor.conf").read_text(),
        )
        self.assertEqual((self.home / ".Xresources").read_text().count("Xcursor.theme:"), 1)

    def test_shell_metacharacters_are_literal(self):
        cursors.apply('Theme $HOME "quoted" `literal`', 24)
        profile = self.home / ".profile"
        result = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s" "$XCURSOR_THEME"', "check", str(profile)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, 'Theme $HOME "quoted" `literal`')

    def test_failure_restores_settings(self):
        profile = self.home / ".profile"
        profile.write_text("original\n")
        original = cursors.atomic_text

        def write(path, text):
            if path == profile and text != "original\n":
                raise OSError("disk full")
            original(path, text)

        with (
            mock.patch.object(cursors, "atomic_text", side_effect=write),
            self.assertRaises(OSError),
        ):
            cursors.apply("Theme", 24)
        self.assertEqual(profile.read_text(), "original\n")
        self.assertFalse((self.home / ".Xresources").exists())
        self.assertFalse((self.home / ".icons/default/index.theme").exists())
        self.publish.assert_not_called()

    def test_xrdb_merges_only_cursor_keys_and_never_reloads_unrelated_resources(self):
        with (
            mock.patch.object(cursors.shutil, "which", return_value="found"),
            mock.patch.object(cursors.subprocess, "run") as run,
        ):
            self.real_publish("Xcursor.theme: Theme\nXcursor.size: 24\n", "Theme", 24)
            self.assertEqual(run.call_args_list[0].args[0], ["xrdb", "-merge"])
            self.assertEqual(
                run.call_args_list[0].kwargs["input"], "Xcursor.theme: Theme\nXcursor.size: 24\n"
            )
            self.assertIn("XCURSOR_THEME=Theme", run.call_args_list[1].args[0])

    def test_cinnamon_size_is_used_for_shared_settings(self):
        with (
            mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
            mock.patch.object(desktop, "_schema_exists", return_value=True),
            mock.patch.object(desktop.Gio, "Settings") as settings,
            mock.patch.object(cursors, "apply") as apply,
        ):
            settings.new.return_value.get_int.return_value = 32
            desktop._set_default_cursor("Theme")
            apply.assert_called_once_with("Theme", 32)

    def test_cursor_apply_routes_to_shared_settings(self):
        component = {"provides": ["cursors"], "name": "Theme", "path": str(self.home)}
        with (
            mock.patch.object(desktop, "running_wm"),
            mock.patch.object(desktop, "compatible_parts", return_value=["cursors"]),
            mock.patch.object(desktop, "set_", return_value=True),
            mock.patch.object(desktop, "_set_default_cursor") as shared,
        ):
            self.assertEqual(desktop.apply_component(component), ["cursors"])
            shared.assert_called_once_with("Theme")

    def test_control_characters_do_not_enter_resources(self):
        with self.assertRaises(ValueError):
            cursors.apply("Theme\nInjected.resource: value", 24)
        self.assertFalse((self.home / ".Xresources").exists())
