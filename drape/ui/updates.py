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
        self.available = None
        # A dedicated header action survives unrelated theme/status notifications.
        self.button = Gtk.Button(label="Update available", no_show_all=True)
        self.button.get_style_context().add_class("suggested-action")
        self.button.set_tooltip_text("Review the available Drape update")
        self.button.connect("clicked", lambda *_: self.available and self.offer(self.available))
        window.get_titlebar().pack_start(self.button)
        self.startup_source = GLib.timeout_add_seconds(5, self.startup)
        self.periodic_source = GLib.timeout_add_seconds(3600, self.automatic)
        window.connect("destroy", self._destroy)

    # Check each launch, then hourly while open, without blocking the GTK thread.

    def startup(self):
        self.startup_source = None
        if not self.window._closing.is_set() and settings.get("check_app_updates"):
            self.check()
        return False

    def automatic(self):
        last = settings.get("app_update_checked_at") or 0
        if (
            not self.window._closing.is_set()
            and settings.get("check_app_updates")
            and time.time() - last >= 3600
        ):
            self.check()
        return True

    def _destroy(self, *_):
        for source in (self.startup_source, self.periodic_source):
            if source is not None:
                GLib.source_remove(source)
        self.startup_source = self.periodic_source = None

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
            self.available = update
            self.button.set_visible(update is not None)
            if update:
                self.button.set_tooltip_text(
                    f"{update.count} new commit(s) available. Click to review and update Drape."
                )
            elif manual:
                self.window.notify("Drape is up to date.")

        def failed(exc):
            self.checking = False
            if manual and not self.window._closing.is_set():
                error_dialog(self.window, "Could not check for Drape updates", exc)

        run_async(updater.check, done, failed)

    # Consent, update progress and restart

    def offer(self, update):
        win = self.window
        if win.busy or getattr(getattr(win, "configurations", None), "busy", False):
            win.notify(
                "Finish the current theme operation before updating Drape.",
            )
            return
        dialog = Gtk.MessageDialog(
            transient_for=win,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            text="A Drape update is available",
        )
        dialog.format_secondary_text(
            f"{update.count} new commit(s):\n\n{update.summary}\n\n"
            + (update.blocked or "Update this checkout and restart Drape now?")
        )
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
