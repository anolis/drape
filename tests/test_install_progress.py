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

    def test_download_completion_changes_to_pulsing_installation_stage(self):
        bar = mock.Mock()
        self.feedback.attach(bar)
        self.feedback.download(100, 100)
        self.feedback._tick()
        bar.set_text.assert_called_with("Downloading 100%")
        bar.set_fraction.assert_called_with(1.0)
        bar.reset_mock()
        self.feedback.status("Extracting files…")
        self.feedback._tick()
        self.feedback._tick()
        bar.set_text.assert_called_with("Extracting files…")
        self.assertEqual(bar.pulse.call_count, 2)
        bar.set_fraction.assert_not_called()

    def test_recreated_card_shows_current_stage_immediately(self):
        self.feedback.status("Updating icon cache for Papirus…")
        bar = mock.Mock()
        self.feedback.attach(bar)
        bar.set_text.assert_called_with("Updating icon cache for Papirus…")
        bar.pulse.assert_called_once()
        callback = bar.connect.call_args.args[1]
        callback(bar)
        bar.reset_mock()
        self.feedback._tick()
        bar.set_text.assert_not_called()

    def test_unknown_download_size_keeps_animation_running(self):
        bar = mock.Mock()
        self.feedback.attach(bar)
        bar.reset_mock()
        self.feedback.download(1000, 0)
        self.feedback._tick()
        bar.set_text.assert_called_with("Downloading…")
        bar.pulse.assert_called_once()
        bar.set_fraction.assert_not_called()

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
        for outcome in ({"components": []}, installer.InstallError("failed"), installer.ConflictError({}, "Pack")):
            with self.subTest(outcome=outcome):
                window = SimpleNamespace(busy={}, _closing=threading.Event(), refresh_item=mock.Mock(),
                                         installed=SimpleNamespace(updates={}), notify=mock.Mock(),
                                         reapply_replaced=mock.Mock(return_value=False), resolve_conflict=mock.Mock())
                item = SimpleNamespace(id="1", name="Pack")

                def run(work, done, error):
                    try:
                        result = work()
                    except Exception as exc:
                        error(exc)
                    else:
                        done(result)

                feedback = mock.Mock()
                with mock.patch.object(theme_actions, "InstallProgress", return_value=feedback), \
                        mock.patch.object(theme_actions, "run_async", side_effect=run), \
                        mock.patch.object(theme_actions.pling, "get"), \
                        mock.patch.object(theme_actions, "error_dialog"), \
                        mock.patch.object(installer, "install_item") as install:
                    if isinstance(outcome, Exception):
                        install.side_effect = outcome
                    else:
                        install.return_value = outcome
                    theme_actions.ThemeActions.install(window, item)
                feedback.close.assert_called_once()
                self.assertNotIn("1", window.busy)
