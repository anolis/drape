"""Plasma theme formats and native apply tools (Plasma 5 and 6)."""

import configparser
import json
import os
import re
import shutil
import subprocess
import sysconfig
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gi.repository import Gio, GLib

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
DIRECTORIES = {"plasma": "plasma/desktoptheme", "lookandfeel": "plasma/look-and-feel",
               "colors": "color-schemes", "aurorae": "aurorae/themes"}
TOOLS = {"plasma": "plasma-apply-desktoptheme", "lookandfeel": "plasma-apply-lookandfeel",
         "colors": "plasma-apply-colorscheme", "cursors": "plasma-apply-cursortheme",
         "wallpapers": "plasma-apply-wallpaperimage", "icons": "plasma-changeicons",
         "aurorae": "kwin-applywindowdecoration"}
CONFIG_KEYS = {"plasma": ("plasmarc", "Theme", "name"),
               "lookandfeel": ("kdeglobals", "KDE", "LookAndFeelPackage"),
               "colors": ("kdeglobals", "General", "ColorScheme"),
               "icons": ("kdeglobals", "Icons", "Theme"),
               "cursors": ("kcminputrc", "Mouse", "cursorTheme"),
               "aurorae": ("kwinrc", "org.kde.kdecoration2", "theme")}


class ApplyError(Exception):
    pass


def major_version():
    value = os.environ.get("KDE_SESSION_VERSION")
    if value in ("5", "6"):
        return int(value)
    return 6 if shutil.which("kpackagetool6") else 5


def tool(name):
    found = shutil.which(name)
    if found:
        return found
    # Several distributions put the icon/decorations helpers outside PATH.
    multiarch = sysconfig.get_config_var("MULTIARCH")
    roots = [Path("/usr/libexec"), Path("/usr/lib"), Path("/usr/lib/qt6/libexec"), Path("/usr/lib/qt5/libexec")]
    if multiarch:
        roots.append(Path("/usr/lib") / multiarch / "libexec")
    return next((str(p / name) for p in roots if (p / name).is_file() and os.access(p / name, os.X_OK)), None)


def supported(part):
    return part in TOOLS and tool(TOOLS[part]) is not None


def _dbus(service, path, interface, method, parameters=None):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(service, path, interface, method, parameters, None,
                         Gio.DBusCallFlags.NO_AUTO_START, 1500, None).unpack()


def kwin_running():
    try:
        return bool(_dbus("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", GLib.Variant("(s)", ("org.kde.KWin",)))[0])
    except GLib.Error:
        return False


def get(part):
    if part == "wallpapers":
        try:
            # Reading the first desktop keeps the existing single-wallpaper UI useful.
            script = 'var ds = desktops(); if (ds.length) { ds[0].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"]; print(ds[0].readConfig("Image", "")); }'
            return _dbus("org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell",
                         "evaluateScript", GLib.Variant("(s)", (script,)))[0].strip()
        except GLib.Error:
            return None
    key = CONFIG_KEYS.get(part)
    reader = tool(f"kreadconfig{major_version()}")
    if not key or not reader:
        return None
    try:
        r = subprocess.run([reader, "--file", key[0], "--group", key[1], "--key", key[2]],
                           capture_output=True, text=True, timeout=3)
        value = r.stdout.rstrip("\n") if r.returncode == 0 else None
        return value.removeprefix("__aurorae__svg__") if value and part == "aurorae" else value
    except (OSError, subprocess.SubprocessError):
        return None


def apply(part, value):
    program = tool(TOOLS.get(part, "")) if part in TOOLS else None
    if not program:
        raise ApplyError(f"KDE's {TOOLS.get(part, part)} tool is not installed.")
    if part == "wallpapers" and value.startswith("file:"):
        uri = urlsplit(value)
        if uri.netloc not in ("", "localhost"):
            raise ApplyError("KDE wallpapers must be local files.")
        value = unquote(uri.path)
    if part == "aurorae":
        path = locate(part, value)
        if path:
            value = str(path)
    # Never reset the desktop/panel layout when applying a global theme.
    args = [program, "--apply", value] if part == "lookandfeel" else [program, value]
    if value.startswith("-"):
        raise ApplyError("Theme names cannot begin with a dash.")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise ApplyError(f"Could not apply the KDE theme: {e}") from e
    if r.returncode != 0:
        raise ApplyError((r.stderr or r.stdout).strip()[:1000] or f"{Path(program).name} failed.")
    return True


def metadata(path):
    try:
        data = json.loads((path / "metadata.json").read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        ini.read(path / "metadata.desktop")
        section = ini["Desktop Entry"]
        return {"KPlugin": {"Id": section.get("X-KDE-PluginInfo-Name", "")},
                "KPackageStructure": section.get("X-KDE-ServiceTypes", "")}
    except (OSError, KeyError, configparser.Error):
        return {}


def classify_dir(path):
    meta = metadata(path)
    structure = meta.get("KPackageStructure", "")
    if "Plasma/LookAndFeel" in structure or (meta and (path / "contents/defaults").is_file()):
        return "lookandfeel"
    if "Plasma/Theme" in structure or (meta and any((path / d).is_dir() for d in ("widgets", "dialogs", "contents/widgets"))):
        return "plasma"
    if any((path / n).is_file() for n in ("decoration.svg", "decoration.svgz")) and any(path.glob("*rc")):
        return "aurorae"
    if "KWin/Decoration" in structure and (path / "contents/ui/main.qml").is_file():
        return "aurorae"
    return None


def theme_name(path, fallback):
    plugin = metadata(path).get("KPlugin", {})
    name = plugin.get("Id") if isinstance(plugin, dict) else None
    name = name or fallback
    if not isinstance(name, str) or not re.fullmatch(r"[\w][\w .+@-]*", name, re.UNICODE) or name in (".", ".."):
        raise ValueError("Invalid KDE theme ID in metadata")
    return name


def compatible(part, path):
    if part == "lookandfeel" and major_version() >= 6:
        return (path / "metadata.json").is_file() and metadata(path).get("KPackageStructure") == "Plasma/LookAndFeel"
    return True


def locate(part, name):
    folder = DIRECTORIES.get(part)
    if not folder or not name:
        return None
    filename = name + ".colors" if part == "colors" else name
    for base in (DATA_HOME, *(Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"))):
        p = base / folder / filename
        if p.exists():
            return p
    return None
