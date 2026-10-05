import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import compatibility, compatibility_worker, local_inspection, pling
from drape.records import ManifestStore


class LocalInspectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.theme = self.root / "Theme"
        (self.theme / "gtk-4.0").mkdir(parents=True)
        (self.theme / "gtk-4.0" / "gtk.css").write_text("button {}")
        (self.theme / "cinnamon").mkdir()
        (self.theme / "cinnamon" / "cinnamon.css").write_text('@import "dialogs.css";')
        (self.theme / "cinnamon" / "dialogs.css").write_text(".dialog {} .modal-dialog {}")

    def test_inspects_local_styles_and_engines_without_http(self):
        with mock.patch("drape.http.get", side_effect=AssertionError("must stay offline")):
            parts, complete = local_inspection.inspect_paths([self.theme])
        self.assertTrue(complete)
        self.assertTrue({"gtk-4.0", "desktop", "cinnamon-modern", "cinnamon-pre54"} <= parts)

    def test_missing_paths_and_unresolved_css_do_not_prove_incompatibility(self):
        (self.theme / "cinnamon" / "dialogs.css").unlink()
        parts, complete = local_inspection.inspect_paths([self.theme, self.root / "missing"])
        self.assertFalse(complete)
        self.assertIn("cinnamon-unknown", parts)
        self.assertNotIn("cinnamon-legacy", parts)

    def test_installed_backfill_is_offline_and_matches_catalog_checksum(self):
        entry = {
            "title": "Theme",
            "file": "theme.zip",
            "changed": "today",
            "paths": [str(self.theme)],
            "components": [{"path": str(self.theme), "provides": ["gtk", "desktop"]}],
        }
        manifest = self.root / "installed.json"
        ManifestStore(manifest).save({"1": entry})
        index = compatibility.Index(self.root / "compatibility.sqlite3")
        with mock.patch("drape.http.get", side_effect=AssertionError("must stay offline")):
            progress = mock.Mock()
            self.assertEqual(compatibility_worker.scan_installed(manifest, index, progress), 1)
            self.assertEqual(
                progress.call_args_list,
                [mock.call(0, 1, ""), mock.call(0, 1, "Inspecting — Theme"), mock.call(1, 1, "")],
            )
        file = pling.Download(
            1, "theme.zip", "https://files.test/theme", 1, "old-install-has-no-md5"
        )
        item = pling.Item("1", "Theme", "", "", "", "today", 0, 0, "", files=[file])
        result = index.inspection(item, file)
        self.assertIn("gtk-4.0", result[0])
        self.assertFalse(result[1])
        self.assertEqual(index.read_many([item])["1"][1], result)

    def test_unchanged_installation_reuses_cache_without_inspection(self):
        entry = {
            "title": "Theme",
            "file": "theme.zip",
            "changed": "today",
            "paths": [str(self.theme)],
            "components": [{"path": str(self.theme)}],
        }
        manifest = self.root / "installed.json"
        ManifestStore(manifest).save({"1": entry})
        index = compatibility.Index(self.root / "index.sqlite3")
        stats = {}
        compatibility_worker.scan_installed(manifest, index, stats=stats)
        self.assertEqual(stats, {"reused": 0, "scanned": 1})
        with mock.patch.object(
            local_inspection, "inspect_paths", side_effect=AssertionError("should reuse cache")
        ):
            compatibility_worker.scan_installed(manifest, index, stats=stats)
        self.assertEqual(stats, {"reused": 1, "scanned": 0})
        (self.theme / "cinnamon" / "dialogs.css").write_text(".modal-dialog { color: red; }")
        compatibility_worker.scan_installed(manifest, index, stats=stats)
        self.assertEqual(stats, {"reused": 0, "scanned": 1})
        with mock.patch.object(compatibility, "RULES_VERSION", compatibility.RULES_VERSION + 1):
            compatibility_worker.scan_installed(manifest, index, stats=stats)
            self.assertEqual(stats, {"reused": 0, "scanned": 1})

    def test_fingerprint_tracks_replaced_components_and_download_metadata(self):
        entry = {"components": [{"path": str(self.theme)}], "changed": "old"}
        before = local_inspection.fingerprint(entry)
        entry["changed"] = "new"
        self.assertNotEqual(before, local_inspection.fingerprint(entry))
        before = local_inspection.fingerprint(entry)
        (self.theme / "gtk-3.0").mkdir()
        self.assertNotEqual(before, local_inspection.fingerprint(entry))

    def test_path_budget_keeps_truncated_evidence_partial(self):
        with mock.patch.object(local_inspection, "MAX_PATHS", 1):
            _, complete = local_inspection.inspect_paths([self.theme])
        self.assertFalse(complete)
