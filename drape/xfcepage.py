"""Panel appearance controls shown only in an Xfce session."""

import threading

from gi.repository import GLib, Gtk

from . import desktop, xfce
from .lockpage import _framed, _row, _section


class XfcePanelPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window = window
        self.busy = False
        self.undo = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        self.add(self.box)
        section = _section("Xfce panel")
        self.box.pack_start(section, False, False, 0)
        section.pack_start(
            Gtk.Label(
                label="The taskbar uses your GTK Controls theme. Use the theme background below to let it style the panel.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        themes = Gtk.Button(label="Browse Controls themes")
        themes.connect("clicked", lambda *_: window.go_to("gtk"))
        section.pack_start(themes, False, False, 0)
        self.selector = Gtk.ComboBoxText()
        self.selector.connect("changed", self._selected)
        refresh = Gtk.Button(label="Refresh panels")
        refresh.connect("clicked", lambda *_: self.load())
        section.pack_start(_row("Panel", self.selector), False, False, 0)
        section.pack_start(refresh, False, False, 0)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.controls.set_sensitive(False)
        self.box.pack_start(self.controls, False, False, 0)
        self.size = Gtk.SpinButton.new_with_range(16, 128, 1)
        self.length = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.hide = Gtk.ComboBoxText()
        for label in ("Never", "When windows overlap", "Always"):
            self.hide.append_text(label)
        self.locked = Gtk.Switch()
        self.theme = Gtk.Switch()
        self.controls.pack_start(
            _framed(
                [
                    _row("Panel size (px)", self.size),
                    _row("Panel length (%)", self.length),
                    _row("Automatically hide", self.hide),
                    _row("Lock position", self.locked),
                    _row(
                        "Use GTK theme background",
                        self.theme,
                        "Turn off to keep the existing color or image override.",
                    ),
                ]
            ),
            False,
            False,
            0,
        )
        self.preset = Gtk.ComboBoxText()
        self.preset.append_text("Keep current layout")
        for name in xfce.PRESETS:
            self.preset.append_text(name)
        self.preset.set_active(0)
        self.controls.pack_start(
            _row(
                "Layout preset",
                self.preset,
                "Presets set size, length, orientation and hiding for this panel.\n"
                "Launchers, widgets and other panels stay in place.",
            ),
            False,
            False,
            0,
        )
        apply = Gtk.Button(label="Apply panel changes")
        apply.get_style_context().add_class("suggested-action")
        apply.connect("clicked", self._apply)
        self.controls.pack_start(apply, False, False, 0)
        self.undo_button = Gtk.Button(label="Undo last panel change")
        self.undo_button.set_sensitive(False)
        self.undo_button.connect("clicked", self._undo)
        self.box.pack_start(self.undo_button, False, False, 0)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.box.pack_start(self.status, False, False, 0)
        self.box.pack_start(
            Gtk.Label(
                xalign=0,
                wrap=True,
                label="Wallpapers applied in drape use the same image on all configured monitors and workspaces, "
                "and stop wallpaper cycling. Window borders are separate: choose an Xfwm theme under Window borders.",
            ),
            False,
            False,
            0,
        )

    def _run(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.box.set_sensitive(False)
        self.status.set_text("Reading Xfce settings…")

        def finish(result, error):
            self.busy = False
            self.box.set_sensitive(True)
            if error:
                self.status.set_text(str(error))
            else:
                self.status.set_text("")
                done(result)
            return False

        def work_thread():
            try:
                result = work()
            except Exception as exc:
                GLib.idle_add(finish, None, exc)
            else:
                GLib.idle_add(finish, result, None)

        threading.Thread(target=work_thread, daemon=True).start()

    def load(self):
        if desktop.current_desktop() != "xfce" or self.busy:
            return
        selected = self.selector.get_active_id()

        def loaded(ids):
            self.selector.handler_block_by_func(self._selected)
            self.selector.remove_all()
            for panel in ids:
                self.selector.append(str(panel), f"Panel {panel}")
            self.selector.set_active_id(
                selected if selected in {str(i) for i in ids} else str(ids[0]) if ids else ""
            )
            self.selector.handler_unblock_by_func(self._selected)
            self.controls.set_sensitive(bool(ids))
            if ids:
                self._selected()
            else:
                self.status.set_text("No panels configured. Start the Xfce panel and refresh.")

        self._run(xfce.panels, loaded)

    def _selected(self, *_):
        panel = self.selector.get_active_id()
        if panel is None:
            return
        self.controls.set_sensitive(False)

        def loaded(values):
            self.values = values
            self.size.set_value(values["size"])
            self.length.set_value(values["length"])
            self.hide.set_active(int(values["autohide-behavior"]))
            self.locked.set_active(values["position-locked"])
            self.theme.set_active(values["background-style"] == 0)
            self.preset.set_active(0)
            self.controls.set_sensitive(True)

        self._run(lambda: xfce.panel_settings(int(panel)), loaded)

    def _apply(self, *_):
        if desktop.current_desktop() != "xfce":
            self.status.set_text("Panel controls require an Xfce session.")
            return
        panel = int(self.selector.get_active_id())
        values = {
            "size": self.size.get_value_as_int(),
            "length": self.length.get_value_as_int(),
            "autohide-behavior": self.hide.get_active(),
            "position-locked": self.locked.get_active(),
        }
        if self.theme.get_active():
            values["background-style"] = 0
        preset = self.preset.get_active_text() if self.preset.get_active() > 0 else None

        def applied(before):
            self.undo = (panel, before)
            self.undo_button.set_sensitive(True)
            self._selected()

        self._run(lambda: xfce.apply_panel(panel, values, preset), applied)

    def _undo(self, *_):
        if not self.undo or desktop.current_desktop() != "xfce":
            return
        panel, before = self.undo

        def work():
            if panel not in xfce.panels():
                raise xfce.ApplyError(
                    "The panel has been removed; its settings cannot be restored."
                )
            xfce.restore(xfce.PANEL_CHANNEL, before)

        def restored(_):
            self.undo = None
            self.undo_button.set_sensitive(False)
            self._selected()

        self._run(work, restored)
