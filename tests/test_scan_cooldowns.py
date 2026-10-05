import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from drape import compatibility, compatibility_worker, http, idle_inspection, pling


class ScanCooldownTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "compatibility.sqlite3"
        self.index = compatibility.Index(self.path)
        self.now = 100
        clock = mock.patch.object(compatibility.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        blocks = mock.patch.dict(http._blocked_until, {}, clear=True)
        blocks.start()
        self.addCleanup(blocks.stop)
        self.item = pling.Item(
            "1",
            "Theme",
            "",
            "",
            "",
            "today",
            0,
            0,
            "",
            files=[
                pling.Download(1, "theme.zip", "https://files.test/theme?token=secret", 1, "abc")
            ],
        )

    def test_empty_or_old_index_has_no_cooldown_and_read_creates_no_files(self):
        self.assertEqual(self.index.scan_cooldown(), (None, 0))
        self.assertFalse(self.path.exists())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE old_schema (value INTEGER)")
        self.assertEqual(self.index.scan_cooldown(), (None, 0))

    def test_cooldown_persists_without_exporting_compatibility_evidence(self):
        self.assertTrue(self.index.defer_scans("files.test", 900))
        self.now = 200
        restored = compatibility.Index(self.path)
        self.assertEqual(restored.scan_cooldown(), ("files.test", 800))
        self.assertEqual(restored.export()["observations"], [])
        self.now = 1001
        self.assertEqual(restored.scan_cooldown(), (None, 0))

    def test_shorter_cooldown_never_overwrites_a_longer_one(self):
        self.index.defer_scans("files.test", 900)
        self.now = 200
        self.index.defer_scans("files.test", 30)
        self.assertEqual(self.index.scan_cooldown(), ("files.test", 800))

    def test_unknown_host_still_defers_scans_and_absurd_delay_is_capped(self):
        self.index.defer_scans(None, 999999999999)
        self.assertEqual(self.index.scan_cooldown(), (None, http.MAX_RETRY_AFTER))

    def test_new_scanner_restores_cooldown_and_blocks_same_host_installs(self):
        self.index.defer_scans("files.test", 900)
        self.now = 200
        scanner = idle_inspection.IdleInspector(clock=lambda: 10, index=self.index)
        self.assertEqual(scanner.cooldown, 810)
        self.assertEqual(scanner.next_check, 810)
        with mock.patch.object(http.requests, "get") as get:
            with self.assertRaises(http.RateLimited):
                http.get("https://files.test/another-download")
            get.assert_not_called()

    def test_worker_cooldown_survives_loss_of_parent_callback(self):
        with mock.patch.object(
            compatibility_worker.peek,
            "contents",
            side_effect=http.RateLimited(900, host="files.test"),
        ):
            result = compatibility_worker.inspect_one({"item": asdict(self.item)}, self.index)
        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["host"], "files.test")
        self.assertEqual(compatibility.Index(self.path).scan_cooldown(), ("files.test", 900))
        with mock.patch.object(compatibility_worker.peek, "contents") as inspect:
            result = compatibility_worker.inspect_one({"item": asdict(self.item)}, self.index)
            self.assertEqual(result["status"], "rate_limited")
            inspect.assert_not_called()

    def test_cached_evidence_is_returned_even_while_network_is_deferred(self):
        self.index.record(self.item, self.item.files[0], ({"gtk"}, True), "archive", {}, "unknown")
        self.index.defer_scans("files.test", 900)
        with mock.patch.object(compatibility_worker.peek, "contents") as inspect:
            self.assertEqual(
                compatibility_worker.inspect_one({"item": asdict(self.item)}, self.index),
                {"status": "cached"},
            )
            inspect.assert_not_called()

    def test_explicit_batch_worker_does_not_ignore_saved_cooldown(self):
        self.index.defer_scans("files.test", 900)
        with mock.patch.object(compatibility_worker.peek, "contents") as inspect:
            self.assertEqual(compatibility_worker.scan_item(self.item, self.index), 1)
            inspect.assert_not_called()
