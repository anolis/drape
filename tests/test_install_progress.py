import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from drape import installer
from drape.ui import install_progress, theme_actions


class InstallProgressTest(unittest.TestCase):
    def setUp(self):
        timer = mock.patch.object(install_progress.GLib, "timeout_add", return_value=42)
        timer.start()
        self.addCleanup(timer.stop)
        remove = mock.patch.object(install_progress.GLib, "source_remove")
        self.remove = remove.start()
        self.addCleanup(remove.stop)
        self.feedback = install_progress.InstallProgress()
        self.addCleanup(self.feedback.close)

    def test_download_completion_is_only_part_of_installation(self):
        bar = mock.Mock()
        self.feedback.attach(bar)
        self.feedback.download(100, 100)
        self.feedback._tick()
        bar.set_text.assert_called_with("40% · Downloading…")
        bar.set_fraction.assert_called_with(0.40)
        bar.reset_mock()
        self.feedback.status("Extracting files…", 0.42)
        self.feedback._tick()
        self.feedback._tick()
        bar.set_text.assert_called_with("42% · Extracting files…")
        bar.set_fraction.assert_called_with(0.42)
        bar.pulse.assert_not_called()

    def test_recreated_card_shows_current_stage_immediately(self):
        self.feedback.status("Updating icon cache for Papirus…", 0.85)
        bar = mock.Mock()
        self.feedback.attach(bar)
        bar.set_text.assert_called_with("85% · Updating icon cache for Papirus…")
        bar.set_fraction.assert_called_once_with(0.85)
        callback = bar.connect.call_args.args[1]
        callback(bar)
        bar.reset_mock()
        self.feedback._tick()
        bar.set_text.assert_not_called()

    def test_unknown_download_size_does_not_invent_progress(self):
        bar = mock.Mock()
        self.feedback.attach(bar)
        bar.reset_mock()
        self.feedback.download(1000, 0)
        self.feedback._tick()
        bar.set_text.assert_called_with("0% · Downloading…")
        bar.set_fraction.assert_called_once_with(0.0)

    def test_progress_is_monotonic_and_reserves_completion_for_success(self):
        bar = mock.Mock()
        self.feedback.attach(bar)
        for text, fraction in (
            ("Extracting…", 0.42),
            ("Inspecting…", 0.60),
            ("Downloading another variant…", 0.0),
            ("Installing…", 0.68),
            ("Updating cache…", 0.85),
            ("Cleaning up…", 0.90),
            ("Saving…", 0.98),
        ):
            self.feedback.status(text, fraction)
            self.feedback._tick()
        fractions = [call.args[0] for call in bar.set_fraction.call_args_list]
        self.assertEqual(fractions, sorted(fractions))
        self.assertLess(max(fractions), 1.0)
        self.feedback.complete()
        bar.set_fraction.assert_called_with(1.0)
        bar.set_text.assert_called_with("100% · Installed")

    def test_close_removes_timer_once(self):
        self.feedback.close()
        self.feedback.close()
        self.remove.assert_called_once_with(42)

    def test_window_shutdown_stops_timer(self):
        closing = threading.Event()
        feedback = install_progress.InstallProgress(closing)
        closing.set()
        self.assertFalse(feedback._tick())
        self.assertIsNone(feedback._source)

    def test_success_failure_and_conflict_stop_progress(self):
        for outcome in (
            {"components": []},
            installer.InstallError("failed"),
            installer.ConflictError({}, "Pack"),
        ):
            with self.subTest(outcome=outcome):
                window = SimpleNamespace(
                    busy={},
                    _closing=threading.Event(),
                    refresh_item=mock.Mock(),
                    installed=SimpleNamespace(updates={}),
                    notify=mock.Mock(),
                    reapply_replaced=mock.Mock(return_value=False),
                    resolve_conflict=mock.Mock(),
                )
                item = SimpleNamespace(id="1", name="Pack")

                def run(work, done, error):
                    try:
                        result = work()
                    except Exception as exc:
                        error(exc)
                    else:
                        done(result)

                feedback = mock.Mock()
                with (
                    mock.patch.object(theme_actions, "InstallProgress", return_value=feedback),
                    mock.patch.object(theme_actions, "run_async", side_effect=run),
                    mock.patch.object(theme_actions.pling, "get"),
                    mock.patch.object(theme_actions, "error_dialog"),
                    mock.patch.object(installer, "install_item") as install,
                ):
                    if isinstance(outcome, Exception):
                        install.side_effect = outcome
                    else:
                        install.return_value = outcome
                    theme_actions.ThemeActions.install(window, item)
                feedback.close.assert_called_once()
                if isinstance(outcome, Exception):
                    feedback.complete.assert_not_called()
                else:
                    feedback.complete.assert_called_once()
                self.assertNotIn("1", window.busy)
