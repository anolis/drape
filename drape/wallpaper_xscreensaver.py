"""Discover installed XScreenSaver animations without executing desktop-file commands."""

import os
import re
import shlex
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import wallpaper_animation_settings as settings
from .video_wallpapers import VideoError

CONFIG_DIRS = (Path("/usr/share/xscreensaver/config"), Path("/usr/local/share/xscreensaver/config"))
BIN_DIRS = tuple(
    Path(p)
    for p in (
        "/usr/libexec/xscreensaver",
        "/usr/lib/xscreensaver",
        "/usr/lib64/xscreensaver",
        "/usr/lib/x86_64-linux-gnu/xscreensaver",
        "/usr/local/libexec/xscreensaver",
        "/usr/local/lib/xscreensaver",
    )
)
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}")


def catalog():
    """Require both packaged metadata and an executable in a known animation directory."""
    entries = {}
    for directory in CONFIG_DIRS:
        for path in sorted(directory.glob("*.xml"))[:512]:
            try:
                if path.stat().st_size > 128 * 1024:
                    continue
                root = ET.fromstring(path.read_bytes())
                name = root.get("name", "")
                if root.tag != "screensaver" or not NAME.fullmatch(name) or name in entries:
                    continue
                executable = next(
                    (
                        d / name
                        for d in BIN_DIRS
                        if (d / name).is_file() and os.access(d / name, os.X_OK)
                    ),
                    None,
                )
                if executable is None:
                    continue
                delay = next(
                    (
                        node.get("arg", "").split()[0]
                        for node in root.iter("number")
                        if node.get("id") == "delay"
                        and node.get("arg") in {"-delay %", "--delay %"}
                    ),
                    "",
                )
                description = " ".join(
                    root.findtext("_description", root.findtext("description", "")).split()
                )
                entries[name] = {
                    "name": name,
                    "label": root.get("_label", root.get("label", name))[:120],
                    "description": description[:2000],
                    "executable": str(executable),
                    "delay": delay,
                    "settings": settings.schema(root),
                }
            except (OSError, ET.ParseError, ValueError):
                continue  # A broken optional animation must not hide the usable ones.
    return sorted(entries.values(), key=lambda entry: entry["label"].casefold())


def validate(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise VideoError("Choose an installed XScreenSaver animation.")
    entry = next((entry for entry in catalog() if entry["name"] == name), None)
    if entry is None:
        raise VideoError("This XScreenSaver animation is no longer installed. Refresh the list.")
    return entry


def command(entry, xid, fps=30, values=None):
    if type(fps) is not int or fps not in {15, 30, 60}:
        raise VideoError("Choose a 15, 30 or 60 FPS animation target.")
    # Never run --root: only Drape's own drawing area may be painted on.
    args = [entry["executable"], "--window-id", str(xid)]
    if entry["delay"]:
        args.extend([entry["delay"], str(round(1_000_000 / fps))])
    args.extend(settings.arguments(entry.get("settings", []), values or {}))
    return args


def environment(entry=None):
    """Let hacks find their packaged image/text helpers without changing the session."""
    env = os.environ.copy()
    directories = [str(path) for path in BIN_DIRS if path.is_dir()]
    if entry and entry["name"] == "glitchpeg":
        from .video_wallpapers import runtime_dir

        helper = next(
            (
                path / "xscreensaver-getimage-file"
                for path in BIN_DIRS
                if (path / "xscreensaver-getimage-file").is_file()
                and os.access(path / "xscreensaver-getimage-file", os.X_OK)
            ),
            None,
        )
        if helper:
            directory = runtime_dir() / "animation-helpers"
            directory.mkdir(mode=0o700, exist_ok=True)
            launcher = directory / "xscreensaver-getimage-file"
            adapter = Path(__file__).with_name("wallpaper_image_helper.py")
            content = (
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(adapter))} "$@"\n'
            )
            if not launcher.is_file() or launcher.read_text() != content:
                with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(content)
                    os.fchmod(output.fileno(), 0o700)
                try:
                    temporary.replace(launcher)
                finally:
                    temporary.unlink(missing_ok=True)
            directories.insert(0, str(directory))
            env["DRAPE_IMAGE_HELPER"] = str(helper)
            env["DRAPE_IMAGE_CACHE"] = str(directory / "jpeg")
    # Hacks invoke helper basenames themselves. Starting a hack by absolute
    # path does not make its libexec directory available to those children.
    env["PATH"] = os.pathsep.join([*directories, env.get("PATH", os.defpath)])
    return env
