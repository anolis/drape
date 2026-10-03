import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape import cli, desktop, installer, pling
from drape.ui.packs import choices
from drape.ui.widgets import ApplyControl


class ThemePacksTest(unittest.TestCase):
    def session(self, name, wm, supported):
        patches = [
            mock.patch.object(desktop, "current_desktop", return_value=name),
            mock.patch.object(desktop, "running_wm", return_value=wm),
            mock.patch.object(
                desktop,
                "border_part",
                return_value={
                    "muffin": None,
                    "xfwm4": "xfwm",
                    "marco": "wm",
                    "kwin": "aurorae",
                }.get(wm),
            ),
            mock.patch.object(
                desktop, "supported", side_effect=lambda part: part == "packs" or part in supported
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_cinnamon_filters_real_bundles_and_excludes_other_desktops(self):
        self.session("cinnamon", "muffin", {"gtk", "desktop", "icons", "cursors", "wallpapers"})
        for parts, expected in [
            ({"gtk", "gtk-3.0", "desktop"}, True),
            ({"gtk", "gtk-3.0", "icons"}, True),
            ({"gtk", "gtk-3.0", "wm"}, False),
            ({"gtk", "gtk-4.0", "desktop"}, False),
            ({"icons", "cursors"}, False),
            ({"lookandfeel", "plasma"}, False),
        ]:
            if "desktop" in parts:
                parts.add("cinnamon-modern")
            self.assertEqual(desktop.archive_compatible(parts, True, "packs"), expected, parts)
        self.assertFalse(desktop.archive_compatible(set(), False, "packs"))
        self.assertTrue(
            desktop.archive_compatible(
                {"gtk", "gtk-3.0", "desktop", "cinnamon-modern"}, False, "packs"
            )
        )
        for only in (True, False):
            self.assertEqual(set(desktop.scope("packs", only)[0].split(",")), {"135", "133"})

    def test_xfce_accepts_controls_and_xfwm_but_not_cinnamon(self):
        self.session("xfce", "xfwm4", {"gtk", "xfwm", "wm", "icons"})
        self.assertTrue(desktop.archive_compatible({"gtk", "gtk-3.0", "xfwm"}, True, "packs"))
        self.assertFalse(desktop.archive_compatible({"gtk", "gtk-3.0", "desktop"}, True, "packs"))
        self.assertEqual(set(desktop.scope("packs")[0].split(",")), {"135", "138"})

    def test_kde_uses_matching_global_theme_catalog_and_rejects_cinnamon(self):
        self.session(
            "kde", "kwin", {"lookandfeel", "desktop", "plasma", "colors", "wm", "aurorae", "icons"}
        )
        with mock.patch.object(desktop.kde, "major_version", return_value=6):
            categories = set(desktop.scope("packs")[0].split(","))
        self.assertEqual(categories, {"722", "104", "112", "114", "717"})
        self.assertTrue(desktop.archive_compatible({"lookandfeel"}, True, "packs"))
        self.assertFalse(desktop.archive_compatible({"desktop", "icons"}, True, "packs"))

    def test_apply_control_routes_to_pack_selection(self):
        window = SimpleNamespace(apply_pack=mock.Mock())
        ApplyControl._apply(SimpleNamespace(kind="packs", key="1", win=window), None)
        window.apply_pack.assert_called_once_with("1")

    def test_variant_choices_are_grouped_and_unsupported_parts_excluded(self):
        self.session("cinnamon", "muffin", {"gtk", "desktop", "icons"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "gtk-3.0").mkdir()
            (root / "cinnamon").mkdir()
            (root / "cinnamon/cinnamon.css").write_text(".dialog {} .modal-dialog {}")
            components = [
                dict(name=name, path=str(root), provides=["gtk", "desktop", "wm"])
                for name in ("Dark", "Light")
            ]
            groups = choices({"components": components})
            self.assertEqual(set(groups), {"gtk", "desktop"})
            self.assertEqual([c["name"] for c in groups["gtk"]], ["Dark", "Light"])
            self.assertTrue(desktop.pack_components(components))

    def test_install_rechecks_bundle_before_writing_files(self):
        self.session("cinnamon", "muffin", {"gtk", "desktop", "icons"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "pack.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("Theme/gtk-3.0/gtk.css", "/* controls */")
                output.writestr("Theme/metacity-1/metacity-theme-3.xml", "<theme/>")
            with (
                mock.patch.object(installer, "MANIFEST", root / "records.json"),
                mock.patch.object(installer, "THEMES_DIR", root / "themes"),
            ):
                with self.assertRaises(installer.IncompatibleError):
                    installer.install_file(
                        archive, "1", "Theme", required_kind="packs", only_applicable=False
                    )
                self.assertFalse((root / "themes").exists())
                self.assertEqual(installer.load_manifest(), {})
            with zipfile.ZipFile(archive, "a") as output:
                output.writestr("Theme/cinnamon/cinnamon.css", ".dialog { background: #222; }")
            with (
                mock.patch.object(installer, "MANIFEST", root / "records.json"),
                mock.patch.object(installer, "THEMES_DIR", root / "themes"),
            ):
                entry = installer.install_file(archive, "1", "Theme", required_kind="packs")
                self.assertTrue(desktop.pack_components(entry["components"]))

    def test_theme_pack_category_registered(self):
        self.assertEqual(pling.KINDS_BY_KEY["packs"].label, "Theme packs")

    def test_cli_pack_search_does_not_admit_unverified_single_themes(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr

        self.session("cinnamon", "muffin", {"gtk", "desktop", "icons"})
        items = [
            pling.Item(
                str(i),
                name,
                "",
                "",
                "",
                "",
                0,
                0,
                "",
                files=[pling.Download(1, "theme.zip", "url", 0, "")],
            )
            for i, name in enumerate(("Bundle", "Single", "Unknown"))
        ]
        results = [
            ({"gtk", "gtk-3.0", "desktop", "cinnamon-modern"}, True),
            ({"gtk", "gtk-3.0"}, True),
            (set(), False),
        ]
        args = SimpleNamespace(
            kind="packs", all_themes=True, query="", sort="top", page=0, limit=10
        )
        output = io.StringIO()
        with (
            mock.patch.object(pling, "search", return_value=(items, 3)),
            mock.patch.object(
                cli.peek, "inspect_downloads", side_effect=[{1: result} for result in results]
            ),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            cli.cmd_search(args)
        self.assertIn("Bundle", output.getvalue())
        self.assertNotIn("Single", output.getvalue())
        self.assertNotIn("Unknown", output.getvalue())

    def test_theme_and_wallpaper_bundle_installs_both_without_previews(self):
        self.session("cinnamon", "muffin", {"gtk", "desktop", "icons", "wallpapers"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("Theme/gtk-3.0/gtk.css", "/* theme */")
                output.writestr("Theme/gtk-3.0/assets/button.png", "asset")
                output.writestr("Theme/wallpapers/background.jpg", "wallpaper")
                output.writestr("preview.png", "preview")
            with (
                mock.patch.object(installer, "MANIFEST", root / "records.json"),
                mock.patch.object(installer, "THEMES_DIR", root / "themes"),
                mock.patch.object(installer, "WALLPAPER_DIR", root / "backgrounds"),
                mock.patch.object(installer, "_register_wallpaper_folder"),
            ):
                entry = installer.install_file(archive, "1", "Bundle", required_kind="packs")
                self.assertEqual(
                    [c["provides"] for c in entry["components"]], [["gtk"], ["wallpapers"]]
                )
                wallpaper = entry["components"][1]
                self.assertEqual(wallpaper["name"], "background.jpg")
                self.assertTrue(Path(wallpaper["path"]).is_file())
