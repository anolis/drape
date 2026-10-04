from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from drape import compatibility, compatibility_worker as worker, peek, pling


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.index = compatibility.Index(Path(self.temp.name) / "index.sqlite3")
        self.item = pling.Item(
            "1",
            "Theme",
            "",
            "",
            "",
            "2026-10-03",
            0,
            0,
            "",
            files=[
                pling.Download(1, "theme.zip", "https://files.test/theme?token=private", 1, "abc")
            ],
        )

    def test_single_file_ipc_reuses_persistent_evidence(self):
        payload = {"item": asdict(self.item)}
        with mock.patch.object(peek, "contents", return_value=({"gtk"}, True)) as inspect:
            self.assertEqual(worker.inspect_one(payload, self.index)["status"], "checked")
            self.assertEqual(
                worker.inspect_one({"item": asdict(self.item)}, self.index)["status"], "cached"
            )
            inspect.assert_called_once()

    def test_single_file_protocol_runs_in_bounded_subprocess_and_reuses_cache(self):
        self.index.record(self.item, self.item.files[0], ({"gtk"}, True), "archive", {}, "unknown")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "drape.compatibility_worker",
                "--inspect-json",
                "--memory-mib",
                "384",
                "--database",
                str(self.index.path),
            ],
            input=json.dumps({"item": asdict(self.item)}),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "cached"})

    def test_single_file_ipc_429_is_pending_and_not_saved(self):
        with mock.patch.object(peek, "contents", side_effect=peek.RateLimited(90)):
            result = worker.inspect_one({"item": asdict(self.item)}, self.index)
        self.assertEqual(result, {"status": "rate_limited", "retry_after": 90})
        self.assertEqual(self.index.export()["observations"], [])

    def test_worker_help_runs_without_gtk_or_gi(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, runpy; sys.modules['gi']=None; sys.argv=['worker','--help']; runpy.run_module('drape.compatibility_worker',run_name='__main__')",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_inspects_stylesheets_without_desktop_imports(self):
        code = """import sys, tempfile
sys.modules['gi'] = None
from pathlib import Path
from unittest import mock
from drape import peek
with tempfile.TemporaryDirectory() as tmp:
    peek.CACHE = Path(tmp) / 'peek.json'
    names = ['T/cinnamon/cinnamon.css']
    names += peek._cinnamon_markers(names, {names[0]: '.dialog {} .modal-dialog {}'}, True)
    with mock.patch.object(peek, 'list_archive', return_value=(names, True)):
        parts, complete = peek.contents('1', 'url', 'theme.zip', use_cache=False, inspect_css=True)
        assert complete and 'cinnamon-modern' in parts
assert 'drape.desktop' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_records_portable_raw_evidence_and_reuses_it(self):
        with mock.patch.object(
            peek, "contents", return_value=({"gtk", "gtk-3.0"}, True)
        ) as inspect:
            self.assertEqual(worker.scan_item(self.item, self.index), 0)
            inspect.assert_called_once_with(
                "1",
                self.item.files[0].url,
                "theme.zip",
                use_cache=False,
                inspect_css=True,
                write_cache=False,
            )
            self.assertEqual(worker.scan_item(self.item, self.index), 0)
            self.assertEqual(inspect.call_count, 1)
        output = self.index.export()
        self.assertEqual(output["observations"][0]["status"], "unknown")
        self.assertNotIn("private", json.dumps(output))

    def test_failed_or_limited_checks_do_not_publish_evidence(self):
        for error in (peek.InspectionFailed("failed"), peek.RateLimited(60)):
            with mock.patch.object(peek, "contents", side_effect=error):
                self.assertEqual(worker.scan_item(self.item, self.index), 1)
            self.assertEqual(self.index.export()["observations"], [])

    def test_streamed_prefix_stops_at_budget(self):
        response = mock.Mock()
        response.iter_content.return_value = iter([b"a" * 16, b"b" * 16, b"c" * 16])
        self.assertEqual(peek._read_prefix(response, 20), b"a" * 16 + b"b" * 4)
        self.assertEqual(next(response.iter_content.return_value), b"c" * 16)

    def test_zip_range_ignored_does_not_read_body(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(peek.http, "get", return_value=response):
            with self.assertRaises(OSError):
                peek._RangeFile("https://files.test/theme", 1000)._block(0)
            response.iter_content.assert_not_called()
            response.close.assert_called_once()

    def test_zip_truncated_range_does_not_loop(self):
        response = mock.Mock(status_code=206)
        response.iter_content.return_value = iter([b"abc"])
        with mock.patch.object(peek.http, "get", return_value=response):
            with self.assertRaises(OSError):
                peek._RangeFile("https://files.test/theme", 1000)._block(0)
            response.close.assert_called_once()
