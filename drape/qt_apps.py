"""Explicit integration for apps whose own appearance can override Qt styles."""

import configparser
import io
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .records import file_lock
from .session import atomic_text

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
PROC = Path("/proc")


def opensnitch_theme():
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read(CONFIG_HOME / "opensnitch/settings.conf")
        return parser.get("global", "theme", fallback="")
    except (OSError, UnicodeError, configparser.Error):
        return ""


def opensnitch_system_theme():
    """Clear only the appearance override; leave firewall and connection settings intact."""
    path = CONFIG_HOME / "opensnitch/settings.conf"
    with file_lock(path.with_suffix(".drape-lock")):
        if not path.exists():
            return
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(path)
        if not parser.has_section("global"):
            return
        parser.set("global", "theme", "")
        text = io.StringIO()
        parser.write(text, space_around_delimiters=False)
        atomic_text(path, text.getvalue())


def opensnitch_processes():
    """Match the current user's GUI executable, never the firewall daemon."""
    found = []
    for process in PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            argv = [
                os.fsdecode(arg) for arg in (process / "cmdline").read_bytes().split(b"\0") if arg
            ]
            if any(Path(arg).name == "opensnitch-ui" for arg in argv[:2]):
                found.append((int(process.name), argv))
        except (OSError, ValueError):
            continue
    return found


def check_opensnitch_engine(found=None):
    """Check the running GUI's Qt major before changing its appearance preference."""
    if found is None:
        found = opensnitch_processes()
    from . import qt

    for pid, _ in found:
        try:
            maps = (PROC / str(pid) / "maps").read_text()
        except OSError:
            continue
        for major in (5, 6):
            if f"libQt{major}Core" in maps and major not in qt.engines():
                raise ValueError(f"OpenSnitch uses Qt {major}. Install its Kvantum engine first.")


def restart_opensnitch():
    """Relaunch only the GUI, retaining its arguments and passing the Qt engine explicitly."""
    program = shutil.which("opensnitch-ui")
    if program is None:
        raise ValueError("OpenSnitch's GUI is not installed.")
    found = opensnitch_processes()
    if len(found) > 1:
        raise ValueError("Close the extra OpenSnitch GUIs before restarting from Drape.")
    argv = found[0][1] if found else [program]
    check_opensnitch_engine(found)
    for pid, _ in found:
        # A pidfd prevents a recycled process ID from receiving this signal.
        try:
            handle = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            signal.pidfd_send_signal(handle, signal.SIGTERM)
        finally:
            os.close(handle)
    deadline = time.monotonic() + 5
    while opensnitch_processes():
        if time.monotonic() >= deadline:
            raise ValueError("OpenSnitch did not close. Quit its GUI and try again.")
        time.sleep(0.1)
    process = subprocess.Popen(
        argv,
        env=dict(os.environ, QT_STYLE_OVERRIDE="kvantum"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.5)
    if process.poll() is not None:
        raise ValueError(
            "OpenSnitch's GUI exited during startup. Launch opensnitch-ui from a terminal to see its error output."
        )
    return process
