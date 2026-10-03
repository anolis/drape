import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from drape import desktop, installer, kde, peek
from drape.ui.common import matches


def package_metadata(kind, name="org.example.test"):
    return json.dumps({"KPackageStructure": kind, "KPlugin": {"Id": name}})


class KdeBackendTest(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "KDE_SESSION_VERSION": "6"}),
            mock.patch.object(desktop, "running_wm", return_value="KWin"),
            mock.patch.object(kde, "tool", side_effect=lambda name: "/usr/bin/" + name),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_kde_categories_and_format_mapping(self):
        self.assertEqual(desktop.current_desktop(), "kde")
        self.assertEqual(desktop.catalog_name(), "KDE-Look.org")
        for kind, cats in (("desktop", "104"), ("wm", "114,717"), ("lookandfeel", "722")):
            self.assertEqual(desktop.scope(kind)[0], cats)
        self.assertEqual(desktop.theme_part("desktop"), "plasma")
        self.assertEqual(desktop.theme_part("wm"), "aurorae")
        self.assertTrue(matches("desktop", {"provides": ["plasma"]}))
        self.assertFalse(matches("desktop", {"provides": ["desktop"]}))
        self.assertFalse(desktop.supported("gtk"))
        with mock.patch.dict(os.environ, {"KDE_SESSION_VERSION": "5"}):
            self.assertEqual(desktop.scope("lookandfeel")[0], "121")
            self.assertEqual(desktop.scope("wm")[0], "114")

    def test_cinnamon_and_metacity_archives_are_not_kde_themes(self):
        self.assertFalse(desktop.archive_compatible({"desktop"}, True, "desktop"))
        self.assertFalse(desktop.archive_compatible({"wm"}, True, "wm"))
        self.assertTrue(desktop.archive_compatible({"plasma"}, True, "desktop"))
        self.assertTrue(desktop.archive_compatible({"aurorae"}, True, "wm"))
        self.assertEqual(
            desktop.compatible_parts({"path": "/tmp/theme", "provides": ["desktop", "wm"]}), []
        )

    def test_native_apply_commands_do_not_touch_gnome_settings(self):
        with (
            mock.patch.object(kde.subprocess, "run", return_value=mock.Mock(returncode=0)) as run,
            mock.patch.object(desktop.Gio.Settings, "new") as settings,
        ):
            self.assertTrue(desktop.set_("desktop", "Test"))
            self.assertEqual(run.call_args.args[0], ["/usr/bin/plasma-apply-desktoptheme", "Test"])
            self.assertTrue(desktop.set_("colors", "Dark"))
            self.assertEqual(run.call_args.args[0], ["/usr/bin/plasma-apply-colorscheme", "Dark"])
            self.assertTrue(desktop.set_("icons", "TestIcons"))
            self.assertEqual(run.call_args.args[0], ["/usr/bin/plasma-changeicons", "TestIcons"])
            self.assertTrue(desktop.set_("cursors", "TestCursors"))
            self.assertEqual(
                run.call_args.args[0], ["/usr/bin/plasma-apply-cursortheme", "TestCursors"]
            )
            self.assertTrue(desktop.set_("lookandfeel", "org.example.test"))
            self.assertEqual(
                run.call_args.args[0],
                ["/usr/bin/plasma-apply-lookandfeel", "--apply", "org.example.test"],
            )
            self.assertNotIn("--resetLayout", run.call_args.args[0])
            settings.assert_not_called()

    def test_wallpaper_uri_is_decoded_and_passed_as_one_argument(self):
        path = Path('/tmp/a #1 "quoted" café.png')
        with mock.patch.object(kde.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(desktop.set_("wallpapers", path.as_uri()))
            self.assertEqual(
                run.call_args.args[0], ["/usr/bin/plasma-apply-wallpaperimage", str(path)]
            )
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_apply_failure_is_reported(self):
        with mock.patch.object(
            kde.subprocess,
            "run",
            return_value=mock.Mock(returncode=1, stderr="Theme not found", stdout=""),
        ):
            with self.assertRaisesRegex(kde.ApplyError, "Theme not found"):
                desktop.set_("desktop", "Missing")

    def test_missing_tools_disable_the_corresponding_part(self):
        with mock.patch.object(kde, "tool", return_value=None):
            self.assertFalse(desktop.supported("desktop"))
            self.assertFalse(desktop.supported("wm"))
            self.assertFalse(desktop.set_("icons", "Test"))

    def test_read_current_theme_and_strip_aurorae_prefix(self):
        with mock.patch.object(
            kde.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout="__aurorae__svg__Test\n"),
        ) as run:
            self.assertEqual(desktop.get("wm"), "Test")
            self.assertEqual(
                run.call_args.args[0],
                [
                    "/usr/bin/kreadconfig6",
                    "--file",
                    "kwinrc",
                    "--group",
                    "org.kde.kdecoration2",
                    "--key",
                    "theme",
                ],
            )


class KdeInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patches = [
            mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "KDE_SESSION_VERSION": "6"}),
            mock.patch.object(kde, "DATA_HOME", self.root / "data"),
            mock.patch.object(kde, "tool", side_effect=lambda name: "/usr/bin/" + name),
            mock.patch.object(desktop, "running_wm", return_value="KWin"),
            mock.patch.object(installer, "MANIFEST", self.root / "manifest.json"),
            mock.patch.object(installer, "THEMES_DIR", self.root / "themes"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def archive(self, files):
        path = self.root / "pack.zip"
        with zipfile.ZipFile(path, "w") as z:
            for name, body in files.items():
                z.writestr(name, body)
        return path

    def test_mixed_kde_pack_goes_to_native_directories_and_removes_cleanly(self):
        archive = self.archive(
            {
                "plasma/desktoptheme/Style/metadata.json": package_metadata(
                    "Plasma/Theme", "style.id"
                ),
                "plasma/desktoptheme/Style/widgets/background.svg": "<svg/>",
                "plasma/look-and-feel/Global/metadata.json": package_metadata(
                    "Plasma/LookAndFeel", "global.id"
                ),
                "plasma/look-and-feel/Global/contents/defaults": "[kdeglobals][General]\nColorScheme=Test",
                "aurorae/themes/Borders/metadata.desktop": "[Desktop Entry]\nX-KDE-PluginInfo-Name=Borders\n",
                "aurorae/themes/Borders/decoration.svg": "<svg/>",
                "aurorae/themes/Borders/Bordersrc": "[General]\n",
                "color-schemes/Test.colors": "[Colors:Window]\nBackgroundNormal=1,2,3\n",
                "Cinnamon/cinnamon/cinnamon.css": "",
            }
        )
        with mock.patch.object(kde.subprocess, "run") as run:
            entry = installer.install_file(archive, "pack", "Pack", only_applicable=True)
            run.assert_not_called()  # installing packages does not execute their code or apply them
        expected = {
            self.root / "data/plasma/desktoptheme/style.id",
            self.root / "data/plasma/look-and-feel/global.id",
            self.root / "data/aurorae/themes/Borders",
            self.root / "data/color-schemes/Test.colors",
        }
        self.assertEqual({Path(c["path"]) for c in entry["components"]}, expected)
        self.assertEqual(entry["skipped"], ["Cinnamon"])
        self.assertTrue(all(p.exists() for p in expected))
        installer.remove("pack")
        self.assertTrue(all(not p.exists() for p in expected))

    def test_standalone_color_scheme(self):
        file = self.root / "Test.colors"
        file.write_text("[Colors:Window]\nBackgroundNormal=1,2,3\n")
        e = installer.install_file(file, "color", "Color", only_applicable=True)
        self.assertEqual(e["components"][0]["name"], "Test")
        self.assertEqual(Path(e["paths"][0]), self.root / "data/color-schemes/Test.colors")

    def test_plasma5_global_theme_rejected_on_plasma6(self):
        archive = self.archive(
            {
                "Old/metadata.desktop": "[Desktop Entry]\nX-KDE-ServiceTypes=Plasma/LookAndFeel\n",
                "Old/contents/defaults": "[kdeglobals]\n",
            }
        )
        with self.assertRaises(installer.IncompatibleError):
            installer.install_file(archive, "old", "Old", only_applicable=True)
        self.assertFalse(installer.MANIFEST.exists())

    def test_package_id_cannot_escape_install_root(self):
        archive = self.archive(
            {
                "Bad/metadata.json": package_metadata("Plasma/Theme", "../../escape"),
                "Bad/widgets/background.svg": "",
            }
        )
        with self.assertRaisesRegex(installer.InstallError, "Invalid KDE theme ID"):
            installer.install_file(archive, "bad", "Bad", only_applicable=True)
        self.assertFalse(installer.MANIFEST.exists())

    def test_archive_listing_distinguishes_kde_formats(self):
        self.assertEqual(
            peek.classify_names(["Style/metadata.json", "Style/widgets/background.svg"]), {"plasma"}
        )
        self.assertEqual(
            peek.classify_names(["Global/metadata.json", "Global/contents/defaults"]),
            {"lookandfeel"},
        )
        self.assertEqual(
            peek.classify_names(["Borders/decoration.svg", "Borders/Bordersrc"]), {"aurorae"}
        )
        self.assertEqual(peek.classify_names(["Test.colors"]), {"colors"})


class KdeWaylandTest(unittest.TestCase):
    def test_detects_kwin_on_wayland_via_session_bus(self):
        with (
            mock.patch.dict(
                os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
            ),
            mock.patch.object(desktop, "_wm_cache", (None, 0)),
            mock.patch.object(kde, "kwin_running", return_value=True),
            mock.patch.object(desktop.subprocess, "run") as run,
        ):
            self.assertEqual(desktop.running_wm(), "KWin")
            self.assertEqual(desktop.border_part(), "aurorae")
            run.assert_not_called()
