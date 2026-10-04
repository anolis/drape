import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import desktop, installer, libadwaita
from tests.test_installer import make_zip


class LibadwaitaTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = self.home / ".config"
        self.css = self.config / "gtk-4.0/gtk.css"
        for key, value in (("HOME", self.home), ("CONFIG_HOME", self.config)):
            patch = mock.patch.object(libadwaita, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(libadwaita, "dark_preferred", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def theme(self, name="Example", css="window { color: #123456; }"):
        folder = self.home / ".themes" / name / "gtk-4.0"
        folder.mkdir(parents=True)
        (folder / "gtk.css").write_text(css)
        return folder

    def test_real_parser_rejects_invalid_css_without_writing_settings(self):
        self.theme(css="window { not-a-property: no; }")
        with self.assertRaisesRegex(ValueError, "No property named"):
            libadwaita.apply("Example")
        self.assertFalse(self.css.exists())
        self.assertFalse(libadwaita.configured())

    def test_apply_and_restore_preserve_first_original_and_other_settings(self):
        self.theme()
        self.theme("Other")
        self.css.parent.mkdir(parents=True)
        self.css.write_text("/* user CSS */\n")
        self.css.chmod(0o640)
        settings = self.css.parent / "settings.ini"
        settings.write_text("[Settings]\ngtk-font-name=Sans 12\n")
        libadwaita.apply("Example")
        self.assertEqual(libadwaita.get(), "Example")
        self.assertIn("file://", self.css.read_text())
        libadwaita.apply("Other")
        libadwaita.restore()
        self.assertEqual(self.css.read_text(), "/* user CSS */\n")
        self.assertEqual(self.css.stat().st_mode & 0o777, 0o640)
        self.assertIn("Sans 12", settings.read_text())
        self.assertFalse(libadwaita.configured())

    def test_dark_source_is_imported_through_user_gtk_css(self):
        folder = self.theme()
        (folder / "gtk-dark.css").write_text("window { color: #654321; }")
        with mock.patch.object(libadwaita, "dark_preferred", return_value=True):
            libadwaita.apply("Example")
        self.assertIn("gtk-dark.css", self.css.read_text())
        self.assertFalse((self.css.parent / "gtk-dark.css").exists())
        libadwaita.restore()
        self.assertFalse(self.css.exists())

    def test_restore_preserves_symlink_and_does_not_write_its_target(self):
        self.theme()
        target = self.home / "my.css"
        target.write_text("/* my stylesheet */")
        self.css.parent.mkdir(parents=True)
        self.css.symlink_to(target)
        libadwaita.apply("Example")
        self.assertFalse(self.css.is_symlink())
        self.assertEqual(target.read_text(), "/* my stylesheet */")
        libadwaita.restore()
        self.assertTrue(self.css.is_symlink())
        self.assertEqual(os.readlink(self.css), str(target))

    def test_external_edit_blocks_reapply_and_restore_without_losing_backup(self):
        self.theme()
        libadwaita.apply("Example")
        self.css.write_text("/* new user edit */")
        self.assertEqual(libadwaita.get(), "")
        with self.assertRaisesRegex(ValueError, "outside Drape"):
            libadwaita.restore()
        with self.assertRaisesRegex(ValueError, "outside Drape"):
            libadwaita.apply("Example")
        self.assertEqual(self.css.read_text(), "/* new user edit */")
        self.assertTrue(libadwaita.configured())

    def test_failed_write_rolls_back_setup(self):
        self.theme()
        original_write = libadwaita._write

        def fail(path, snapshot):
            if path == self.css and snapshot:
                raise OSError("disk full")
            original_write(path, snapshot)

        with mock.patch.object(libadwaita, "_write", side_effect=fail), self.assertRaises(OSError):
            libadwaita.apply("Example")
        self.assertFalse(self.css.exists())
        self.assertFalse(libadwaita.configured())

    def test_native_compatibility_requires_gnome_and_real_gtk4_css(self):
        folder = self.theme()
        component = {"name": "Example", "path": str(folder.parent), "provides": ["gtk"]}
        with (
            mock.patch.object(desktop, "current_desktop", return_value="gnome"),
            mock.patch.object(libadwaita, "available", return_value=True),
        ):
            self.assertIn("libadwaita", desktop.compatible_parts(component))
            self.assertEqual(
                desktop.archive_status({"gtk", "gtk-4.0"}, True, "libadwaita"), "compatible"
            )
            self.assertEqual(
                desktop.archive_status({"gtk", "gtk-3.0"}, True, "libadwaita"), "incompatible"
            )
            self.assertEqual(desktop.archive_status(set(), False, "libadwaita"), "unknown")
            with (
                mock.patch.object(desktop, "running_wm"),
                mock.patch.object(desktop, "set_", return_value=True) as apply,
            ):
                self.assertEqual(desktop.apply_component(component), [])
                apply.assert_not_called()
                self.assertEqual(desktop.apply_component(component, ["libadwaita"]), ["libadwaita"])
        with mock.patch.object(desktop, "current_desktop", return_value="cinnamon"):
            self.assertFalse(desktop.category_visible("libadwaita"))

    def test_native_install_rejects_gtk3_only_and_accepts_gtk4_only(self):
        archive = self.home / "theme.zip"
        with (
            mock.patch.object(desktop, "current_desktop", return_value="gnome"),
            mock.patch.object(libadwaita, "available", return_value=True),
            mock.patch.object(installer, "THEMES_DIR", self.home / ".themes"),
            mock.patch.object(installer, "MANIFEST", self.home / "manifest.json"),
        ):
            make_zip(archive, {"Theme/gtk-3.0/gtk.css": b"window {}"})
            with self.assertRaisesRegex(installer.IncompatibleError, "no usable GTK 4"):
                installer.install_file(archive, "1", "Theme", required_kind="libadwaita")
            make_zip(archive, {"Theme/gtk-4.0/gtk.css": b"window {}"})
            entry = installer.install_file(archive, "1", "Theme", required_kind="libadwaita")
            self.assertIn("libadwaita", desktop.compatible_parts(entry["components"][0]))
