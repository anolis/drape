import configparser
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import desktop, installer, peek, pling, qt
from tests.test_installer import make_zip


class QtTest(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {"ZDOTDIR": ""})
        environment.start()
        self.addCleanup(environment.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = self.home / ".config"
        self.themes = self.config / "Kvantum"
        for key, value in {
            "HOME": self.home,
            "CONFIG_HOME": self.config,
            "DATA_HOME": self.home / ".local/share",
            "THEMES_DIR": self.themes,
        }.items():
            patch = mock.patch.object(qt, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(qt.refresh)
        qt.refresh()

    def make_theme(self, name="Example"):
        folder = self.themes / name
        folder.mkdir(parents=True)
        (folder / (name + ".kvconfig")).write_text("[General]\n")
        (folder / (name + ".svg")).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        return folder

    def test_engine_detection_requires_plugin_for_each_major(self):
        lib = self.home / "lib"
        five = lib / "x86_64-linux-gnu/qt5/plugins/styles/libkvantum.so"
        six = lib / "qt6/plugins/styles/libkvantum.so"
        with mock.patch.object(qt, "LIB_DIRS", (lib,)):
            self.assertEqual(qt.engines(), set())
            five.parent.mkdir(parents=True)
            five.touch()
            qt.refresh()
            self.assertEqual(qt.engines(), {5})
            six.parent.mkdir(parents=True)
            six.touch()
            qt.refresh()
            self.assertEqual(qt.engines(), {5, 6})

    def test_qt_supported_independently_of_desktop_and_manager(self):
        with mock.patch.object(qt, "engines", return_value={6}):
            for de in ("cinnamon", "gnome", "xfce", "kde", None):
                with (
                    self.subTest(desktop=de),
                    mock.patch.object(desktop, "current_desktop", return_value=de),
                ):
                    self.assertTrue(desktop.supported("kvantum"))
                    self.assertTrue(desktop.category_visible("kvantum", True))
                    self.assertEqual(desktop.scope("kvantum")[0], "123")
        with mock.patch.object(qt, "engines", return_value=set()):
            self.assertFalse(desktop.category_visible("kvantum", False))

    def test_apply_preserves_assignments_and_profile_and_is_idempotent(self):
        self.make_theme()
        config = self.themes / "kvantum.kvconfig"
        config.write_text("[General]\ntheme=Previous\n[Applications]\nSpecial=one,two\n")
        profile = self.home / ".profile"
        profile.write_text("# My login setup\nexport OTHER=value\n")
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt.shutil, "which", return_value=None),
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            self.assertTrue(desktop.set_("kvantum", "Example"))
            first = profile.read_text()
            self.assertTrue(qt.enable("Example"))
            self.assertEqual(profile.read_text(), first)
            self.assertEqual(os.environ["QT_STYLE_OVERRIDE"], "kvantum")
            self.assertEqual(desktop.get("kvantum"), "Example")
        parser = configparser.ConfigParser()
        parser.read(config)
        self.assertEqual(parser["Applications"]["Special"], "one,two")
        self.assertIn("export OTHER=value", first)
        self.assertEqual(first.count("BEGIN DRAPE QT STYLE"), 1)
        self.assertIn(
            "QT_STYLE_OVERRIDE=kvantum",
            (self.config / "environment.d/90-drape-qt.conf").read_text(),
        )

    def test_failed_setup_restores_previous_files(self):
        self.make_theme()
        profile = self.home / ".profile"
        profile.write_text("old profile\n")
        original = qt._atomic_text

        def write(path, text):
            if path == profile and text != "old profile\n":
                raise OSError("disk full")
            original(path, text)

        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt, "_atomic_text", side_effect=write),
            self.assertRaises(OSError),
        ):
            qt.enable("Example")
        self.assertEqual(profile.read_text(), "old profile\n")
        self.assertFalse((self.themes / "kvantum.kvconfig").exists())
        self.assertFalse((self.config / "environment.d/90-drape-qt.conf").exists())

    def test_disable_restores_user_setup_and_previous_theme(self):
        self.make_theme()
        profile = self.home / ".profile"
        profile.write_text("export OTHER=value\nexport QT_STYLE_OVERRIDE=Fusion\n")
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt.shutil, "which", return_value=None),
            mock.patch.dict(os.environ, {"QT_STYLE_OVERRIDE": "Fusion"}),
        ):
            qt.enable("Example")
            qt.disable()
            self.assertEqual(os.environ["QT_STYLE_OVERRIDE"], "Fusion")
        self.assertIn("export QT_STYLE_OVERRIDE=Fusion", profile.read_text())
        self.assertNotIn("DRAPE QT STYLE", profile.read_text())
        self.assertEqual(qt.get(), "")
        self.assertFalse((self.config / "environment.d/90-drape-qt.conf").exists())

    def test_restore_keeps_first_baseline_across_theme_switches(self):
        self.make_theme()
        self.make_theme("Other")
        config = self.themes / "kvantum.kvconfig"
        original = "[General]\ntheme=Previous\n[Applications]\nSpecial=one,two\n"
        config.write_text(original)
        environment = self.config / "environment.d/90-drape-qt.conf"
        environment.parent.mkdir(parents=True)
        environment.write_text("QT_STYLE_OVERRIDE=Fusion\nOTHER=yes\n")
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt, "_activation_style"),
            mock.patch.dict(os.environ),
        ):
            qt.enable("Example")
            qt.enable("Other")
            qt.disable()
        self.assertEqual(config.read_text(), original)
        self.assertEqual(environment.read_text(), "QT_STYLE_OVERRIDE=Fusion\nOTHER=yes\n")
        self.assertFalse(qt.restore_available())
        self.assertFalse((self.home / ".profile").exists())

    def test_restore_preserves_later_unrelated_edits(self):
        self.make_theme()
        config = self.themes / "kvantum.kvconfig"
        config.write_text("[General]\ntheme=Previous\n")
        profile = self.home / ".profile"
        profile.write_text("export ORIGINAL=yes\n")
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt, "_activation_style"),
            mock.patch.dict(os.environ),
        ):
            qt.enable("Example")
            profile.write_text(profile.read_text() + "export NEW_SETTING=yes\n")
            config.write_text(config.read_text() + "[Applications]\nSpecial=one,two\n")
            qt.disable()
        self.assertIn("export ORIGINAL=yes", profile.read_text())
        self.assertIn("export NEW_SETTING=yes", profile.read_text())
        self.assertNotIn("DRAPE QT STYLE", profile.read_text())
        self.assertEqual(qt.get(), "Previous")
        self.assertIn("Special=one,two", config.read_text())

    def test_restore_conflict_changes_nothing_and_keeps_backup(self):
        self.make_theme()
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt, "_activation_style"),
            mock.patch.dict(os.environ),
        ):
            qt.enable("Example")
            environment = self.config / "environment.d/90-drape-qt.conf"
            environment.write_text("QT_STYLE_OVERRIDE=Fusion\n")
            profile = self.home / ".profile"
            original = profile.read_text()
            with self.assertRaisesRegex(ValueError, "changed outside Drape"):
                qt.disable()
        self.assertEqual(profile.read_text(), original)
        self.assertEqual(environment.read_text(), "QT_STYLE_OVERRIDE=Fusion\n")
        self.assertTrue(qt.restore_available())

    def test_legacy_setup_does_not_masquerade_as_an_original_backup(self):
        self.make_theme()
        profile = self.home / ".profile"
        profile.write_text(
            "export ORIGINAL=yes\n\n# BEGIN DRAPE QT STYLE\nexport QT_STYLE_OVERRIDE=kvantum\n# END DRAPE QT STYLE\n"
        )
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt, "_activation_style"),
            mock.patch.dict(os.environ),
        ):
            qt.enable("Example")
            self.assertIn("cannot be recovered", qt.restore_description())
            qt.disable()
        self.assertNotIn("DRAPE QT STYLE", profile.read_text())
        self.assertIn("ORIGINAL=yes", profile.read_text())

    def test_restore_write_failure_rolls_back_and_keeps_backup(self):
        self.make_theme()
        profile = self.home / ".profile"
        profile.write_text("original\n")
        with (
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt, "_activation_style"),
            mock.patch.dict(os.environ),
        ):
            qt.enable("Example")
            applied = profile.read_text()
            write = qt._atomic_text

            def fail(path, text):
                if path == profile and text == "original\n":
                    raise OSError("disk full")
                write(path, text)

            with (
                mock.patch.object(qt, "_atomic_text", side_effect=fail),
                self.assertRaises(OSError),
            ):
                qt.disable()
        self.assertEqual(profile.read_text(), applied)
        self.assertEqual(qt.get(), "Example")
        self.assertTrue(qt.restore_available())

    def test_session_status_uses_desktop_environment_not_drape_environment(self):
        proc = self.home / "proc"
        proc.mkdir()
        with (
            mock.patch.object(qt, "PROC", proc),
            mock.patch.dict(os.environ, {"QT_STYLE_OVERRIDE": "kvantum"}),
        ):
            self.assertIsNone(qt.session_active())
            cinnamon = proc / "123"
            cinnamon.mkdir()
            (cinnamon / "cmdline").write_bytes(b"/usr/bin/cinnamon\0--replace\0")
            (cinnamon / "environ").write_bytes(b"SHELL=/bin/zsh\0")
            self.assertFalse(qt.session_active())
            (cinnamon / "environ").write_bytes(b"QT_STYLE_OVERRIDE=kvantum\0")
            self.assertTrue(qt.session_active())
            (cinnamon / "environ").unlink()
            self.assertIsNone(qt.session_active())

    def test_partial_theme_cannot_be_applied(self):
        folder = self.make_theme()
        (folder / "Example.svg").unlink()
        with mock.patch.object(qt, "engines", return_value={6}):
            self.assertEqual(
                desktop.compatible_parts(
                    {"provides": ["kvantum"], "name": "Example", "path": str(folder)}
                ),
                [],
            )
            with self.assertRaises(desktop.ApplyError):
                desktop.set_("kvantum", "Example")
        self.assertFalse((self.home / ".profile").exists())

    def test_archive_detection_requires_matching_pair_and_excludes_artwork(self):
        self.assertEqual(
            peek.classify_names(
                {
                    "T/One.kvconfig",
                    "T/One.svg",
                    "T/Two.kvconfig",
                    "T/Two.svg",
                    "T/Three.kvconfig",
                    "T/Three.svg",
                }
            ),
            {"kvantum"},
        )
        self.assertNotIn("kvantum", peek.classify_names({"T/One.kvconfig", "T/Other.svg"}))
        self.assertIn("kvantum", pling.KINDS_BY_KEY)

    def test_nested_companion_and_variant_names(self):
        root = self.home / "bundle"
        (root / "gtk-3.0").mkdir(parents=True)
        folder = root / "Kvantum" / "Themes"
        folder.mkdir(parents=True)
        for name in ("Example", "ExampleDark"):
            (folder / (name + ".kvconfig")).write_text("[General]\n")
            (folder / (name + ".svg")).write_text("svg")
        components = installer.classify(root, "Bundle", include_wallpapers=True)
        self.assertEqual(
            [(c.provides, c.name) for c in components],
            [(("gtk",), "Bundle"), (("kvantum",), "Example"), (("kvantum",), "ExampleDark")],
        )

    def test_install_collision_renames_paired_files_and_removal_is_safe(self):
        archive = self.home / "themes.zip"
        make_zip(
            archive,
            {
                f"{folder}/Example{ext}": b"[General]\n" if ext == ".kvconfig" else b"svg"
                for folder in ("One", "Two")
                for ext in (".kvconfig", ".svg")
            },
        )
        with (
            mock.patch.object(installer, "MANIFEST", self.home / "manifest.json"),
            mock.patch.object(qt, "engines", return_value={6}),
        ):
            entry = installer.install_file(
                archive, "test", "Example", required_kind="kvantum", only_applicable=True
            )
            self.assertEqual([c["name"] for c in entry["components"]], ["Example", "Example-2"])
            for component in entry["components"]:
                self.assertEqual(desktop.compatible_parts(component), ["kvantum"])
                self.assertTrue((Path(component["path"]) / (component["name"] + ".svg")).is_file())
            installer.remove("test")
            self.assertFalse((self.themes / "Example").exists())
            self.assertFalse((self.themes / "Example-2").exists())

    def test_qt_and_icons_form_an_applicable_pack(self):
        archive = self.home / "bundle.zip"
        make_zip(
            archive,
            {
                "UnrelatedFolder/Example.kvconfig": b"[General]\n",
                "UnrelatedFolder/Example.svg": b"svg",
                "Icons/index.theme": b"[Icon Theme]\nName=Icons\n",
            },
        )
        with (
            mock.patch.object(installer, "MANIFEST", self.home / "manifest.json"),
            mock.patch.object(installer, "ICONS_DIR", self.home / "icons"),
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(
                desktop, "supported", side_effect=lambda part: part in {"kvantum", "icons"}
            ),
            mock.patch.object(installer.shutil, "which", return_value=None),
        ):
            entry = installer.install_file(archive, "pack", "Bundle", required_kind="packs")
            self.assertTrue(desktop.pack_components(entry["components"]))
            self.assertEqual(
                {p for c in entry["components"] for p in desktop.compatible_parts(c)},
                {"kvantum", "icons"},
            )

    def test_repository_commands_and_removal_plan_rejection(self):
        with (
            mock.patch.object(
                qt.platform,
                "freedesktop_os_release",
                return_value={"ID": "cachyos", "ID_LIKE": "arch"},
            ),
            mock.patch.object(qt.shutil, "which", return_value="/usr/bin/pacman"),
        ):
            self.assertEqual(qt.install_command(5)[-1], "kvantum-qt5")
            self.assertEqual(qt.install_command(6)[-1], "kvantum")
        with (
            mock.patch.object(
                qt.platform,
                "freedesktop_os_release",
                return_value={"ID": "linuxmint", "ID_LIKE": "ubuntu debian"},
            ),
            mock.patch.object(qt.shutil, "which", return_value="/usr/bin/apt-get"),
        ):
            command = qt.install_command(6)
            self.assertEqual(command[-1], "qt6-style-kvantum")
            with (
                mock.patch.object(
                    qt.subprocess,
                    "run",
                    return_value=mock.Mock(
                        returncode=0, stdout="Remv qt5-style-kvantum [1.0]\n", stderr=""
                    ),
                ),
                self.assertRaises(ValueError),
            ):
                qt.check_install(command)
