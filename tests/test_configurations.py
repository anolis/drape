import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import configurations, desktop
from drape.configuration_store import ConfigurationError, ConfigurationStore


class ConfigurationStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "drape/configurations.json"
        self.store = ConfigurationStore(self.path)
        self.snapshot = {
            "desktop": "gnome",
            "wm": "Mutter",
            "components": [
                {"part": "gtk", "value": "Adwaita-dark"},
                {"part": "icons", "value": "Papirus"},
            ],
        }

    def test_saved_selections_survive_reload_and_independent_writers(self):
        key = self.store.save("Evening", self.snapshot)
        other = ConfigurationStore(self.path)
        second = other.save(
            "Daytime", dict(self.snapshot, components=[{"part": "gtk", "value": "Adwaita"}])
        )
        self.snapshot["components"][0]["value"] = "Changed after saving"
        entries = self.store.load()
        self.assertEqual(set(entries), {key, second})
        self.assertEqual(entries[key]["components"][0]["value"], "Adwaita-dark")
        self.assertEqual(entries[key]["desktop"], "gnome")
        self.assertTrue(entries[key]["created"])

    def test_failed_write_keeps_original_and_valid_backup(self):
        from drape.configuration_store import _atomic_write

        key = self.store.save("Original", self.snapshot)
        original = self.path.read_bytes()

        def fail_current(path, text):
            if path == self.path:
                raise OSError("disk full")
            _atomic_write(path, text)

        with (
            mock.patch("drape.configuration_store._atomic_write", side_effect=fail_current),
            self.assertRaisesRegex(ConfigurationError, "disk full"),
        ):
            self.store.save("New", self.snapshot)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.backup.read_bytes(), original)
        self.assertEqual(set(self.store.load()), {key})

    def test_corrupt_records_are_not_reset_or_overwritten(self):
        self.store.save("First", self.snapshot)
        self.store.save("Second", self.snapshot)
        backup = self.store.backup.read_bytes()
        self.path.write_text("{bad json")
        with self.assertRaisesRegex(ConfigurationError, "not been overwritten"):
            self.store.save("Third", self.snapshot)
        self.assertEqual(self.path.read_text(), "{bad json")
        self.assertEqual(self.store.backup.read_bytes(), backup)

    def test_missing_records_with_backup_are_not_treated_as_empty(self):
        self.store.save("First", self.snapshot)
        self.store.save("Second", self.snapshot)
        self.path.unlink()
        with self.assertRaisesRegex(ConfigurationError, "Restore the backup"):
            self.store.load()

    def test_invalid_snapshots_and_duplicate_names_do_not_change_records(self):
        self.store.save("Evening", self.snapshot)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ConfigurationError, "already exists"):
            self.store.save(" evening ", self.snapshot)
        for components in (
            [],
            [{"part": "unsupported", "value": "Theme"}],
            [{"part": "gtk", "value": "../../outside"}],
            [{"part": "gtk", "value": "-option"}],
            [{"part": "icons", "value": "Theme"}] * 2,
        ):
            with self.assertRaises(ConfigurationError):
                self.store.save("Invalid", dict(self.snapshot, components=components))
        self.assertEqual(self.path.read_bytes(), before)

    def test_rename_and_delete_only_change_saved_records(self):
        theme = Path(self.temp.name) / "theme.css"
        theme.write_text("original theme files")
        key = self.store.save("First", self.snapshot)
        self.store.rename(key, "Renamed")
        renamed = self.store.load()[key]
        self.assertEqual(renamed["name"], "Renamed")
        self.assertEqual(renamed["components"], self.snapshot["components"])
        self.store.delete(key)
        self.assertEqual(self.store.load(), {})
        self.assertEqual(theme.read_text(), "original theme files")

    def test_invalid_loaded_records_cannot_reach_application(self):
        self.store.save("First", self.snapshot)
        data = json.loads(self.path.read_text())
        next(iter(data["configurations"].values()))["components"][0]["value"] = "../escape"
        self.path.write_text(json.dumps(data))
        with self.assertRaises(ConfigurationError):
            self.store.load()


class ConfigurationEnginesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch("running_wm", return_value="Mutter")
        self.patch("current_desktop", return_value="gnome")
        self.patch("border_part", return_value=None)
        self.supported = {"gtk", "icons", "cursors", "libadwaita", "kvantum", "wallpapers"}
        self.patch("supported", side_effect=lambda part: part in self.supported)
        self.values = {
            "gtk": "Graphite-Dark",
            "icons": "Papirus",
            "libadwaita": "Native-Dark",
            "wallpapers": (self.root / "wall paper.png").as_uri(),
        }
        self.get = self.patch("get", side_effect=lambda part: self.values.get(part))
        self.apply_component = self.patch("apply_component", side_effect=lambda comp, only: only)
        self.set_value = self.patch("set_", return_value=True)
        locate_patch = mock.patch.object(configurations, "locate", return_value=self.root)
        self.locate = locate_patch.start()
        self.addCleanup(locate_patch.stop)
        self.patch(
            "compatible_parts",
            side_effect=lambda comp: (
                [*comp["provides"], "libadwaita"] if "gtk" in comp["provides"] else comp["provides"]
            ),
        )

    def patch(self, name, **kwargs):
        patch = mock.patch.object(desktop, name, **kwargs)
        result = patch.start()
        self.addCleanup(patch.stop)
        return result

    def snapshot(self, *pairs):
        return {
            "desktop": "gnome",
            "wm": "Mutter",
            "components": [{"part": part, "value": value} for part, value in pairs],
        }

    def test_capture_reads_current_variants_instead_of_installed_pack_defaults(self):
        snapshot, warnings = configurations.capture()
        self.assertEqual({c["part"]: c["value"] for c in snapshot["components"]}, self.values)
        self.assertEqual(warnings, [])
        self.apply_component.assert_not_called()
        self.set_value.assert_not_called()

    def test_capture_uses_actual_engines_without_duplicate_desktop_aliases(self):
        self.supported = {"desktop", "plasma", "wm", "aurorae", "icons"}
        self.values = {
            "desktop": "Wrong alias",
            "plasma": "Breeze",
            "wm": "Wrong alias",
            "aurorae": "Decoration",
            "icons": "Papirus",
        }
        self.patch("current_desktop", return_value="kde")
        self.patch("border_part", return_value="aurorae")
        snapshot, _ = configurations.capture()
        self.assertEqual(
            [c["part"] for c in snapshot["components"]], ["plasma", "aurorae", "icons"]
        )
        self.get.assert_any_call("plasma")
        self.assertNotIn(mock.call("desktop"), self.get.call_args_list)

    def test_read_failure_keeps_other_captured_components_and_warns(self):
        self.get.side_effect = lambda part: (
            (_ for _ in ()).throw(OSError("read failed"))
            if part == "gtk"
            else self.values.get(part)
        )
        snapshot, warnings = configurations.capture()
        self.assertNotIn("gtk", [c["part"] for c in snapshot["components"]])
        self.assertIn("icons", [c["part"] for c in snapshot["components"]])
        self.assertIn("read failed", warnings[0])

    def test_apply_uses_only_checked_components_and_rechecks_files(self):
        snapshot = self.snapshot(("gtk", "Saved-Dark"), ("icons", "Papirus"))
        rows = configurations.review(snapshot)
        self.assertEqual(rows[0]["current"], "Graphite-Dark")
        self.assertEqual(rows[0]["reason"], "")
        applied, failed = configurations.apply(snapshot, ["icons"])
        self.assertEqual((applied, failed), (["icons"], []))
        self.assertEqual(self.apply_component.call_args.args[0]["name"], "Papirus")
        self.locate.return_value = self.root / "now removed"
        applied, failed = configurations.apply(snapshot, ["gtk"])
        self.assertEqual(applied, [])
        self.assertIn("missing", failed[0][1])
        self.assertEqual(self.apply_component.call_count, 1)

    def test_other_desktop_blocks_old_components_but_keeps_portable_icons(self):
        snapshot = self.snapshot(("desktop", "Cinnamon-Dark"), ("icons", "Papirus"))
        rows = configurations.review(snapshot)
        self.assertIn("Unsupported", rows[0]["reason"])
        applied, failed = configurations.apply(snapshot, ["desktop", "icons"])
        self.assertEqual(applied, ["icons"])
        self.assertEqual(failed[0][0], "desktop")

    def test_global_theme_is_applied_before_specific_saved_selections(self):
        self.supported |= {"lookandfeel", "colors"}
        snapshot = self.snapshot(
            ("icons", "Papirus"), ("colors", "Dark"), ("lookandfeel", "Global")
        )
        applied, failed = configurations.apply(snapshot, ["lookandfeel", "icons", "colors"])
        self.assertEqual(applied, ["lookandfeel", "colors", "icons"])
        self.assertEqual(failed, [])

    def test_incompatible_files_are_never_applied(self):
        self.patch("compatible_parts", return_value=[])
        applied, failed = configurations.apply(self.snapshot(("gtk", "Broken")), ["gtk"])
        self.assertEqual(applied, [])
        self.assertIn("incompatible", failed[0][1])
        self.apply_component.assert_not_called()

    def test_partial_failure_reports_applied_components_and_engine_error(self):
        def apply(comp, only):
            if "gtk" in only:
                raise desktop.ApplyError("CSS validation failed")
            return only

        self.apply_component.side_effect = apply
        applied, failed = configurations.apply(
            self.snapshot(("gtk", "Broken"), ("icons", "Papirus")), ["gtk", "icons"]
        )
        self.assertEqual(applied, ["icons"])
        self.assertEqual(failed, [("gtk", "CSS validation failed")])

    def test_gtk_builtin_and_cinnamon_default_do_not_require_external_theme_files(self):
        self.supported.add("desktop")
        self.locate.return_value = None
        applied, failed = configurations.apply(
            self.snapshot(("gtk", "Adwaita-dark"), ("desktop", "")), ["gtk", "desktop"]
        )
        self.assertEqual((applied, failed), (["gtk", "desktop"], []))
        self.set_value.assert_has_calls(
            [mock.call("gtk", "Adwaita-dark"), mock.call("desktop", "")]
        )

    def test_native_gnome_applies_only_explicit_gtk4_selection(self):
        applied, failed = configurations.apply(
            self.snapshot(("libadwaita", "Native-Dark")), ["libadwaita"]
        )
        self.assertEqual((applied, failed), (["libadwaita"], []))
        self.assertEqual(
            self.apply_component.call_args.args,
            ({"name": "Native-Dark", "path": str(self.root), "provides": ["gtk"]}, ["libadwaita"]),
        )

    def test_wallpaper_preserves_exact_uri_and_missing_images_are_disabled(self):
        wall = self.root / "wall paper.png"
        wall.write_bytes(b"image")
        self.locate.return_value = wall
        snapshot = self.snapshot(("wallpapers", wall.as_uri()))
        self.assertEqual(configurations.component_name(snapshot["components"][0]), "wall paper.png")
        self.assertEqual(configurations.apply(snapshot, ["wallpapers"]), (["wallpapers"], []))
        wall.unlink()
        self.assertIn("missing", configurations.review(snapshot)[0]["reason"])


class ConfigurationLookupTest(unittest.TestCase):
    def test_system_and_user_icon_roots_and_local_wallpaper_uris(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            icons = root / "system/icons/System-Icons"
            icons.mkdir(parents=True)
            with mock.patch.dict(
                "os.environ",
                {"XDG_DATA_HOME": str(root / "data"), "XDG_DATA_DIRS": str(root / "system")},
            ):
                self.assertEqual(configurations.locate("icons", "System-Icons"), icons)
            wallpaper = root / "wall paper.png"
            self.assertEqual(configurations.locate("wallpapers", wallpaper.as_uri()), wallpaper)
            self.assertIsNone(configurations.locate("wallpapers", "https://example.org/wall.png"))
            self.assertIsNone(configurations.locate("wallpapers", "file://other-host/wall.png"))
