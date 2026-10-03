"""Cursor settings for Xcursor clients and sessions outside GTK/Xfconf."""

import configparser
import io
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from .records import file_lock
from .session import atomic_text

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
RESTART_NOTE = "Restart apps such as Kitty if they keep the old cursor. Log out and back in once for apps that read cursor settings from their launch environment."


def apply(name, size):
    """Publish the same theme through default inheritance, X resources and login env.

    Xcursor-based clients may ignore the desktop's live settings. X11 resources
    help newly opened clients immediately; environment exports also cover Wayland.
    Existing clients can retain cursors allocated before the theme changed.
    """
    if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("Invalid cursor theme name")
    size = max(1, min(int(size), 512))
    index = HOME / ".icons/default/index.theme"
    resources = HOME / ".Xresources"
    environment = CONFIG_HOME / "environment.d/90-drape-cursor.conf"
    profile = HOME / ".profile"
    resource_lines = f"Xcursor.theme: {name}\nXcursor.size: {size}\n"
    with file_lock(CONFIG_HOME / "drape/session.lock"):
        before = {
            path: path.read_text() if path.exists() else None
            for path in (index, resources, environment, profile)
        }
        try:
            # Preserve metadata and unrelated sections from an existing default theme.
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            parser.read_string(before[index] or "[Icon Theme]\nName=Default\n")
            if not parser.has_section("Icon Theme"):
                parser.add_section("Icon Theme")
            parser.set("Icon Theme", "Inherits", name)
            text = io.StringIO()
            parser.write(text, space_around_delimiters=False)
            atomic_text(index, text.getvalue())
            # Replace only cursor resources; xrdb -merge preserves live Xft settings.
            existing = re.sub(
                r"(?m)^\s*Xcursor[.*](theme|size)\s*:.*(?:\n|$)", "", before[resources] or ""
            )
            atomic_text(resources, existing.rstrip("\n") + "\n" + resource_lines)
            quoted = name.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
            atomic_text(
                environment,
                f'# Cursor theme selected by Drape\nXCURSOR_THEME="{quoted}"\nXCURSOR_SIZE={size}\n',
            )
            existing = re.sub(
                r"(?m)^# BEGIN DRAPE CURSOR\n.*?^# END DRAPE CURSOR\n?",
                "",
                before[profile] or "",
                flags=re.DOTALL,
            )
            atomic_text(
                profile,
                existing.rstrip("\n")
                + f"\n\n# BEGIN DRAPE CURSOR\nexport XCURSOR_THEME={shlex.quote(name)}\nexport XCURSOR_SIZE={size}\n# END DRAPE CURSOR\n",
            )
        except BaseException:
            for path, content in before.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_text(path, content)
            raise
    os.environ.update(XCURSOR_THEME=name, XCURSOR_SIZE=str(size))
    _publish(resource_lines, name, size)


def _publish(resources, name, size):
    """Best-effort live publication; persisted settings remain usable without these tools."""
    commands = []
    if os.environ.get("DISPLAY") and shutil.which("xrdb"):
        commands.append((["xrdb", "-merge"], resources))
    if shutil.which("dbus-update-activation-environment"):
        commands.append(
            (
                [
                    "dbus-update-activation-environment",
                    "--systemd",
                    "XCURSOR_THEME=" + name,
                    f"XCURSOR_SIZE={size}",
                ],
                None,
            )
        )
    for command, text in commands:
        try:
            subprocess.run(
                command, input=text, text=True, capture_output=True, timeout=3, check=False
            )
        except (OSError, subprocess.SubprocessError):
            pass
