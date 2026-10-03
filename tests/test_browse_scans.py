import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from drape.ui import browse, retries
from drape.ui.retries import RetryQueue
from drape import pling


class Widget(SimpleNamespace):
    """Test double hashed by identity, as GTK widgets are when used as retry queue keys."""

    __hash__ = object.__hash__


class BrowseScansTest(unittest.TestCase):
    def test_rate_limited_scan_waits_in_queue_and_cancels_on_navigation(self):
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
        card = Widget(item=item, _show_glyphs=mock.Mock())
        page = Widget(
            kind="gtk",
            generation=1,
            _scanned_checks={},
            _rate_limited=set(),
            flow=mock.Mock(),
            cards=lambda: [card],
            _filtered_status=mock.Mock(),
            _maybe_more=mock.Mock(),
        )
        page.flow.in_destruction.return_value = False
        submit = mock.Mock()
        queue = RetryQueue(submit)
        with (
            mock.patch.object(browse._peeks, "submit", submit),
            mock.patch.object(browse, "_retries", queue),
            mock.patch.object(
                browse.peek,
                "inspect_downloads",
                side_effect=browse.peek.RateLimited(90, checks={1: None}, host="files.test"),
            ),
            mock.patch.object(
                browse.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)
            ),
            mock.patch.object(retries.GLib, "timeout_add") as timer,
            mock.patch.object(
                retries.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)
            ),
            mock.patch.object(retries.time, "monotonic", return_value=1000),
        ):
            browse.BrowsePage._preflight(page, [item], mock.Mock(), mock.Mock(), 1)
            submit.call_args.args[0]()
            self.assertTrue(card._rate_limited)
            self.assertEqual(page._rate_limited, {"rate"})
            self.assertEqual(card._checks, {1: None})
            self.assertEqual(queue.pending("files.test"), [("scan", page, "rate")])
            self.assertEqual(timer.call_args.args[0], 90_000)
            with mock.patch.object(
                browse.peek, "inspect_downloads", return_value=(item, {1: ({"gtk", "gtk-3.0"}, True)})
            ) as inspect:
                release, host = timer.call_args.args[1:]
                release(host)
                submit.call_args.args[0]()
                inspect.assert_called_once_with(item, "gtk", None)  # links are not re-fetched up front
                self.assertFalse(card._rate_limited)
                self.assertEqual(page._rate_limited, set())

            # leaving the page drops its waiting scans
            queue.add("files.test", ("scan", page, "other"), mock.Mock(), 30)
            queue.add("files.test", ("card", card), mock.Mock(), 30)
            queue.cancel(lambda key: key[:2] == ("scan", page))
            self.assertEqual(queue.pending(), [("card", card)])

    def test_failed_check_is_unverified_not_pending(self):
        item = pling.Item(
            "1",
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
        card = SimpleNamespace(
            _scan_alive=True,
            departing=False,
            kind="gtk",
            item=item,
            _rate_limited=True,
            _show_glyphs=mock.Mock(),
        )
        with (
            mock.patch.object(
                browse.peek, "contents_refreshing", side_effect=browse.peek.InspectionFailed("x")
            ),
            mock.patch.object(
                browse.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)
            ),
        ):
            browse.Card._peek_work(card, None)
        card._show_glyphs.assert_called_once_with(set(), False, checked=True)
        self.assertFalse(card._rate_limited)

    def test_broken_alternative_does_not_hide_best_result(self):
        files = [pling.Download(1, "theme.zip", "a", 1, ""), pling.Download(2, "src.zip", "b", 1, "")]
        item = pling.Item("1", "Theme", "", "", "", "", 0, 0, "", files=files)
        card = SimpleNamespace(
            _scan_alive=True,
            departing=False,
            kind="gtk",
            item=item,
            _rate_limited=False,
            _show_glyphs=mock.Mock(),
        )
        with (
            mock.patch.object(
                browse.peek,
                "contents_refreshing",
                side_effect=[(item, ({"icons"}, True)), browse.peek.InspectionFailed("x")],
            ),
            mock.patch.object(
                browse.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)
            ),
        ):
            browse.Card._peek_work(card, None)
        card._show_glyphs.assert_called_once_with({"icons"}, True, checked=True)

    def test_deferred_card_keeps_cached_component_glyphs_and_queues_its_check(self):
        item = pling.Item(
            "1",
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
        card = Widget(
            _scan_alive=True,
            departing=False,
            item=item,
            _show_glyphs=mock.Mock(),
            _peek_work=mock.Mock(),
        )
        queue = mock.Mock()
        with (
            mock.patch.object(browse.peek, "cached", return_value=({"gtk", "gtk-4.0"}, True)),
            mock.patch.object(browse, "_retries", queue),
        ):
            browse.Card._defer_peek(card, browse.peek.RateLimited(90, host="files.test"), None)
            card._show_glyphs.assert_called_once_with({"gtk", "gtk-4.0"}, True)
            host, key, job, delay = queue.add.call_args.args
            self.assertEqual((host, key, delay), ("files.test", ("card", card), 90))
            job()
            card._peek_work.assert_called_once_with(None)

    def test_stale_scan_never_changes_current_results(self):
        page = SimpleNamespace(
            kind="gtk",
            generation=1,
            _scanned_checks={},
            _rate_limited=set(),
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
            _rate_limited=set(),
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
            mock.patch.object(
                browse.peek, "inspect_downloads", side_effect=lambda item, *_: (item, checks)
            ) as scan,
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
