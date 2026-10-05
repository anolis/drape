import copy
from contextlib import closing
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from drape import compatibility, desktop, peek, pling


class CompatibilityIndexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.index = compatibility.Index(Path(self.temp.name) / "index.sqlite3")
        self.file = pling.Download(1, "theme.zip", "https://example.test/theme.zip", 1, "abc")
        self.item = pling.Item(
            "1", "Theme", "author", "", "", "2026-01-01", 0, 0, "", files=[self.file]
        )
        self.result = ({"gtk", "gtk-3.0"}, True)
        self.context = {"desktop": "cinnamon", "cinnamon": [6, 4]}

    def record(self, result=None, context=None, **kwargs):
        self.index.record(
            self.item,
            self.file,
            result or self.result,
            "gtk",
            context or self.context,
            "compatible",
            **kwargs,
        )

    def test_installed_subset_is_partial_revision_specific_and_local_only(self):
        entry = {"file": self.file.name, "changed": self.item.changed, "download_md5": "abc"}
        self.index.record_installed(self.item.id, entry, {"gtk", "gtk-3.0"})
        expected = ({"gtk", "gtk-3.0"}, False)
        self.assertEqual(self.index.inspection(self.item, self.file), expected)
        self.assertEqual(self.index.read_many([self.item]), {self.item.id: {1: expected}})
        self.assertEqual(self.index.export()["observations"], [])
        changed = replace(self.item, files=[replace(self.file, md5="different")])
        self.assertEqual(self.index.read_many([changed]), {self.item.id: {}})
        self.assertIsNone(self.index.inspection(changed, changed.files[0]))
        self.record()
        self.assertEqual(self.index.inspection(self.item, self.file), self.result)

    def test_reuses_exact_revision_and_invalidates_changed_checksum(self):
        self.record()
        self.assertEqual(self.index.inspection(self.item, self.file), self.result)
        self.assertIsNone(self.index.inspection(self.item, replace(self.file, md5="def")))

    def test_read_only_snapshot_does_not_create_missing_database(self):
        self.assertEqual(self.index.read_many([self.item]), {self.item.id: {}})
        self.assertFalse(self.index.path.exists())

    def test_complete_snapshot_persists_until_revision_changes(self):
        self.record()
        self.assertEqual(self.index.read_many([self.item]), {self.item.id: {1: self.result}})
        changed = replace(self.item, files=[replace(self.file, md5="def")])
        self.assertEqual(self.index.read_many([changed]), {self.item.id: {}})
        with mock.patch.object(compatibility.time, "time", return_value=10**12):
            self.assertEqual(self.index.read_many([self.item]), {self.item.id: {1: self.result}})
            reopened = compatibility.Index(self.index.path)
            self.assertEqual(reopened.inspection(self.item, self.file), self.result)

    def test_public_snapshot_fetch_validates_before_importing(self):
        import json

        self.record()
        payload = json.dumps(self.index.export()).encode()
        response = mock.Mock()
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        response.iter_content.return_value = iter([payload])
        other = compatibility.Index(Path(self.temp.name) / "downloaded.sqlite3")
        with mock.patch("drape.http.get", return_value=response):
            self.assertEqual(
                compatibility.fetch_snapshot("https://example.test/index.json", other), 1
            )
        self.assertEqual(other.inspection(self.item, self.file), self.result)

    def test_invalid_public_snapshot_does_not_create_database(self):
        response = mock.Mock()
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        response.iter_content.return_value = iter([b'{"format":"bad","observations":[]}'])
        with mock.patch("drape.http.get", return_value=response):
            with self.assertRaises(ValueError):
                compatibility.fetch_snapshot("https://example.test/index.json", self.index)
        self.assertFalse(self.index.path.exists())

    def test_fallback_revision_uses_modification_date(self):
        file = replace(self.file, md5="")
        self.index.record(self.item, file, self.result, "gtk", self.context, "compatible")
        self.assertIsNone(self.index.inspection(replace(self.item, changed="2026-02-01"), file))

    def test_partial_evidence_expires_so_unknown_can_be_retried(self):
        with mock.patch.object(compatibility.time, "time", return_value=1000):
            self.record(result=(set(), False))
            self.assertEqual(self.index.inspection(self.item, self.file), (set(), False))
        with mock.patch.object(compatibility.time, "time", return_value=87401):
            self.assertIsNone(self.index.inspection(self.item, self.file))

    def test_legacy_empty_rows_are_ignored_without_discarding_positive_evidence(self):
        self.record(result=(set(), False))
        with closing(self.index.connect()) as db, db:
            db.execute("UPDATE inspections SET rules=1")
        self.assertIsNone(self.index.inspection(self.item, self.file))
        self.record()
        with closing(self.index.connect()) as db, db:
            db.execute("UPDATE inspections SET rules=1")
        self.assertEqual(self.index.inspection(self.item, self.file), self.result)

    def test_transport_failure_is_not_recorded_as_inspection(self):
        with (
            mock.patch.object(compatibility, "Index", return_value=self.index),
            mock.patch.object(peek, "contents", side_effect=peek.InspectionFailed("failed")),
        ):
            self.assertEqual(peek.inspect_downloads(self.item, "gtk")[1], {1: None})
        self.assertEqual(self.index.export()["observations"], [])
        self.assertIsNone(self.index.inspection(self.item, self.file))

    def test_import_parser_update_only_rechecks_old_unknown_cinnamon_evidence(self):
        for parts, expected in (
            ({"desktop", "cinnamon-unknown"}, None),
            ({"desktop", "cinnamon-modern"}, ({"desktop", "cinnamon-modern"}, True)),
            ({"gtk", "gtk-3.0"}, ({"gtk", "gtk-3.0"}, True)),
            (set(), (set(), True)),
        ):
            with self.subTest(parts=parts):
                self.record(result=(parts, True))
                with closing(self.index.connect()) as db, db:
                    db.execute("UPDATE inspections SET rules=3")
                self.assertEqual(self.index.inspection(self.item, self.file), expected)
                self.assertEqual(
                    self.index.read_many([self.item]),
                    {self.item.id: {1: expected} if expected is not None else {}},
                )

    def test_expired_link_is_refreshed_and_fresh_item_returned(self):
        fresh = replace(self.item, files=[replace(self.file, url="https://example.test/fresh")])
        with (
            mock.patch.object(compatibility, "Index", return_value=self.index),
            mock.patch.object(
                peek, "contents", side_effect=[peek.LinkExpired("403"), self.result]
            ) as contents,
            mock.patch.object(peek, "fresh_item", return_value=fresh),
        ):
            item, checks = peek.inspect_downloads(self.item, "gtk")
        self.assertIs(item, fresh)
        self.assertEqual(checks, {1: self.result})
        self.assertEqual(contents.call_args_list[-1].args[1], "https://example.test/fresh")
        self.assertEqual(self.index.inspection(fresh, fresh.files[0]), self.result)

    def test_diff_cursor_only_advances_for_changes(self):
        self.record()
        first = self.index.export()
        self.record()
        self.assertEqual(self.index.export(first["cursor"])["observations"], [])
        self.record(context={"desktop": "cinnamon", "cinnamon": [5, 2]})
        delta = self.index.export(first["cursor"])
        self.assertEqual(len(delta["observations"]), 1)
        self.assertGreater(delta["cursor"], first["cursor"])

    def test_installed_evidence_does_not_claim_archive_contents(self):
        self.record(basis="installed-components")
        self.assertIsNone(self.index.inspection(self.item, self.file))
        self.assertEqual(self.index.export()["observations"][0]["basis"], "installed-components")

    def test_imported_observations_are_not_exported_as_local(self):
        self.record()
        other = compatibility.Index(Path(self.temp.name) / "imported.sqlite3")
        self.assertEqual(other.import_records(self.index.export()), 1)
        self.assertEqual(other.inspection(self.item, self.file), self.result)
        self.assertEqual(other.export()["observations"], [])

    def test_local_evidence_takes_precedence_over_community(self):
        self.record()
        document = copy.deepcopy(self.index.export())
        document["observations"][0]["parts"] = ["gtk", "gtk-4.0"]
        self.index.import_records(document)
        self.assertEqual(self.index.inspection(self.item, self.file), self.result)

    def test_invalid_import_leaves_database_unchanged(self):
        self.record()
        document = copy.deepcopy(self.index.export())
        document["observations"].append({"parts": "invalid"})
        other = compatibility.Index(Path(self.temp.name) / "invalid.sqlite3")
        with self.assertRaises(ValueError):
            other.import_records(document)
        self.assertIsNone(other.inspection(self.item, self.file))

    def test_indexed_inspection_avoids_network_and_rechecks_current_rules(self):
        self.record()
        with (
            mock.patch.object(compatibility, "Index", return_value=self.index),
            mock.patch.object(compatibility, "context", return_value=self.context),
            mock.patch.object(peek, "contents") as contents,
            mock.patch.object(desktop, "supported", return_value=True),
        ):
            self.assertEqual(peek.inspect_downloads(self.item, "gtk")[1], {1: self.result})
            contents.assert_not_called()

    def test_rate_limit_never_becomes_compatibility_evidence(self):
        with (
            mock.patch.object(compatibility, "Index", return_value=self.index),
            mock.patch.object(compatibility, "context", return_value=self.context),
            mock.patch.object(peek, "contents", side_effect=peek.RateLimited(60)),
        ):
            with self.assertRaises(peek.RateLimited) as error:
                peek.inspect_downloads(self.item, "gtk")
            self.assertEqual(error.exception.checks, {1: None})
            self.assertIsNone(self.index.inspection(self.item, self.file))
            self.assertEqual(self.index.export()["observations"], [])

    def test_compatible_alternative_still_wins_after_rate_limit(self):
        item = replace(self.item, files=[self.file, replace(self.file, index=2, name="other.zip")])
        with (
            mock.patch.object(compatibility, "Index", return_value=self.index),
            mock.patch.object(compatibility, "context", return_value=self.context),
            mock.patch.object(peek, "contents", side_effect=[peek.RateLimited(60), self.result]),
            mock.patch.object(desktop, "supported", return_value=True),
        ):
            self.assertEqual(peek.inspect_downloads(item, "gtk")[1], {1: None, 2: self.result})

    def test_installed_bundle_proof_requires_matching_download(self):
        entry = {"file": self.file.name, "changed": self.item.changed, "components": []}
        parts = {"gtk", "gtk-3.0", "icons"}
        with (
            mock.patch.object(compatibility, "Index", return_value=self.index),
            mock.patch.object(compatibility, "context", return_value=self.context),
            mock.patch.object(peek, "installed_parts", return_value=parts),
            mock.patch.object(peek, "contents", return_value=(set(), False)) as contents,
            mock.patch.object(desktop, "supported", return_value=True),
        ):
            self.assertEqual(
                peek.inspect_downloads(self.item, "packs", {"1": entry})[1], {1: (parts, False)}
            )
            contents.assert_not_called()
            peek.inspect_downloads(replace(self.item, changed="2026-02-01"), "packs", {"1": entry})
            contents.assert_called_once()
