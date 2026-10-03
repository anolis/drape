"""Nonblocking app update checks and an explicit update-and-restart action."""

import os
import sys
import time

from .. import settings, updater
from .common import error_dialog, run_async
from .gtk import GLib, Gtk


class AppUpdates:
    def __init__(self, window):
        self.window = window
        self.checking = False
        GLib.timeout_add_seconds(5, self.automatic)

    def automatic(self):
        last = settings.get("app_update_checked_at") or 0
        if (not self.window._closing.is_set() and settings.get("check_app_updates") and
                time.time() - last >= 86400):
            self.check()
        return False

    def check(self, manual=False):
        if self.checking:
            return
        self.checking = True
        if manual:
            self.window.notify("Checking for Drape updates…")

        def done(update):
            self.checking = False
            if self.window._closing.is_set():
                return
            try:
                settings.set("app_update_checked_at", time.time())
            except OSError:
                pass
            if update:
                self.window.notify("A Drape update is available.", action=("View update", lambda: self.offer(update)))
            elif manual:
                self.window.notify("Drape is up to date.")

        def failed(exc):
            self.checking = False
            if manual and not self.window._closing.is_set():
                error_dialog(self.window, "Could not check for Drape updates", exc)

        run_async(updater.check, done, failed)

    def offer(self, update):
        win = self.window
        if win.busy:
            win.notify("Finish the current theme installation before updating Drape.",
                     action=("View update", lambda: self.offer(update)))
            return
        dialog = Gtk.MessageDialog(transient_for=win, modal=True,
                                   message_type=Gtk.MessageType.INFO, text="A Drape update is available")
        dialog.format_secondary_text(f"{update.count} new commit(s):\n\n{update.summary}\n\n" +
                                     (update.blocked or "Update this checkout and restart Drape now?"))
        dialog.add_button("Later", Gtk.ResponseType.CANCEL)
        if not update.blocked:
            dialog.add_button("Update and restart", Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        progress = Gtk.MessageDialog(transient_for=win, modal=True, text="Updating Drape…")
        progress.format_secondary_text("Drape will restart when the update finishes.")
        progress.connect("delete-event", lambda *_: True)
        spinner = Gtk.Spinner()
        progress.get_content_area().pack_end(spinner, False, False, 12)
        spinner.start()
        finished = []

        def done(_result):
            finished.append(True)
            progress.response(Gtk.ResponseType.OK)

        errors = []

        def failed(exc):
            errors.append(exc)
            finished.append(True)
            progress.response(Gtk.ResponseType.CANCEL)

        run_async(lambda: updater.apply(update), done, failed)
        progress.show_all()
        while not finished:
            progress.run()
        progress.destroy()
        if errors:
            error_dialog(win, "Could not update Drape", errors[0])
        else:
            os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
