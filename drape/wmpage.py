"""The Window manager settings page: what manages windows now, Compiz, and ccsm hosted inside drape."""

import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import compiz  # noqa: E402
from .lockpage import _framed, _row, _section  # noqa: E402

COMPIZ_PITCH = ("Wobbly windows, a desktop cube, Expo, window previews, fire and water effects and "
                "dozens more, each with its own settings.")


class WindowManagerPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = window
        self.ccsm = None
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=24)
        self.add(self.body)

    def load(self):
        for c in self.body.get_children():
            if c is not self.ccsm_section():
                c.destroy()
        self.body.pack_start(self._current(), False, False, 0)
        self.body.pack_start(self._compiz(), False, False, 0)
        if compiz.ccsm_available():
            section = self.ccsm_section(create=True)
            if section.get_parent() is None:
                self.body.pack_start(section, True, True, 0)
            self.body.reorder_child(section, -1)
        self.show_all()

    # ------------------------------------------------------------ what's running
    def _current(self):
        box = _section("Window manager")
        sess, wm = compiz.session(), compiz.running_wm() or "unknown"
        text = GLib.markup_escape_text
        if sess == "cinnamon":
            msg = (f"You're in <b>Cinnamon</b>, which manages windows itself (<b>{text(wm)}</b>). Its look comes "
                   "from your Window borders and Desktop themes.")
        else:
            msg = f"You're in <b>{text(sess.upper() if sess == 'mate' else sess.title())}</b>, using <b>{text(wm)}</b>."
        info = Gtk.Label(xalign=0, wrap=True)
        info.set_markup(msg)
        box.pack_start(info, False, False, 0)

        if sess in ("mate", "xfce"):
            own = "marco" if sess == "mate" else "xfwm4"
            rows, group = [], None
            current = "compiz" if "compiz" in wm.lower() else own
            for key in ("compiz", own):
                label, _cmd = compiz.WINDOW_MANAGERS[key]
                rb = Gtk.RadioButton.new_with_label_from_widget(group, label)
                group = group or rb
                rb.set_active(key == current)
                rb.set_sensitive(compiz.installed(key))
                rb.connect("toggled", lambda b, k=key: b.get_active() and self._switch(k))
                rows.append(rb)
            chooser = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
            for rb in rows:
                chooser.pack_start(rb, False, False, 0)
            box.pack_start(chooser, False, False, 0)
        return box

    def _switch(self, wm):
        compiz.switch_live(wm)
        if compiz.session() == "mate":
            compiz.use_compiz_in_mate(wm == "compiz")
        self.win.notify(f"Switched to {compiz.WINDOW_MANAGERS[wm][0]}.")
        GLib.timeout_add_seconds(3, lambda: (self.load(), False)[1])

    # ------------------------------------------------------------ Compiz
    def _compiz(self):
        sess = compiz.session()
        box = _section("Want even more? Compiz")
        pitch = Gtk.Label(label=COMPIZ_PITCH, xalign=0, wrap=True)
        box.pack_start(pitch, False, False, 0)
        rows = []
        if not compiz.compiz_installed():
            if sess in ("mate", "xfce"):
                how = "drape installs Compiz and its settings manager; you can switch to it right here."
            else:
                how = ("Compiz can't run inside Cinnamon, so drape also adds a small MATE + Compiz session that you "
                       "pick at the login screen. Cinnamon stays exactly as it is, and you can switch back by logging out.")
            b = Gtk.Button(label="Install Compiz")
            b.get_style_context().add_class("suggested-action")
            b.connect("clicked", lambda _b: self._install())
            rows.append(_row("Compiz, its settings and all the plugins", b, how))
        elif sess not in ("mate", "xfce"):
            ready = compiz.mate_session_installed()
            if ready:
                if compiz.mate_wm_setting() != "compiz":
                    compiz.use_compiz_in_mate(True)
                b = Gtk.Button(label="Log out")
                b.connect("clicked", lambda _b: self._log_out())
                rows.append(_row("Compiz is installed", b,
                                 "To use it, log out and choose MATE at the login screen. Effects you set up below "
                                 "are ready when you get there."))
            else:
                b = Gtk.Button(label="Add the MATE + Compiz session")
                b.connect("clicked", lambda _b: self._install())
                rows.append(_row("Compiz is installed", b, "Compiz needs a session it can run in, next to Cinnamon."))
        else:
            rows.append(_row("Compiz is installed", Gtk.Label(label="✓"),
                             "Choose it above to switch now."))
        box.pack_start(_framed(rows), False, False, 0)
        return box

    def _install(self):
        pkgs = compiz.packages_to_install()
        if not self.win.ask("Install Compiz?",
                            f"drape will install {len(pkgs)} packages: Compiz, its settings manager, the plugins "
                            + ("and a minimal MATE session to run it in. " if len(pkgs) > len(compiz.COMPIZ_PACKAGES)
                               else ". ") + "This needs your password.", "Install"):
            return

        def done():
            if compiz.session() not in ("mate", "xfce"):
                compiz.use_compiz_in_mate(True)
            self.win.notify("Compiz is installed. Restart drape to set up effects here"
                            + ("; log out and choose MATE at the login screen to use it."
                               if compiz.session() not in ("mate", "xfce") else "."))
            self.load()
        self.win.run_root([["apt-install", *pkgs]], "Installing Compiz…", done)

    def _log_out(self):
        for cmd in (["cinnamon-session-quit", "--logout"], ["mate-session-save", "--logout-dialog"]):
            try:
                subprocess.Popen(cmd, start_new_session=True)
                return
            except OSError:
                continue

    # ------------------------------------------------------------ ccsm
    def ccsm_section(self, create=False):
        if getattr(self, "_ccsm_section", None) is None and create:
            box = _section("Compiz effects")
            if compiz.session() not in ("mate", "xfce") or "compiz" not in (compiz.running_wm() or "").lower():
                note = Gtk.Label(xalign=0, wrap=True, label="Changes take effect the next time Compiz runs.")
                note.get_style_context().add_class("dim-label")
                box.pack_start(note, False, False, 0)
            try:
                self.ccsm = compiz.make_ccsm()
                self.win.ccsm = self.ccsm
                frame = Gtk.Frame()
                frame.set_size_request(-1, 520)
                frame.add(self.ccsm)
                box.pack_start(frame, True, True, 0)
            except Exception as e:  # noqa: BLE001 - show why rather than breaking the page
                box.pack_start(Gtk.Label(label=f"Couldn't load Compiz's settings: {e}", xalign=0, wrap=True),
                               False, False, 0)
            self._ccsm_section = box
        return getattr(self, "_ccsm_section", None)
