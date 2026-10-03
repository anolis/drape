import unittest
from types import SimpleNamespace
from unittest import mock

from drape.ui import browse
from drape import pling


class BrowseScansTest(unittest.TestCase):
    def test_rate_limited_scan_retries_later_and_cancels_on_navigation(self):
        item = pling.Item(
            "rate",
            "Theme",
            "",
            "",
            "",
            "",
            0,
            0,
            "",
            files=[pling.Download(1, "theme.zip", "url", 1, "")],
        )
        card = SimpleNamespace(item=item, _show_glyphs=mock.Mock())
        page = SimpleNamespace(
            kind="gtk",
            generation=1,
            _scanned_checks={},
            flow=mock.Mock(),
            cards=lambda: [card],
            _filtered_status=mock.Mock(),
            _maybe_more=mock.Mock(),
        )
        page.flow.in_destruction.return_value = False
        with (
            mock.patch.object(browse._peeks, "submit") as submit,
            mock.patch.object(
                browse.peek,
                "inspect_downloads",
                side_effect=browse.peek.RateLimited(90, checks={1: None}),
            ),
            mock.patch.object(
                browse.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)
            ),
            mock.patch.object(browse.GLib, "timeout_add_seconds") as timer,
        ):
            browse.BrowsePage._preflight(page, [item], mock.Mock(), mock.Mock(), 1)
            submit.call_args.args[0]()
            self.assertTrue(card._rate_limited)
            self.assertEqual(card._checks, {1: None})
            self.assertEqual(timer.call_args.args[0], 90)
            page.generation = 2
            timer.call_args.args[1]()
            self.assertEqual(submit.call_count, 1)

    def test_stale_scan_never_changes_current_results(self):
        page = SimpleNamespace(
            kind="gtk",
            generation=1,
            _scanned_checks={},
            flow=mock.Mock(),
            cards=lambda: [],
            _filtered_status=mock.Mock(),
            _maybe_more=mock.Mock(),
        )
        item = SimpleNamespace(id="1")
        ready, failed = mock.Mock(), mock.Mock()
        with (
            mock.patch.object(browse._peeks, "submit") as submit,
            mock.patch.object(browse.peek, "inspect_downloads") as inspect,
        ):
            browse.BrowsePage._preflight(page, [item], ready, failed, 1)
            ready.assert_called_once()
            page.generation = 2
            page._scanned_checks.clear()
            submit.call_args.args[0]()
            inspect.assert_not_called()
            self.assertEqual(page._scanned_checks, {})

    def test_cards_publish_before_independent_scans_finish(self):
        card = SimpleNamespace(item=SimpleNamespace(id="0"), _show_glyphs=mock.Mock())
        page = SimpleNamespace(
            kind="gtk",
            generation=1,
            _scanned_checks={},
            flow=mock.Mock(),
            cards=lambda: [card],
            _filtered_status=mock.Mock(),
            _maybe_more=mock.Mock(),
        )
        page.flow.in_destruction.return_value = False
        items = [
            pling.Item(
                str(i),
                "Theme",
                "",
                "",
                "",
                "",
                0,
                0,
                "",
                files=[pling.Download(1, "theme.zip", "url", 1, "")],
            )
            for i in range(3)
        ]
        ready, failed = mock.Mock(), mock.Mock()
        checks = {1: ({"gtk", "gtk-3.0"}, True)}
        with (
            mock.patch.object(browse._peeks, "submit") as submit,
            mock.patch.object(browse.peek, "inspect_downloads", return_value=checks) as scan,
            mock.patch.object(
                browse.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)
            ),
        ):
            browse.BrowsePage._preflight(page, items, ready, failed, 1)
            ready.assert_called_once()
            scan.assert_not_called()
            self.assertEqual(submit.call_count, 3)
            submit.call_args_list[0].args[0]()
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(card._checks, checks)
            card._show_glyphs.assert_called_once_with({"gtk", "gtk-3.0"}, True, checked=True)
            self.assertEqual(page._scanned_checks["1"], {})
