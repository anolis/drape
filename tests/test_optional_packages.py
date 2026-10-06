"""Optional installers cannot run without reviewing and accepting the exact command."""

import unittest
from unittest import mock

from drape.ui import optional_packages as packages


class OptionalPackageTests(unittest.TestCase):
    def setUp(self):
        self.parent, self.finished = mock.Mock(), mock.Mock()
        self.callbacks = []
        for patch in (
            mock.patch.object(
                packages, "run_async", side_effect=lambda *args: self.callbacks.append(args)
            ),
            mock.patch.object(
                packages.dependencies, "install_command", return_value=["pacman", "-S", "cava"]
            ),
            mock.patch.object(packages.dependencies, "pacman_database_missing", return_value=False),
            mock.patch.object(packages.os, "geteuid", return_value=1000),
            mock.patch.object(packages.shutil, "which", return_value="/usr/bin/pkexec"),
            mock.patch.object(packages.Gtk, "MessageDialog"),
            mock.patch.object(packages.dependencies, "Dialogs"),
            mock.patch.object(packages, "error_dialog"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        packages.dependencies.Dialogs.return_value.install.return_value = (True, "")

    def start(self):
        packages.install(self.parent, ["cava"], self.finished)
        return self.callbacks.pop()

    def test_cancel_cannot_install_and_finishes_once(self):
        packages.Gtk.MessageDialog.return_value.run.return_value = packages.Gtk.ResponseType.NO
        work, done, _ = self.start()
        done(work())
        packages.dependencies.Dialogs.return_value.install.assert_not_called()
        self.finished.assert_called_once()

    def test_confirmed_upgrade_shows_command_and_scope_before_install(self):
        packages.dependencies.pacman_database_missing.return_value = True
        packages.Gtk.MessageDialog.return_value.run.return_value = packages.Gtk.ResponseType.YES
        work, done, _ = self.start()
        done(work())
        text = packages.Gtk.MessageDialog.return_value.format_secondary_text.call_args.args[0]
        self.assertIn("pkexec pacman -Syu cava", text)
        self.assertIn("upgrades all installed system packages", text)
        packages.dependencies.Dialogs.return_value.install.assert_called_once_with(
            ["pkexec", "pacman", "-Syu", "cava"]
        )
        self.finished.assert_called_once()

    def test_closed_parent_during_probe_cannot_prompt_or_install(self):
        work, done, _ = self.start()
        result = work()
        self.parent.connect.call_args.args[1]()
        done(result)
        packages.Gtk.MessageDialog.assert_not_called()
        packages.dependencies.Dialogs.return_value.install.assert_not_called()
        self.finished.assert_not_called()

    def test_install_failure_is_reported_and_controls_are_released(self):
        packages.Gtk.MessageDialog.return_value.run.return_value = packages.Gtk.ResponseType.YES
        packages.dependencies.Dialogs.return_value.install.return_value = (
            False,
            "cancelled authentication",
        )
        work, done, _ = self.start()
        done(work())
        packages.error_dialog.assert_called_once()
        self.finished.assert_called_once()
