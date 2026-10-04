"""Detect theme compatibility and apply components via GSettings or Xfconf."""

import configparser
import functools
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gi.repository import Gio

from . import cursors, kde, libadwaita, qt, settings, xfce
from .kde import ApplyError
from .theme_css import NEW_DIALOG_RE, OLD_DIALOG_RE, cinnamon_css_imports, cinnamon_css_outdated

# component -> (schema, key) for the running desktop
KEYS = {
    "cinnamon": {
        "icons": ("org.cinnamon.desktop.interface", "icon-theme"),
        "cursors": ("org.cinnamon.desktop.interface", "cursor-theme"),
        "gtk": ("org.cinnamon.desktop.interface", "gtk-theme"),
        "wm": ("org.cinnamon.desktop.wm.preferences", "theme"),
        "desktop": ("org.cinnamon.theme", "name"),
        "wallpapers": ("org.cinnamon.desktop.background", "picture-uri"),
    },
    "mate": {
        "icons": ("org.mate.interface", "icon-theme"),
        "cursors": ("org.mate.peripherals-mouse", "cursor-theme"),
        "gtk": ("org.mate.interface", "gtk-theme"),
        "wm": ("org.mate.Marco.general", "theme"),
        "wallpapers": ("org.mate.background", "picture-filename"),
    },
    "gnome": {
        "icons": ("org.gnome.desktop.interface", "icon-theme"),
        "cursors": ("org.gnome.desktop.interface", "cursor-theme"),
        "gtk": ("org.gnome.desktop.interface", "gtk-theme"),
        "wm": ("org.gnome.desktop.wm.preferences", "theme"),
        "wallpapers": ("org.gnome.desktop.background", "picture-uri"),
    },
}


FALLBACK = {
    "icons": "Adwaita",
    "cursors": "Adwaita",
    "gtk": "Adwaita",
    "wm": "Adwaita",
    "desktop": "",
}
XFCE_KEYS = {
    "gtk": ("xsettings", "/Net/ThemeName"),
    "icons": ("xsettings", "/Net/IconThemeName"),
    "cursors": ("xsettings", "/Gtk/CursorThemeName"),
    "xfwm": ("xfwm4", "/general/theme"),
}
_wm_cache = (None, 0.0)


# Desktop and window-manager detection


def running_wm(refresh=False):
    """Read the actual X11 window manager, never infer it from installed packages."""
    global _wm_cache
    if not refresh and time.monotonic() < _wm_cache[1]:
        return _wm_cache[0]
    name = None
    if (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        and current_desktop() == "kde"
        and kde.kwin_running()
    ):
        name = "KWin"
    if os.environ.get("DISPLAY") and os.environ.get("XDG_SESSION_TYPE") != "wayland":
        try:
            root = subprocess.run(
                ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
            wid = re.search(r"window id # (0x[0-9a-fA-F]+)", root)
            if wid:
                out = subprocess.run(
                    ["xprop", "-id", wid[1], "_NET_WM_NAME"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ).stdout
                match = re.search(r'= "([^"]*)"', out)
                name = match[1] if match else None
        except (OSError, subprocess.SubprocessError):
            pass
    _wm_cache = (name, time.monotonic() + 2)
    return name


def border_part():
    wm = (running_wm() or "").lower()
    if "xfwm4" in wm:
        return "xfwm"
    if "kwin" in wm:
        return "aurorae"
    if "muffin" in wm:
        # Cinnamon 5.4 rebased Muffin onto GTK-drawn decorations. The old
        # GSettings theme key still exists, but Metacity themes have no effect.
        version = cinnamon_version()
        return "wm" if version is not None and version < (5, 4) else None
    if any(name in wm for name in ("marco", "metacity")):
        return "wm"
    # Mutter, Compiz (whose decorator is independent), and unknown WMs
    # must not be offered classic Metacity themes as though they were supported.
    return None


def _schema_exists(schema):
    src = Gio.SettingsSchemaSource.get_default()
    return src is not None and src.lookup(schema, True) is not None


def current_desktop():
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if any(name in de.split(":") for name in ("kde", "plasma")):
        return "kde"
    if "cinnamon" in de and _schema_exists("org.cinnamon.desktop.interface"):
        return "cinnamon"
    if "mate" in de.split(":"):
        return "mate" if _schema_exists("org.mate.interface") else None
    if "xfce" in de.split(":"):
        return "xfce"
    if any(name in de.split(":") for name in ("gnome", "unity")):
        return "gnome" if _schema_exists("org.gnome.desktop.interface") else None
    return None


def _key(part):
    if part == "wm":
        if border_part() != "wm":
            return None
        wm = (running_wm() or "").lower()
        schema = (
            "org.mate.Marco.general"
            if "marco" in wm
            else "org.cinnamon.desktop.wm.preferences"
            if "muffin" in wm
            else "org.gnome.desktop.wm.preferences"
        )
        return (schema, "theme") if _schema_exists(schema) else None
    de = current_desktop()
    if de not in KEYS:
        return None
    sk = KEYS[de].get(part)
    return sk if sk and _schema_exists(sk[0]) else None


def supported(part):
    if part == "libadwaita":
        return current_desktop() == "gnome" and libadwaita.available()
    if part == "kvantum":
        return qt.supported()
    if part == "packs":
        return any(supported(p) for p in ("gtk", "desktop", "wm", "lookandfeel", "plasma"))
    if part in ("wm", "aurorae") and border_part() == "aurorae":
        return kde.supported("aurorae")
    if current_desktop() == "kde":
        target = theme_part(part)
        if target == "aurorae" and border_part() != "aurorae":
            return False
        return kde.supported(target)
    if part == "wm" and border_part() == "xfwm":
        return supported("xfwm")
    if part == "xfwm":
        return border_part() == "xfwm" and shutil.which("xfconf-query") is not None
    if current_desktop() == "xfce" and part in (*XFCE_KEYS, "wallpapers"):
        return shutil.which("xfconf-query") is not None
    return _key(part) is not None


# Desktop settings backends


def _xfce_key(part):
    if part == "wm" and border_part() == "xfwm":
        part = "xfwm"
    if part == "xfwm" or current_desktop() == "xfce":
        return XFCE_KEYS.get(part) if supported(part) else None
    return None


def _xfconf(key, value=None):
    cmd = ["xfconf-query", "-c", key[0], "-p", key[1]]
    if value is not None:
        cmd += ["--create", "--type", "string", "--set", value]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return (r.stdout.rstrip("\n") if value is None else True) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def get(part):
    if part == "libadwaita":
        return libadwaita.get()
    if part == "kvantum":
        return qt.get()
    if part == "wallpapers" and current_desktop() == "xfce":
        return xfce.wallpaper()
    if current_desktop() == "kde" or part in ("wm", "aurorae") and border_part() == "aurorae":
        return kde.get(theme_part(part))
    xk = _xfce_key(part)
    if xk:
        return _xfconf(xk)
    sk = _key(part)
    if sk is None:
        return None
    value = Gio.Settings.new(sk[0]).get_string(sk[1])
    # The rest of drape uses wallpaper URIs; MATE stores a local filename.
    if sk == KEYS["mate"]["wallpapers"] and value:
        return Path(value).as_uri()
    return value


def set_(part, value):
    if part == "libadwaita":
        if not supported(part):
            return False
        try:
            return libadwaita.apply(value)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ApplyError(str(exc)) from exc
    if part == "kvantum":
        try:
            return qt.enable(value)
        except (OSError, ValueError, configparser.Error) as exc:
            raise ApplyError(str(exc)) from exc
    if part == "wallpapers" and current_desktop() == "xfce":
        return xfce.apply_wallpaper(value) if supported(part) else False
    if current_desktop() == "kde" or part in ("wm", "aurorae") and border_part() == "aurorae":
        return kde.apply(theme_part(part), value) if supported(part) else False
    xk = _xfce_key(part)
    if xk:
        return bool(_xfconf(xk, value))
    sk = _key(part)
    if sk is None:
        return False
    if sk == KEYS["mate"]["wallpapers"] and value.startswith("file:"):
        uri = urlsplit(value)
        if uri.netloc not in ("", "localhost"):
            return False
        value = unquote(uri.path)
    s = Gio.Settings.new(sk[0])
    if part != "wallpapers" and s.get_string(sk[1]) == value:
        # same name as before (e.g. a reinstalled theme): nothing would notice the change,
        # so switch away for a moment to make the desktop reload it
        s.set_string(sk[1], FALLBACK.get(part, "Adwaita"))
        Gio.Settings.sync()
    if not s.set_string(sk[1], value):
        return False
    if part == "wallpapers" and current_desktop() == "gnome":
        s.set_string("picture-uri-dark", value)
    Gio.Settings.sync()
    return True


def _set_default_cursor(name):
    """Keep desktop settings and non-GTK cursor clients on the same theme and size."""
    size = 24
    de = current_desktop()
    try:
        if de in ("cinnamon", "mate", "gnome"):
            schema = "org.mate.peripherals-mouse" if de == "mate" else f"org.{de}.desktop.interface"
            if _schema_exists(schema):
                size = Gio.Settings.new(schema).get_int("cursor-size")
        elif de == "xfce":
            size = int(_xfconf(("xsettings", "/Gtk/CursorThemeSize")) or 24)
        elif de == "kde":
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read(cursors.CONFIG_HOME / "kcminputrc")
            size = parser.getint("Mouse", "cursorSize", fallback=24)
        if size <= 0:
            size = 24
        cursors.apply(name, size)
    except (OSError, ValueError, configparser.Error) as exc:
        raise ApplyError(
            f"The desktop cursor was selected, but shared cursor settings could not be saved: {exc}"
        ) from exc


# Applying and validating extracted components


def apply_component(component, only=None):
    """Apply one manifest component (dict with provides/name/path). Returns the parts applied."""
    applied = []
    running_wm(refresh=True)
    for part in compatible_parts(component):
        if part == "libadwaita" and only is None:
            continue  # User CSS is applied only through an explicit native-GNOME choice.
        if only is not None and part not in {theme_part(p) for p in only}:
            continue
        value = Path(component["path"]).as_uri() if part == "wallpapers" else component["name"]
        if set_(part, value):
            applied.append(part)
            if part == "cursors":
                _set_default_cursor(value)
    return applied


def compatible_parts(component):
    """Supported parts of a fully unpacked component; mixed packs keep usable parts."""
    result = []
    path = Path(component["path"])
    if (
        "gtk" in component["provides"]
        and supported("libadwaita")
        and (path / "gtk-4.0/gtk.css").is_file()
    ):
        result.append("libadwaita")
    for part in component["provides"]:
        if (
            part == "desktop"
            and current_desktop() == "cinnamon"
            and settings.get("only_applicable")
        ):
            # Unknown version or unresolved CSS cannot satisfy a strict filter.
            if cinnamon_version() is None or _cinnamon_theme_css(path) is None:
                continue
        if part == "desktop" and cinnamon_filter_active() and cinnamon_theme_incompatible(path):
            continue
        if part == "desktop" and current_desktop() == "kde":
            continue  # this format means Cinnamon, even though the UI tab also hosts Plasma
        if part == "wm" and border_part() != "wm":
            continue
        if not supported(part):
            continue
        if part == "kvantum" and component.get("name", path.name) not in qt.theme_names(path):
            continue
        if part in kde.DIRECTORIES and not kde.compatible(part, path):
            continue
        # Our supported desktop shells use GTK 3; a GTK 2/4-only theme
        # cannot style their controls even though it is a GTK theme.
        if part == "gtk" and not (path / "gtk-3.0").is_dir():
            continue
        result.append(part)
    return result


# Catalog scope and archive compatibility


def scope(kind, only=True):
    """Catalog scope shared by GUI and CLI. Empty categories means unsupported."""
    label = (
        f"{current_desktop() or 'unsupported desktop'} / {running_wm() or 'unknown window manager'}"
    )
    if kind == "packs":
        categories = []
        for category in ("gtk", "libadwaita", "kvantum", "desktop", "wm", "lookandfeel", "colors"):
            if supported(category):
                scoped, _ = scope(category, True)
                if scoped != "":
                    from .pling import KINDS_BY_KEY

                    categories.extend((scoped or KINDS_BY_KEY[category].categories).split(","))
        return ",".join(dict.fromkeys(categories)), (
            f"Theme bundles for {label}. Shows packs with multiple usable appearance parts, "
            "and compatible KDE global themes. Each part can be chosen before applying."
        )
    if kind in ("login", "boot"):
        return None, ""
    if kind == "wm" and not only:
        return "125,138,114,717", label
    if not supported(kind):
        return ("" if only else None), f"{label}: applying {kind} themes is not supported."
    if kind == "kvantum":
        versions = ", ".join(f"Qt {major}" for major in sorted(qt.engines()))
        return (
            "123",
            f"Kvantum widget themes for {versions}. Restart Qt apps after applying; log out and back in after first enabling the engine.",
        )
    if kind == "libadwaita":
        return (
            "135",
            "GTK 4 styles for native GNOME apps. GTK 4 files are required; archive contents do not guarantee a theme was designed for your libadwaita version.",
        )
    if kind == "wm":
        return {
            "xfwm": "138",
            "wm": "125",
            "aurorae": "114,717" if kde.major_version() >= 6 else "114",
        }[border_part()], label
    if current_desktop() == "kde":
        if kind == "desktop":
            return "104", "Plasma styles from KDE-Look.org"
        if kind == "lookandfeel":
            return (
                "722" if kde.major_version() >= 6 else "121"
            ) if only else "121,722", "KDE global themes"
    return None, label


def archive_status(parts, complete, kind):
    """Contextual format checks, retaining an explicit state for unverified downloads."""
    if "gtk-4.0" in parts:
        parts = {*parts, "libadwaita"}
    if kind == "packs":
        usable = {p for p in parts if p in PACK_PARTS and supported(p)}
        if "wm" in usable and border_part() != "wm":
            usable.remove("wm")
        if "desktop" in usable and current_desktop() == "kde":
            usable.remove("desktop")
        if (
            "desktop" in usable
            and cinnamon_filter_active()
            and _cinnamon_archive_status(parts) != "compatible"
        ):
            usable.remove("desktop")
        if "gtk" in usable and "gtk-3.0" not in parts:
            usable.remove("gtk")
        return "compatible" if _is_pack(usable) else "incompatible" if complete else "unknown"
    if kind in ("login", "boot"):
        target = "login" if kind == "login" else "plymouth"
        if target in parts:
            if kind == "login":
                system_parts = parts & {"sddm", "webgreeter"}
                if system_parts and not any(system_part_compatible(p) for p in system_parts):
                    return "incompatible"
                if not system_parts:
                    return "unknown"
            return "compatible"
        return "incompatible" if complete else "unknown"
    target = theme_part(kind)
    if target == "libadwaita":
        if not supported(target):
            return "incompatible"
        return "compatible" if "gtk-4.0" in parts else "incompatible" if complete else "unknown"
    if not target or not supported(target):
        return "incompatible"
    if target not in parts:
        return "incompatible" if complete else "unknown"
    if target == "desktop" and cinnamon_filter_active():
        return _cinnamon_archive_status(parts)
    if target == "gtk" and "gtk-3.0" not in parts:
        return "incompatible" if complete and parts & {"gtk-2.0", "gtk-4.0"} else "unknown"
    return "compatible"


def archive_compatible(parts, complete, kind):
    # Unverified downloads are only visible when the user disables filtering.
    return archive_status(parts, complete, kind) == "compatible"


def download_status(checks, kind):
    """A card can offer several downloads; one usable variant keeps it available."""
    states = [archive_status(*hit, kind) if hit is not None else "unknown" for hit in checks]
    return (
        "compatible"
        if "compatible" in states
        else "unknown"
        if not states or "unknown" in states
        else "incompatible"
    )


# Login-manager and Cinnamon version rules


def system_part_compatible(part):
    from . import system

    manager = system.display_manager()
    if part == "sddm":
        return manager in (None, "sddm")
    if part == "webgreeter":
        return manager is None or (
            manager == "lightdm" and system.lightdm_greeter() in ("web-greeter", "nody-greeter")
        )
    return True


def _cinnamon_archive_status(parts):
    # The supported CSS generation follows the running release in both directions.
    if cinnamon_version() >= (5, 4):
        if "cinnamon-legacy" in parts:
            return "incompatible"
        return "compatible" if "cinnamon-modern" in parts else "unknown"
    if "cinnamon-modern-only" in parts:
        return "incompatible"
    return "compatible" if "cinnamon-pre54" in parts else "unknown"


def theme_part(kind):
    """Map a UI category to the format used by the active desktop/window manager."""
    if kind == "wm":
        return border_part() or "wm"
    if kind == "desktop" and current_desktop() == "kde":
        return "plasma"
    return kind


def category_label(kind, default):
    if kind == "desktop" and current_desktop() == "kde":
        return "Plasma style"
    if kind == "gtk":
        return "GTK applications"
    return default


def category_visible(kind, only_applicable=True):
    """Sections require an apply backend, independently of the archive filter.

    Login and boot themes use the privileged system helper, not the window manager.
    """
    return kind in ("login", "boot") or supported(kind)


def catalog_name():
    return {"kde": "KDE-Look.org", "xfce": "Xfce-Look.org"}.get(current_desktop(), "GNOME-Look.org")


# ---------------------------------------------------------------- Cinnamon theme compatibility


@functools.lru_cache(maxsize=1)
def cinnamon_version():
    try:
        out = subprocess.run(
            ["cinnamon", "--version"], capture_output=True, text=True, timeout=5
        ).stdout
        m = re.search(r"(\d+)\.(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def find_theme_dir(name):
    for base in (
        Path.home() / ".themes",
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "themes",
        Path("/usr/share/themes"),
    ):
        if (base / name).is_dir():
            return base / name
    return None


# Cinnamon 5.4 moved its dialogs (password prompts, logout, ...) from .modal-dialog to .dialog /
# .prompt-dialog. Themes that only style the old names leave those dialogs without a background.
def hide_outdated_cinnamon():
    return (
        settings.get("only_applicable")
        and cinnamon_filter_active()
        and cinnamon_version() >= (5, 4)
    )


def cinnamon_filter_active():
    # Context is independent of the visibility toggle. Showing a theme does not make
    # its unsupported components safe to apply.
    if current_desktop() != "cinnamon":
        return False
    version = cinnamon_version()
    return version is not None


def cinnamon_theme_outdated(theme_dir):
    """Detect missing modern dialog styles, including styles in imported CSS files."""
    version = cinnamon_version()
    if version is None or version < (5, 4):
        return False
    content = _cinnamon_theme_css(theme_dir)
    return content is not None and cinnamon_css_outdated(content)


def cinnamon_theme_incompatible(theme_dir):
    content = _cinnamon_theme_css(theme_dir)
    version = cinnamon_version()
    if content is None or version is None:
        return False
    if version >= (5, 4):
        return cinnamon_css_outdated(content)
    clean = re.sub(r"/\*.*?\*/", "", content, flags=re.S)
    return bool(NEW_DIALOG_RE.search(clean)) and not OLD_DIALOG_RE.search(clean)


def _cinnamon_theme_css(theme_dir):
    """Unresolved imports are unknown; absence of a selector cannot prove a mismatch."""
    css = Path(theme_dir) / "cinnamon" / "cinnamon.css"
    if not css.is_file():
        return None
    try:
        content = css.read_text(errors="replace")
        if "@import" in content:
            imports = cinnamon_css_imports(content)
            if not imports or any(
                not (css.parent / ref).is_file()
                or not (css.parent / ref).resolve().is_relative_to(css.parent.resolve())
                for ref in imports
            ):
                return None
            content += "\n" + "\n".join(
                p.read_text(errors="replace")
                for p in css.parent.rglob("*.css")
                if p != css and not p.is_symlink()
            )
    except OSError:
        return None
    return content


def cinnamon_entry_outdated(components):
    themes = [c for c in components if "desktop" in c["provides"]]
    return bool(themes) and all(cinnamon_theme_outdated(c["path"]) for c in themes)


OUTDATED_NOTE = (
    "made for an older Cinnamon: system dialogs such as password prompts and the logout "
    "dialog won't have a background"
)


# ---------------------------------------------------------------- which open windows show window borders


def _xprop(*args):
    try:
        return subprocess.run(["xprop", *args], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.lru_cache(maxsize=1)
def _app_names():
    """Window class / program name -> the app's name in the menu."""
    from gi.repository import Gio

    names = {}
    for info in Gio.AppInfo.get_all():
        if not isinstance(info, Gio.DesktopAppInfo) or info.get_nodisplay():
            continue
        name = info.get_name()
        keys = [
            info.get_startup_wm_class(),
            Path(info.get_id() or "").stem,
            Path(info.get_executable() or "").name,
        ]
        for k in keys:
            if k:
                names.setdefault(k.lower(), name)
    return names


def _app_name(res_name, res_class):
    """Readable name from the app's menu entry (e.g. nemo -> Files), else its window class."""
    names = _app_names()
    for k in (res_class, res_name, res_name.removesuffix(".bin"), res_class.split(".")[-1]):
        if k and k.lower() in names:
            return names[k.lower()]
    return res_class


def classify_window(frame_extents, motif_hints):
    """True if the window manager draws this window's title bar (so window borders apply to it)."""
    if frame_extents:
        return False  # the app draws its own (client-side decorations)
    m = re.findall(r"0x[0-9a-f]+|\d+", motif_hints or "")
    if len(m) >= 3:
        flags, decorations = int(m[0], 0), int(m[2], 0)
        if flags & 2 and decorations == 0:
            return False  # the app asked for no window manager decorations
    return True


def open_windows():
    """[(app name, shows window borders)] for open app windows, or None if this can't be told
    (not X11, or xprop missing). Only called when the user asks; reads each window's class and
    title-bar hints, nothing else, and the result isn't stored."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not _xprop("-root", "_NET_CLIENT_LIST"):
        return None
    seen = {}
    for wid in re.findall(r"0x[0-9a-f]+", _xprop("-root", "_NET_CLIENT_LIST").split("=", 1)[-1]):
        props = _xprop(
            "-id", wid, "WM_CLASS", "_NET_WM_WINDOW_TYPE", "_GTK_FRAME_EXTENTS", "_MOTIF_WM_HINTS"
        )
        wtype = re.search(r"_NET_WM_WINDOW_TYPE\(ATOM\) = (\S+)", props)
        if wtype and not wtype.group(1).rstrip(",").endswith(("NORMAL", "DIALOG")):
            continue  # desktop, panels, ...
        cls = re.search(r'WM_CLASS\(STRING\) = "([^"]*)", "([^"]*)"', props)
        if not cls:
            continue
        frame = re.search(r"_GTK_FRAME_EXTENTS\(CARDINAL\) = ", props)
        motif = re.search(r"_MOTIF_WM_HINTS\(_MOTIF_WM_HINTS\) = (.*)", props)
        name = _app_name(cls.group(1), cls.group(2))
        classic = classify_window(bool(frame), motif.group(1) if motif else "")
        seen[name] = seen.get(name, False) or classic
    return sorted(seen.items(), key=lambda kv: kv[0].lower())


PACK_PARTS = {
    "gtk",
    "libadwaita",
    "kvantum",
    "desktop",
    "wm",
    "xfwm",
    "aurorae",
    "plasma",
    "lookandfeel",
    "colors",
    "icons",
    "cursors",
    "wallpapers",
}
PACK_ANCHORS = {"gtk", "libadwaita", "kvantum", "desktop", "wm", "xfwm", "aurorae", "plasma"}


def _is_pack(parts):
    return (current_desktop() == "kde" and "lookandfeel" in parts) or (
        len(parts) >= 2 and bool(parts & PACK_ANCHORS)
    )


def pack_components(components, usable=None):
    """Qualify extracted/installed bundles using real formats and current capabilities."""
    if usable is None:
        usable = {part for component in components for part in compatible_parts(component)}
    return _is_pack(usable & PACK_PARTS)
