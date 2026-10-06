import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from drape import updater
from drape.ui import updates


class UpdateNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.preferences = {"check_app_updates": True, "app_update_checked_at": 1000}
        self.calls = []
        self.window = SimpleNamespace(
            _closing=threading.Event(),
            busy={},
            configurations=SimpleNamespace(busy=False),
            notify=mock.Mock(),
            get_titlebar=mock.Mock(),
            connect=mock.Mock(),
        )
        for patch in (
            mock.patch.object(updates.Gtk, "Button"),
            mock.patch.object(updates.GLib, "timeout_add_seconds", side_effect=[100, 101]),
            mock.patch.object(updates.GLib, "source_remove"),
            mock.patch.object(updates.settings, "get", side_effect=self.preferences.get),
            mock.patch.object(updates.settings, "set"),
            mock.patch.object(
                updates, "run_async", side_effect=lambda *args: self.calls.append(args)
            ),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.controller = updates.AppUpdates(self.window)
        self.update = updater.Update("old", "new", 3, "Feature changes")

    def test_startup_checks_even_if_previous_launch_checked_recently(self):
        with mock.patch.object(updates.time, "time", return_value=1001):
            self.assertFalse(self.controller.startup())
        self.assertEqual(len(self.calls), 1)
        self.assertIs(self.calls[0][0], updater.check)

    def test_automatic_checks_repeat_hourly_and_respect_preference(self):
        with mock.patch.object(updates.time, "time", return_value=4600):
            self.assertTrue(self.controller.automatic())
        self.assertEqual(len(self.calls), 1)
        self.preferences["check_app_updates"] = False
        self.controller.checking = False
        self.controller.startup()
        self.controller.automatic()
        self.assertEqual(len(self.calls), 1)

    def test_update_header_action_survives_other_notifications(self):
        self.controller.check()
        self.calls.pop()[1](self.update)
        self.controller.button.set_visible.assert_called_with(True)
        self.window.notify.assert_not_called()
        self.window.notify("Applied a theme")
        self.assertIs(self.controller.available, self.update)
        self.controller.button.set_visible.assert_called_once_with(True)
        clicked = self.controller.button.connect.call_args.args[1]
        with mock.patch.object(self.controller, "offer") as offer:
            clicked(self.controller.button)
        offer.assert_called_once_with(self.update)

    def test_successful_current_check_clears_notice_but_failed_check_keeps_it(self):
        self.controller.check()
        self.calls.pop()[1](self.update)
        self.controller.check()
        self.calls.pop()[2](OSError("offline"))
        self.assertIs(self.controller.available, self.update)
        self.controller.check()
        self.calls.pop()[1](None)
        self.assertIsNone(self.controller.available)
        self.controller.button.set_visible.assert_called_with(False)

    def test_late_result_cannot_update_destroyed_window(self):
        self.controller.check()
        self.window._closing.set()
        self.calls.pop()[1](self.update)
        self.controller.button.set_visible.assert_not_called()
        self.window.notify.assert_not_called()

    def test_update_waits_for_configuration_operation(self):
        self.window.configurations.busy = True
        with mock.patch.object(updates.Gtk, "MessageDialog") as dialog:
            self.controller.offer(self.update)
        dialog.assert_not_called()
        self.assertIn("Finish the current theme operation", self.window.notify.call_args.args[0])

    def test_canceling_offer_never_applies_an_update(self):
        with mock.patch.object(updates.Gtk, "MessageDialog") as dialog:
            dialog.return_value.run.return_value = updates.Gtk.ResponseType.CANCEL
            self.controller.offer(self.update)
        self.assertEqual(self.calls, [])

    def test_destroy_removes_both_background_timers(self):
        self.controller._destroy()
        updates.GLib.source_remove.assert_has_calls([mock.call(100), mock.call(101)])
