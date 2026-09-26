"""Apply installed components to the running desktop via GSettings."""

import os
from pathlib import Path

from gi.repository import Gio

# component -> (schema, key) per desktop; first schema that exists wins
KEYS = {
    "cinnamon": {
        "icons": ("org.cinnamon.desktop.interface", "icon-theme"),
        "cursors": ("org.cinnamon.desktop.interface", "cursor-theme"),
        "gtk": ("org.cinnamon.desktop.interface", "gtk-theme"),
        "wm": ("org.cinnamon.desktop.wm.preferences", "theme"),
        "desktop": ("org.cinnamon.theme", "name"),
        "wallpapers": ("org.cinnamon.desktop.background", "picture-uri"),
    },
    "gnome": {
        "icons": ("org.gnome.desktop.interface", "icon-theme"),
        "cursors": ("org.gnome.desktop.interface", "cursor-theme"),
        "gtk": ("org.gnome.desktop.interface", "gtk-theme"),
        "wm": ("org.gnome.desktop.wm.preferences", "theme"),
        "wallpapers": ("org.gnome.desktop.background", "picture-uri"),
    },
}


FALLBACK = {"icons": "Adwaita", "cursors": "Adwaita", "gtk": "Adwaita", "wm": "Adwaita", "desktop": ""}


def _schema_exists(schema):
    src = Gio.SettingsSchemaSource.get_default()
    return src is not None and src.lookup(schema, True) is not None


def current_desktop():
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "cinnamon" in de and _schema_exists("org.cinnamon.desktop.interface"):
        return "cinnamon"
    if _schema_exists("org.gnome.desktop.interface"):
        return "gnome"
    return None


def _key(part):
    de = current_desktop()
    if de is None:
        return None
    sk = KEYS[de].get(part)
    return sk if sk and _schema_exists(sk[0]) else None


def supported(part):
    return _key(part) is not None


def get(part):
    sk = _key(part)
    return Gio.Settings.new(sk[0]).get_string(sk[1]) if sk else None


def set_(part, value):
    sk = _key(part)
    if sk is None:
        return False
    s = Gio.Settings.new(sk[0])
    if part != "wallpapers" and s.get_string(sk[1]) == value:
        # same name as before (e.g. a reinstalled theme): nothing would notice the change,
        # so switch away for a moment to make the desktop reload it
        s.set_string(sk[1], FALLBACK.get(part, "Adwaita"))
        Gio.Settings.sync()
    s.set_string(sk[1], value)
    if part == "wallpapers" and current_desktop() == "gnome":
        s.set_string("picture-uri-dark", value)
    Gio.Settings.sync()
    return True


def _set_default_cursor(name):
    """Point ~/.icons/default at the theme, for apps that don't follow the desktop's live setting."""
    index = Path.home() / ".icons" / "default" / "index.theme"
    try:
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(f"[Icon Theme]\nName=Default\nComment=Set by drape\nInherits={name}\n")
    except OSError:
        pass


def apply_component(component, only=None):
    """Apply one manifest component (dict with provides/name/path). Returns the parts applied."""
    applied = []
    for part in component["provides"]:
        if only and part not in only:
            continue
        value = Path(component["path"]).as_uri() if part == "wallpapers" else component["name"]
        if set_(part, value):
            applied.append(part)
            if part == "cursors":
                _set_default_cursor(value)
    return applied
