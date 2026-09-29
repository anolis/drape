"""Xfce wallpaper and panel settings, using the session's Xfconf service."""

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .kde import ApplyError


def query(channel, *args):
    try:
        result = subprocess.run(["xfconf-query", "-c", channel, *args],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApplyError(f"Couldn't reach Xfce settings: {exc}") from exc
    if result.returncode:
        raise ApplyError(result.stderr.strip() or result.stdout.strip() or
                         f"Couldn't change {channel} settings.")
    return result.stdout.rstrip("\n")


def properties(channel):
    return set(query(channel, "-l").splitlines())


def read(channel, key):
    return query(channel, "-p", key)


def write(channel, key, value, typ, exists=True):
    # Existing values retain their type (panel length was uint on older Xfce).
    args = ["-p", key, "-s", str(value).lower() if isinstance(value, bool) else str(value)]
    if not exists:
        args += ["-n", "-t", typ]
    query(channel, *args)


def change(channel, values):
    """Apply scalar settings with rollback; return the previous values for Undo."""
    present = properties(channel)
    before = {key: (read(channel, key) if key in present else None, typ)
              for key, (_, typ) in values.items()}
    applied = []
    try:
        for key, (value, typ) in values.items():
            write(channel, key, value, typ, key in present)
            applied.append(key)
    except ApplyError as exc:
        failed = []
        for key in reversed(applied):
            try:
                old, typ = before[key]
                if old is None:
                    query(channel, "-p", key, "-r")
                else:
                    write(channel, key, old, typ)
            except ApplyError:
                failed.append(key)
        if failed:
            raise ApplyError(f"{exc}\nCouldn't restore: {', '.join(failed)}") from exc
        raise
    return before


def restore(channel, before):
    # Reset properties that were absent before the change instead of inventing defaults.
    present = properties(channel)
    for key, (value, typ) in before.items():
        if value is None:
            if key in present:
                query(channel, "-p", key, "-r")
        else:
            write(channel, key, value, typ, key in present)


def wallpaper_targets(present):
    targets = {}
    for key in sorted(present):
        match = re.fullmatch(r"(/backdrop/screen\d+/monitor[^/]+/workspace\d+)/[^/]+", key)
        if match:
            base = match[1]
            targets[base] = base + "/last-image"
    return targets


def live_wallpaper_targets(present):
    """Xfdesktop 4.12+ uses connector/workspace keys, even before they are saved."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return {}  # Xwayland outputs do not describe the compositor's monitors.
    try:
        outputs = subprocess.run(["xrandr", "--query"], capture_output=True, text=True,
                                 timeout=5, check=True).stdout
        desktops = subprocess.run(["xprop", "-root", "_NET_NUMBER_OF_DESKTOPS"],
                                  capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    screen = re.search(r"^Screen (\d+):", outputs, re.M)
    count = re.search(r"=\s*(\d+)", desktops)
    if not screen or not count or not 1 <= int(count[1]) <= 256:
        return {}
    # Connected but disabled outputs have no geometry and need no wallpaper.
    monitors = re.findall(r"^(\S+) connected (?:primary )?\d+x\d+[+-]\d+[+-]\d+", outputs, re.M)
    workspaces = set(range(int(count[1])))
    single = "/backdrop/single-workspace-number"
    if single in present:
        value = read("xfce4-desktop", single)
        if value.isdigit() and int(value) < 256:
            workspaces.add(int(value))
    return {base: base + "/last-image" for monitor in monitors if "/" not in monitor
            for workspace in sorted(workspaces)
            for base in [f"/backdrop/screen{screen[1]}/monitor{monitor}/workspace{workspace}"]}


def wallpaper():
    try:
        present = properties("xfce4-desktop")
        targets = live_wallpaper_targets(present) or wallpaper_targets(present)
        for key in targets.values():
            if key not in present:
                continue
            path = Path(read("xfce4-desktop", key))
            if path.is_absolute():
                return path.as_uri()
    except ApplyError:
        pass
    return None


def apply_wallpaper(value):
    uri = urlsplit(value)
    if uri.scheme == "file" and uri.netloc in ("", "localhost"):
        path = Path(unquote(uri.path))
    elif not uri.scheme:
        path = Path(value)
    else:
        raise ApplyError("Xfce wallpaper must be a local image.")
    if not path.is_absolute() or not path.is_file():
        raise ApplyError("The wallpaper image no longer exists.")
    channel = "xfce4-desktop"
    present = properties(channel)
    targets = wallpaper_targets(present)
    targets.update(live_wallpaper_targets(present))
    if not targets:
        raise ApplyError("Couldn't identify Xfce monitor/workspace settings. Open Xfce Desktop Settings "
                         "and choose a background once, then try again.")
    values = {}
    for base, key in targets.items():
        values[key] = (str(path), "string")
        style = base + "/image-style"
        if style not in present or read(channel, style) == "0":
            values[style] = (5, "int")  # zoom; retain any other chosen placement
        values[base + "/backdrop-cycle-enable"] = (False, "bool")
    change(channel, values)
    return True


PANEL_CHANNEL = "xfce4-panel"
PANEL_FIELDS = {
    "size": ("uint", 48), "length": ("double", 10),
    "autohide-behavior": ("uint", 0), "position-locked": ("bool", False),
    "background-style": ("uint", 0),
}
# Xfce SnapPosition: NC=9, SC=10, WC=7. Keep the existing monitor coordinates.
PRESETS = {
    "Bottom taskbar": {"position": 10, "mode": 0, "size": 32, "length": 100, "autohide-behavior": 0},
    "Top bar": {"position": 9, "mode": 0, "size": 28, "length": 100, "autohide-behavior": 0},
    "Bottom dock": {"position": 10, "mode": 0, "size": 48, "length": 35, "autohide-behavior": 1},
    "Left bar": {"position": 7, "mode": 1, "size": 40, "length": 100, "autohide-behavior": 0},
}


def panels():
    # /panels is the active ID array; old panel-N properties may remain after removal.
    text = read(PANEL_CHANNEL, "/panels")
    return sorted({int(line.strip()) for line in text.splitlines() if line.strip().isdigit()})


def panel_settings(panel):
    present = properties(PANEL_CHANNEL)
    base = f"/panels/panel-{panel}/"
    result = {}
    for key, (typ, default) in PANEL_FIELDS.items():
        value = read(PANEL_CHANNEL, base + key) if base + key in present else default
        result[key] = str(value).lower() == "true" if typ == "bool" else float(value)
    return result


def apply_panel(panel, values, preset=None):
    if panel not in panels():
        raise ApplyError("This panel no longer exists. Refresh the panel list.")
    base = f"/panels/panel-{panel}/"
    limits = {"size": (16, 128), "length": (1, 100), "autohide-behavior": (0, 2),
              "background-style": (0, 2)}
    for key, value in values.items():
        if key not in PANEL_FIELDS or (key in limits and not limits[key][0] <= value <= limits[key][1]):
            raise ApplyError(f"Invalid panel setting: {key}")
    changes = {base + key: (value, PANEL_FIELDS[key][0]) for key, value in values.items()}
    if preset:
        if preset not in PRESETS:
            raise ApplyError("Unknown panel preset.")
        position = read(PANEL_CHANNEL, base + "position")
        if not re.fullmatch(r"p=\d+;x=\d+;y=\d+", position):
            raise ApplyError("Open Panel Preferences and position this panel before using a preset.")
        for key, value in PRESETS[preset].items():
            if key == "position":
                value = re.sub(r"^p=\d+", f"p={value}", position)
            typ = "string" if key == "position" else "double" if key == "length" else "uint"
            changes[base + key] = (value, typ)
        changes[base + "length-adjust"] = (True, "bool")
        changes[base + "position-locked"] = (True, "bool")
        changes[base + "nrows"] = (1, "uint")
    return change(PANEL_CHANNEL, changes)
