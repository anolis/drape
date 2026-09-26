"""The Lock & login page: what the login and lock screens look like, and changing them."""

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from . import desktop, installer, system  # noqa: E402
from . import helper as root_helper  # noqa: E402

SCREENSAVER = "org.cinnamon.desktop.screensaver"
LOGIN_OPTIONS = [
    # label, display manager, greeter (LightDM only), apt package
    ("LightDM with Slick Greeter", "lightdm", "slick-greeter", "slick-greeter"),
    ("LightDM with GTK Greeter", "lightdm", "lightdm-gtk-greeter", "lightdm-gtk-greeter"),
    ("SDDM", "sddm", None, "sddm"),
]


def _find_theme(kind, name):
    """(path, in_home) of an installed Controls theme / icon set / cursor theme called `name`."""
    home = [installer.THEMES_DIR, installer.DATA_HOME / "themes"] if kind == "gtk" else \
        [installer.ICONS_DIR, installer.CURSORS_DIR]
    for base in home:
        if (base / name).is_dir():
            return base / name, True
    system_dir = root_helper.DIRS["gtk" if kind == "gtk" else "icons"]
    if (system_dir / name).is_dir():
        return system_dir / name, False
    return None, False


def current_look_commands(greeter, login_commands):
    """Commands that give the login screen your current Controls theme, icons, cursor and wallpaper."""
    cmds = []
    for kind in ("gtk", "icons", "cursors"):
        name = desktop.get(kind)
        if not name:
            continue
        path, in_home = _find_theme(kind, name)
        if path is None:
            continue
        if in_home:
            cmds += login_commands(greeter, kind, {"name": name, "path": str(path)})
        else:
            cmds.append(["greeter-set", greeter, f"{login_key(kind)}={name}"])
    uri = desktop.get("wallpapers") or ""
    if uri.startswith("file://"):
        path = Path(GLib.filename_from_uri(uri)[0])
        if path.is_file():
            if path.is_relative_to("/usr/share/backgrounds"):
                cmds.append(["greeter-set", greeter, f"background={path}"])
            else:
                cmds += login_commands(greeter, "wallpapers", {"name": path.name, "path": str(path)})
    return cmds


def login_key(kind):
    return {"gtk": "theme-name", "icons": "icon-theme-name", "cursors": "cursor-theme-name"}[kind]


def theme_styles_lock_screen(name):
    """cinnamon-screensaver uses the Controls theme's .csstage rules when it has them."""
    for base in (installer.THEMES_DIR, installer.DATA_HOME / "themes", Path("/usr/share/themes")):
        gtk3 = base / name / "gtk-3.0"
        if gtk3.is_dir():
            return any("csstage" in p.read_text(errors="replace") for p in gtk3.glob("*.css"))
    return False


def _section(title):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_bottom=24)
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<big><b>{GLib.markup_escape_text(title)}</b></big>")
    box.pack_start(label, False, False, 0)
    return box


def _row(label, widget, hint=None):
    row = Gtk.Box(spacing=12)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    left.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    if hint:
        h = Gtk.Label(label=hint, xalign=0, wrap=True)
        h.get_style_context().add_class("dim-label")
        left.pack_start(h, False, False, 0)
    row.pack_start(left, True, True, 0)
    widget.set_valign(Gtk.Align.CENTER)
    row.pack_end(widget, False, False, 0)
    return row


def _framed(rows):
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.set_header_func(lambda row, before: row.set_header(Gtk.Separator() if before else None))
    for r in rows:
        r.set_margin_start(12)
        r.set_margin_end(12)
        r.set_margin_top(8)
        r.set_margin_bottom(8)
        lr = Gtk.ListBoxRow(activatable=False)
        lr.add(r)
        lb.add(lr)
    frame = Gtk.Frame()
    frame.add(lb)
    return frame


class LockLoginPage(Gtk.ScrolledWindow):
    def __init__(self, window, login_commands):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = window
        self.login_commands = login_commands
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=24)
        clamp = Gtk.Box()
        self.body.set_size_request(640, -1)
        clamp.set_center_widget(self.body)
        self.add(clamp)

    def load(self):
        for c in self.body.get_children():
            c.destroy()
        self.body.pack_start(self._login_section(), False, False, 0)
        self.body.pack_start(self._lock_section(), False, False, 0)
        self.show_all()

    # ------------------------------------------------------------ login screen
    def _login_section(self):
        box = _section("Login screen")
        dm, greeter = system.display_manager(), system.lightdm_greeter()
        current = f"LightDM with {greeter}" if dm == "lightdm" else (dm or "unknown")
        info = Gtk.Label(xalign=0, wrap=True)
        info.set_markup(f"You're using <b>{GLib.markup_escape_text(current)}</b>.")
        box.pack_start(info, False, False, 0)

        if dm == "lightdm" and greeter in system.GTK_GREETERS:
            cur = system.greeter_settings(greeter)
            rows = []
            for key, label in (("background", "Background"), ("theme-name", "Controls"),
                               ("icon-theme-name", "Icons"), ("cursor-theme-name", "Cursor")):
                value = cur.get(key)
                shown = Path(value).name if key == "background" and value else (value or "Default")
                v = Gtk.Label(label=shown, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=36)
                v.get_style_context().add_class("dim-label")
                rows.append(_row(label, v))
            use = Gtk.Button(label="Use my current desktop look")
            use.get_style_context().add_class("suggested-action")
            use.connect("clicked", lambda _b: self._use_current(greeter))
            rows.append(_row("Match your desktop",
                             use, "Your Controls theme, icons, cursor and wallpaper, copied so the login "
                                  "screen can use them. Or pick single items with ⋯ → Use for login screen "
                                  "on the Installed page."))
            box.pack_start(_framed(rows), False, False, 0)
        elif dm == "sddm":
            note = Gtk.Label(xalign=0, wrap=True,
                             label="SDDM uses its own themes: browse them on the Login screen page.")
            note.get_style_context().add_class("dim-label")
            box.pack_start(note, False, False, 0)

        sub = Gtk.Label(xalign=0, margin_top=12)
        sub.set_markup("<b>Login screen software</b>")
        box.pack_start(sub, False, False, 0)
        rows = []
        greeters = system.installed_greeters()
        for label, option_dm, option_greeter, package in LOGIN_OPTIONS:
            installed = (option_greeter in greeters) if option_greeter else system.which(option_dm) is not None
            active = dm == option_dm and (option_greeter is None or greeter == option_greeter)
            if active:
                w = Gtk.Label(label="✓ In use")
            elif installed or system.apt_available(package):
                w = Gtk.Button(label="Switch" if installed else "Install and switch")
                w.connect("clicked", lambda _b, o=(label, option_dm, option_greeter, package, installed):
                          self._switch(*o))
            else:
                w = Gtk.Label(label="Not available")
                w.get_style_context().add_class("dim-label")
            rows.append(_row(label, w))
        box.pack_start(_framed(rows), False, False, 0)
        return box

    def _use_current(self, greeter):
        from .app import login_commands
        cmds = current_look_commands(greeter, login_commands)
        if not cmds:
            self.win.notify("Couldn't find your current theme files to copy.")
            return
        self.win.run_root(cmds, "Updating the login screen…",
                          lambda: (self.win.notify("The login screen now matches your desktop."), self.load()))

    def _switch(self, label, dm, greeter, package, installed):
        esc = GLib.markup_escape_text
        if not self.win.ask(f"Switch to {label}?",
                            f"{'drape will install <tt>' + esc(package) + '</tt> and ' if not installed else ''}"
                            f"your login screen will change after you restart.\n\nIf anything looks wrong you "
                            "can switch back here.", "Switch"):
            return
        cmds = [] if installed else [["apt-install", package]]
        if dm == "lightdm":
            if system.which("lightdm") is None:
                cmds.append(["apt-install", "lightdm"])
            cmds.append(["use-greeter", greeter])
        if system.display_manager() != dm:
            cmds.append(["display-manager", dm])
        self.win.run_root(cmds, f"Switching to {label}…",
                          lambda: (self.win.notify(f"{label} will be your login screen after a restart."),
                                   self.load()))

    # ------------------------------------------------------------ lock screen
    def _lock_section(self):
        box = _section("Lock screen")
        de = desktop.current_desktop()
        if de == "gnome":
            note = Gtk.Label(xalign=0, wrap=True, label="GNOME's lock screen has its own wallpaper: choose "
                             "⋯ → Use for lock screen on a wallpaper on the Installed page.")
            box.pack_start(note, False, False, 0)
            return box
        src = Gio.SettingsSchemaSource.get_default()
        if de != "cinnamon" or not src or not src.lookup(SCREENSAVER, True):
            box.pack_start(Gtk.Label(xalign=0, label="drape can't change this desktop's lock screen."),
                           False, False, 0)
            return box

        theme = desktop.get("gtk") or ""
        styled = theme_styles_lock_screen(theme)
        note = Gtk.Label(xalign=0, wrap=True)
        note.set_markup(
            "Cinnamon's lock screen shows your desktop wallpaper, drawn with your Controls theme. "
            + (f"<b>{GLib.markup_escape_text(theme)}</b> includes lock screen styling." if styled else
               f"<b>{GLib.markup_escape_text(theme)}</b> doesn't style the lock screen, so it uses Cinnamon's "
               "default look."))
        note.get_style_context().add_class("dim-label")
        box.pack_start(note, False, False, 0)

        s = Gio.Settings.new(SCREENSAVER)
        rows = []
        clock = Gtk.Switch()
        s.bind("show-clock", clock, "active", Gio.SettingsBindFlags.DEFAULT)
        rows.append(_row("Show the clock", clock))
        custom = Gtk.Switch()
        s.bind("use-custom-format", custom, "active", Gio.SettingsBindFlags.DEFAULT)
        rows.append(_row("Custom time and date format", custom, "Uses the formats below (strftime codes)"))
        for key, label in (("time-format", "Time format"), ("date-format", "Date format")):
            e = Gtk.Entry(width_chars=16)
            s.bind(key, e, "text", Gio.SettingsBindFlags.DEFAULT)
            s.bind("use-custom-format", e, "sensitive", Gio.SettingsBindFlags.GET)
            rows.append(_row(label, e))
        for key, label in (("font-time", "Time font"), ("font-date", "Date font"),
                           ("font-message", "Message font")):
            fb = Gtk.FontButton()
            s.bind(key, fb, "font", Gio.SettingsBindFlags.DEFAULT)
            rows.append(_row(label, fb))
        msg = Gtk.Entry(width_chars=24, placeholder_text="None")
        s.bind("default-message", msg, "text", Gio.SettingsBindFlags.DEFAULT)
        rows.append(_row("Message", msg, "Shown under the clock"))
        box.pack_start(_framed(rows), False, False, 0)
        return box
