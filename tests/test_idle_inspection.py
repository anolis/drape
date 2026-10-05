import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import compatibility, idle_inspection, pling


class IdleInspectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 0
        self.index = compatibility.Index(Path(self.temp.name) / "index.sqlite3")
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
            files=[pling.Download(1, "theme.zip", "https://test/theme", 1, "abc")],
        )
        self.card = mock.Mock(item=self.item, departing=False, _scan_alive=True, _checks={})
        self.flow = mock.Mock()
        self.flow.get_mapped.return_value = True
        self.flow.get_toplevel.return_value.is_active.return_value = True
        self.flow.cards.return_value = [self.card]
        self.flow.in_view.return_value = True
        self.card.get_parent.return_value = self.flow
        self.process = mock.Mock(stdout=io.BytesIO(b'{"status":"checked"}'))
        self.process.poll.return_value = None
        self.spawn = mock.Mock(return_value=self.process)
        self.scanner = idle_inspection.IdleInspector(
            clock=lambda: self.now, spawn=self.spawn, index=self.index
        )
        self.scanner.register(self.flow)

    def start(self):
        self.now = 2
        self.scanner.tick()
        self.spawn.assert_called_once()
        self.assertIsNotNone(self.scanner.active)

    def test_local_progress_only_reaches_complete_after_successful_exit(self):
        self.scanner.local_manifest = Path(self.temp.name) / "installed.json"
        self.scanner.tick()
        progress = Path(self.scanner.local_progress.name) / "progress.json"
        progress.write_text('{"done":1,"total":2,"title":"Second theme"}')
        self.scanner.tick()
        self.assertEqual(self.scanner.status["state"], "local")
        self.assertEqual(self.scanner.status["fraction"], 0.5)
        self.assertTrue(self.scanner.status["spinning"])
        self.assertIn("Second theme", self.scanner.status["text"])
        progress.write_text('{"done":2,"total":2,"title":""}')
        self.scanner.tick()
        self.assertLess(self.scanner.status["fraction"], 1)
        self.process.poll.return_value = 0
        self.process.returncode = 0
        self.scanner.tick()
        self.assertEqual(self.scanner.status["state"], "complete")
        self.assertEqual(self.scanner.status["fraction"], 1)
        self.assertFalse(self.scanner.status["spinning"])
        self.assertFalse(progress.parent.exists())

    def test_failed_local_scan_preserves_partial_progress(self):
        self.scanner.local_manifest = Path(self.temp.name) / "installed.json"
        self.scanner.tick()
        progress = Path(self.scanner.local_progress.name) / "progress.json"
        progress.write_text('{"done":1,"total":3,"title":""}')
        self.process.poll.return_value = 1
        self.process.returncode = 1
        self.scanner.tick()
        self.assertEqual(self.scanner.status["state"], "failed")
        self.assertIn("remaining checks pending", self.scanner.status["text"])
        self.assertAlmostEqual(self.scanner.status["fraction"], 1 / 3)

    def test_scanning_cancellation_and_rate_limit_have_visible_status(self):
        self.start()
        self.assertEqual(self.scanner.status["state"], "scanning")
        self.assertTrue(self.scanner.status["spinning"])
        self.card.set_scan_busy.assert_called_with(True)
        self.process.stdout = io.BytesIO(b'{"status":"rate_limited","retry_after":60}')
        self.process.poll.return_value = 0
        self.now = 3
        self.scanner.tick()
        self.assertEqual(self.scanner.status["state"], "rate_limited")
        self.assertFalse(self.scanner.status["spinning"])
        self.now = 13
        self.scanner.tick()
        self.assertIn("50s", self.scanner.status["text"])
        self.card.set_scan_busy.assert_called_with(False)

    def test_local_backfill_starts_without_idle_or_visible_cards(self):
        self.scanner.local_manifest = Path(self.temp.name) / "installed.json"
        self.scanner.set_enabled(False)
        self.flow.get_mapped.return_value = False
        self.scanner.tick()
        self.spawn.assert_called_once()
        self.assertIn("--installed-local", self.spawn.call_args.args[0])
        self.assertIsNone(self.scanner.active)
        self.scanner.tick()
        self.spawn.assert_called_once()
        self.scanner.close()
        self.process.kill.assert_called_once()

    def test_waits_for_idle_and_only_starts_one_process(self):
        self.now = 1
        self.scanner.tick()
        self.spawn.assert_not_called()
        self.scanner.activity(self.flow)
        self.now = 2
        self.scanner.tick()
        self.spawn.assert_not_called()
        self.now = 3
        self.scanner.tick()
        self.now = 10
        self.scanner.tick()
        self.spawn.assert_called_once()

    def test_offscreen_cancels_immediately_without_evidence(self):
        self.start()
        self.flow.in_view.return_value = False
        self.scanner.activity(self.flow)
        self.process.terminate.assert_called_once()
        self.assertIsNone(self.scanner.active)
        self.assertFalse(self.index.path.exists())
        self.now = 10
        self.scanner.tick()
        self.process.kill.assert_called_once()
        self.spawn.assert_called_once()

    def test_view_destroy_and_disable_cancel(self):
        self.start()
        self.scanner.unregister(self.flow)
        self.process.terminate.assert_called_once()
        self.assertNotIn(self.flow, self.scanner.flows)

    def test_disabled_and_background_views_never_start(self):
        self.scanner.set_enabled(False)
        self.now = 10
        self.scanner.tick()
        self.scanner.set_enabled(True)
        self.flow.get_toplevel.return_value.is_active.return_value = False
        self.scanner.tick()
        self.spawn.assert_not_called()

    def test_finished_evidence_prevents_repeat_after_relaunch(self):
        self.index.record(self.item, self.item.files[0], ({"gtk"}, True), "archive", {}, "unknown")
        for scanner in [
            self.scanner,
            idle_inspection.IdleInspector(
                clock=lambda: self.now, spawn=self.spawn, index=compatibility.Index(self.index.path)
            ),
        ]:
            scanner.register(self.flow)
            self.now += 10
            scanner.tick()
        self.spawn.assert_not_called()
        self.card.refresh_evidence.assert_called()

    def test_rate_limit_is_not_cached_or_retried_in_a_burst(self):
        self.start()
        self.process.stdout = io.BytesIO(b'{"status":"rate_limited","retry_after":60}')
        self.process.poll.return_value = 0
        self.now = 3
        self.scanner.tick()
        self.assertEqual(self.scanner.next_check, 63)
        self.now = 62
        self.scanner.tick()
        self.spawn.assert_called_once()
        self.assertFalse(self.index.path.exists())

    def test_worker_has_memory_and_time_limits(self):
        self.start()
        command = self.spawn.call_args.args[0]
        self.assertIn("384", command)
        self.assertIn("--request-interval", command)
        self.now = 100
        self.scanner.tick()
        self.process.terminate.assert_called_once()

    def test_layout_change_cancels_even_without_scroll_signal(self):
        self.start()
        self.flow.in_view.return_value = False
        self.scanner.tick()
        self.process.terminate.assert_called_once()
