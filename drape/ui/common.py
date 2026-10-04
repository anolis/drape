"""Shared theme metadata, matching and small UI helpers."""

import sys
import threading
from pathlib import Path

from .. import desktop, system
from .. import helper as root_helper
from ..installer import system_file_name
from .gtk import GLib, Gtk

APP_ID = "io.github.anolis.Drape"

CARD_W, CARD_H = 260, 160


PART_NAMES = {
    "packs": "Theme pack",
    "icons": "Icons",
    "cursors": "Cursors",
    "gtk": "GTK applications",
    "libadwaita": "GNOME / libadwaita (GTK 4)",
    "kvantum": "Qt applications (Kvantum)",
    "wm": "Window borders",
    "desktop": "Desktop",
    "wallpapers": "Wallpaper",
    "plymouth": "Boot splash",
    "login": "Login screen",
    "plasma": "Plasma style",
    "lookandfeel": "Global theme",
    "colors": "Color scheme",
    "aurorae": "KWin borders",
}

# which glyph a browse tab expects to see
TAB_PART = {
    "icons": "icons",
    "cursors": "cursors",
    "gtk": "gtk",
    "libadwaita": "libadwaita",
    "kvantum": "kvantum",
    "wm": "wm",
    "desktop": "desktop",
    "wallpapers": "wallpapers",
    "login": "login",
    "boot": "plymouth",
    "colors": "colors",
    "lookandfeel": "lookandfeel",
}


# tabs whose installed items are identified by other part names
TAB_PARTS = {"login": {"sddm", "webgreeter"}, "boot": {"plymouth"}}


LOGIN_KEYS = {
    "gtk": ("gtk", "theme-name"),
    "icons": ("icons", "icon-theme-name"),
    "cursors": ("icons", "cursor-theme-name"),
}


# Privileged login-screen copy commands


def login_commands(greeter, kind, component):
    """Helper commands that copy an installed item into /usr/share and point the greeter at it
    (the login screen runs as its own user and can't read your home folder)."""
    if kind == "wallpapers":
        name = system_file_name(component)
        return [
            ["install", "background", component["path"], "--name", name],
            ["greeter-set", greeter, f"background={root_helper.DIRS['background'] / name}"],
        ]
    target, key = LOGIN_KEYS[kind]
    name = component["name"]
    dest = root_helper.DIRS[target] / name
    cmds = []
    # a system theme of the same name is already readable by the login screen
    if not dest.exists() or (dest / root_helper.MARKER).exists():
        cmds.append(["install", target, component["path"], "--name", name])
    cmds.append(["greeter-set", greeter, f"{key}={name}"])
    return cmds


def system_theme_active(kind, name):
    if kind == "plymouth":
        return system.current_plymouth() == name
    if kind == "sddm":
        return system.display_manager() == "sddm" and system.current_sddm_theme() == name
    if kind == "webgreeter":
        return system.current_web_greeter_theme() == name
    return False


# Shared component matching


def matches(kind, component):
    if kind == "libadwaita":
        return "libadwaita" in desktop.compatible_parts(component)
    if kind == "packs":
        return bool(set(desktop.compatible_parts(component)) & desktop.PACK_PARTS)
    if kind in ("wm", "desktop"):
        return desktop.theme_part(kind) in component["provides"]
    return bool(TAB_PARTS.get(kind, {kind}) & set(component["provides"]))


# Worker-to-GTK main-loop dispatch


def run_async(work, done, error=None):
    """Run work() on a thread, then done(result) or error(exc) on the main loop."""

    def target():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            if error:
                GLib.idle_add(error, e)
            else:
                print(f"drape: {e}", file=sys.stderr)
            return
        GLib.idle_add(done, result)

    threading.Thread(target=target, daemon=True).start()


# Current component detection


def in_use(component):
    """True if this installed component is what the desktop is currently using."""
    kind = component.get("system")
    if kind == "plymouth":
        return system.current_plymouth() == component["name"]
    if kind == "sddm":
        return (
            system.display_manager() == "sddm" and system.current_sddm_theme() == component["name"]
        )
    if kind == "webgreeter":
        return system.current_web_greeter_theme() == component["name"]
    for part in desktop.compatible_parts(component):
        value = Path(component["path"]).as_uri() if part == "wallpapers" else component["name"]
        if desktop.get(part) == value:
            return True
    return False


# UI error reporting


def error_dialog(parent, title, err):
    d = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    d.format_secondary_text(str(err))
    d.run()
    d.destroy()


def _safe(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"drape: preview failed: {e}", file=sys.stderr)
