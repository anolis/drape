"""Qt engine setup and per-version availability in the appearance settings."""

import os
import shlex
import shutil

from .. import dependencies, qt
from .common import error_dialog, run_async
from .gtk import Gtk


class QtSettingsPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = window
        self.alive = True
        self.connect("destroy", lambda *_: setattr(self, "alive", False))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        self.add(self.body)

    def load(self):
        qt.refresh()
        for child in self.body.get_children():
            child.destroy()
        heading = Gtk.Label(xalign=0)
        heading.set_markup("<big><b>Qt application appearance</b></big>")
        self.body.pack_start(heading, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label="Kvantum themes Qt widget applications on any desktop. Qt 5 and Qt 6 need their own engine plugins. GTK themes do not style Qt applications.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        found = qt.engines()
        enabled = qt.configured()
        active = qt.session_active()
        session_text = (
            "Current desktop: Kvantum is active for newly launched Qt applications."
            if active is True
            else "Current desktop: log out and back in to activate the saved Kvantum setup."
            if enabled and active is False
            else "Current desktop: Kvantum is not active."
            if active is False
            else "Current desktop: activation could not be verified. Log out and back in after enabling."
        )
        self.body.pack_start(Gtk.Label(label=session_text, xalign=0, wrap=True), False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label="Setup: "
                + ("Kvantum enabled for future logins" if enabled else "Kvantum not enabled"),
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        self.body.pack_start(
            Gtk.Label(
                label="Selected Qt theme: " + (qt.get() or "Kvantum default"), xalign=0, wrap=True
            ),
            False,
            False,
            0,
        )
        for major in (5, 6):
            row = Gtk.Box(spacing=12)
            row.pack_start(
                Gtk.Label(
                    label=f"Qt {major}: "
                    + ("Kvantum installed" if major in found else "Kvantum not installed"),
                    xalign=0,
                ),
                True,
                True,
                0,
            )
            if major not in found:
                command = qt.install_command(major)
                if command and shutil.which("pkexec"):
                    button = Gtk.Button(label=f"Install Qt {major} support")
                    button.connect(
                        "clicked", lambda button, command=command: self._install(button, command)
                    )
                    row.pack_end(button, False, False, 0)
            self.body.pack_start(row, False, False, 0)
        actions = Gtk.Box(spacing=8)
        enable = Gtk.Button(
            label="Reapply Kvantum setup" if enabled else "Enable Kvantum for Qt applications",
            sensitive=bool(found),
        )
        enable.connect("clicked", self._enable)
        actions.pack_start(enable, False, False, 0)
        browse = Gtk.Button(label="Browse Qt themes", sensitive=bool(found))
        browse.connect("clicked", lambda *_: self.win.go_to("kvantum"))
        browse_actions = Gtk.Box(spacing=8)
        browse_actions.pack_start(browse, False, False, 0)
        reset = Gtk.Button(
            label="Restore previous Qt appearance", sensitive=enabled or qt.restore_available()
        )
        reset.connect("clicked", self._disable)
        actions.pack_start(reset, False, False, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda *_: self._refresh())
        browse_actions.pack_start(refresh, False, False, 0)
        self.body.pack_start(actions, False, False, 0)
        self.body.pack_start(browse_actions, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label=qt.RESTART_NOTE
                + "\n\nThis sets the Qt widget engine for the whole login session without changing application menu entries or autostart commands. Applications with their own stylesheet or explicit style command can override it. Sandboxed applications may need the engine inside their sandbox. Qt Quick applications use a separate styling system.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        if not found:
            link = Gtk.LinkButton.new_with_label(
                "https://github.com/tsujan/Kvantum/blob/master/Kvantum/INSTALL.md",
                "Kvantum installation instructions",
            )
            link.set_halign(Gtk.Align.START)
            self.body.pack_start(link, False, False, 0)
        self.show_all()

    def _refresh(self):
        self.load()
        self.win._sidebar_state = None
        self.win._sidebar_list.invalidate_filter()
        self.win.refresh_item()

    def _enable(self, _button):
        try:
            if not qt.enable():
                self.win.notify("Install a Kvantum engine before enabling Qt themes.")
                self.load()
                return
        except (OSError, ValueError) as exc:
            error_dialog(self.win, "Could not enable Qt themes", exc)
            return
        self.win.notify(qt.RESTART_NOTE)
        self.load()

    def _disable(self, _button):
        dialog = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Restore previous Qt appearance?",
        )
        dialog.format_secondary_text(qt.restore_description())
        accepted = dialog.run() == Gtk.ResponseType.YES
        dialog.destroy()
        if not accepted:
            return
        try:
            qt.disable()
        except (OSError, ValueError) as exc:
            error_dialog(self.win, "Could not restore the previous Qt appearance", exc)
            return
        self.win.notify("Qt setup restored. Log out and back in to use the restored appearance.")
        self.load()

    def _install(self, button, command):
        button.set_sensitive(False)
        button.set_label("Checking packages…")

        def failed(exc):
            if not self.alive:
                return
            error_dialog(self.win, "Could not install Qt support", exc)
            self.load()

        def ready(_result):
            if not self.alive:
                return
            dialog = Gtk.MessageDialog(
                transient_for=self.win,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Install Qt theme support?",
            )
            dialog.format_secondary_text(
                "Administrator authentication is required.\n\n" + shlex.join(command)
            )
            accepted = dialog.run() == Gtk.ResponseType.YES
            dialog.destroy()
            if accepted:
                ok, output = dependencies.Dialogs().install(
                    command if os.geteuid() == 0 else ["pkexec", *command]
                )
                if not ok:
                    error_dialog(self.win, "Qt engine installation failed or was canceled", output)
            self._refresh()

        run_async(lambda: qt.check_install(command), ready, failed)
