"""Qt engine setup and per-version availability in the appearance settings."""

import os
import shlex
import shutil

from .. import dependencies, qt, qt_apps
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
        reset = Gtk.Button(label="Use system default")
        reset.connect("clicked", self._disable)
        actions.pack_start(reset, False, False, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda *_: self._refresh())
        browse_actions.pack_start(refresh, False, False, 0)
        self.body.pack_start(actions, False, False, 0)
        self.body.pack_start(browse_actions, False, False, 0)
        if shutil.which("opensnitch-ui"):
            override = qt_apps.opensnitch_theme()
            message = "OpenSnitch: " + (
                f"custom theme {override} overrides the Qt appearance."
                if override
                else "using the system theme."
            )
            self.body.pack_start(Gtk.Label(label=message, xalign=0, wrap=True), False, False, 0)
            button = Gtk.Button(label="Use Kvantum in OpenSnitch", sensitive=bool(found))
            button.set_halign(Gtk.Align.START)
            button.connect("clicked", self._opensnitch)
            self.body.pack_start(button, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label=qt.RESTART_NOTE
                + "\n\nApplications with their own custom stylesheet can override the Qt theme. In OpenSnitch, choose the system/default UI theme in Preferences. Sandboxed applications may need the engine inside their sandbox. Qt Quick applications use a separate styling system.",
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

    def _opensnitch(self, button):
        dialog = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Apply the Qt theme to OpenSnitch?",
        )
        dialog.format_secondary_text(
            "This switches OpenSnitch's appearance to System and restarts its GUI with Kvantum. The firewall service keeps running."
        )
        accepted = dialog.run() == Gtk.ResponseType.YES
        dialog.destroy()
        if not accepted:
            return
        button.set_sensitive(False)
        button.set_label("Restarting OpenSnitch…")

        def work():
            qt_apps.check_opensnitch_engine()
            if not qt.enable():
                raise ValueError("Install a Kvantum engine first.")
            qt_apps.opensnitch_system_theme()
            return qt_apps.restart_opensnitch()

        def done(_result):
            if self.alive:
                self.load()
                self.win.notify("OpenSnitch was relaunched with Kvantum and its System theme.")

        def failed(exc):
            if self.alive:
                self.load()
                error_dialog(self.win, "Could not apply Qt appearance to OpenSnitch", exc)

        run_async(work, done, failed)

    def _disable(self, _button):
        try:
            qt.disable()
        except OSError as exc:
            error_dialog(self.win, "Could not restore the system Qt style", exc)
            return
        self.win.notify(
            "Drape's Qt style override was removed. Log out and back in to restore your system style."
        )
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
