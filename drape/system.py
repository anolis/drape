"""What the system has (login manager, greeters, Plymouth) and running the root helper."""

import configparser
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HELPER = Path(__file__).resolve().with_name("helper.py")
XGREETERS = Path("/usr/share/xgreeters")
PLYMOUTH_DIR = Path("/usr/share/plymouth/themes")
WEB_GREETERS = ("nody-greeter", "web-greeter", "lightdm-webkit2-greeter")
GTK_GREETERS = ("slick-greeter", "lightdm-gtk-greeter")


def which(name):
    return shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin")


def _ini(paths, section, key):
    """Last value of section/key across config files, like LightDM/SDDM layering."""
    value = None
    for p in paths:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(p)
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        if cp.has_option(section, key):
            value = cp.get(section, key).strip()
    return value


def display_manager():
    try:
        return Path(Path("/etc/X11/default-display-manager").read_text().strip()).name
    except OSError:
        link = Path("/etc/systemd/system/display-manager.service")
        return link.resolve().stem if link.is_symlink() else None


def lightdm_greeter():
    confs = sorted(Path("/usr/share/lightdm/lightdm.conf.d").glob("*.conf")) + \
        sorted(Path("/etc/lightdm/lightdm.conf.d").glob("*.conf")) + [Path("/etc/lightdm/lightdm.conf")]
    session = _ini(confs, "Seat:*", "greeter-session") or _ini(confs, "SeatDefaults", "greeter-session")
    if session == "lightdm-greeter":
        # Debian's alternatives-managed default
        link = XGREETERS / "lightdm-greeter.desktop"
        session = link.resolve().stem if link.exists() else session
    return session


def installed_greeters():
    return {p.stem for p in XGREETERS.glob("*.desktop")} if XGREETERS.is_dir() else set()


def apt_available(package):
    if not which("apt-cache"):
        return False
    r = subprocess.run(["apt-cache", "policy", package], capture_output=True, text=True)
    m = re.search(r"Candidate:\s*(\S+)", r.stdout)
    return bool(m and m.group(1) != "(none)")


def plymouth_installed():
    return bool(which("plymouthd") or which("plymouth-set-default-theme"))


def current_plymouth():
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read("/etc/plymouth/plymouthd.conf")
        name = cp.get("Daemon", "Theme", fallback="").strip()
    except (configparser.Error, OSError):
        name = ""
    default = PLYMOUTH_DIR / "default.plymouth"
    if default.exists():
        name = default.resolve().parent.name  # alternatives win on Debian
    return name or None


def current_sddm_theme():
    confs = [Path("/usr/lib/sddm/sddm.conf.d/default.conf")] + \
        sorted(Path("/usr/lib/sddm/sddm.conf.d").glob("*.conf")) + [Path("/etc/sddm.conf")] + \
        sorted(Path("/etc/sddm.conf.d").glob("*.conf"))
    return _ini(confs, "Theme", "Current")


def current_web_greeter_theme():
    try:
        m = re.search(r"^\s+theme:\s*(\S+)", Path("/etc/lightdm/web-greeter.yml").read_text(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def greeter_settings(greeter):
    """Current background/theme/icons/cursor of slick-greeter or lightdm-gtk-greeter."""
    path, section = {"slick-greeter": ("/etc/lightdm/slick-greeter.conf", "Greeter"),
                     "lightdm-gtk-greeter": ("/etc/lightdm/lightdm-gtk-greeter.conf", "greeter")}[greeter]
    keys = ("background", "theme-name", "icon-theme-name", "cursor-theme-name")
    return {k: _ini([Path(path)], section, k) for k in keys}


# ---------------------------------------------------------------- requirements per theme type

@dataclass
class Requirement:
    label: str               # what it needs, in words
    installed: bool          # the software is present
    active: bool             # ...and it's what the system actually uses
    package: str = None      # apt package that provides it, if installable
    activate: list = None    # helper command that makes it the active one
    url: str = None          # where to get it when there's no package
    note: str = ""           # what switching means


def requirement(kind):
    dm = display_manager()
    if kind == "plymouth":
        ok = plymouth_installed()
        return Requirement("Plymouth (the boot splash system)", ok, ok,
                           package="plymouth" if apt_available("plymouth") else None,
                           url="https://www.freedesktop.org/wiki/Software/Plymouth/")
    if kind == "sddm":
        ok = which("sddm") is not None
        return Requirement("the SDDM login manager", ok, ok and dm == "sddm",
                           package="sddm" if apt_available("sddm") else None,
                           activate=["display-manager", "sddm"], url="https://github.com/sddm/sddm",
                           note="Your login screen will switch from "
                                f"{dm or 'the current login manager'} to SDDM after a restart. "
                                "You can switch back from drape's Lock & login page.")
    if kind == "webgreeter":
        have = [g for g in WEB_GREETERS if g in installed_greeters()]
        return Requirement("a web-based LightDM greeter (nody-greeter or web-greeter)", bool(have),
                           bool(have) and dm == "lightdm" and lightdm_greeter() in have,
                           activate=["use-greeter", have[0]] if have else None,
                           url="https://github.com/JezerM/nody-greeter",
                           note="LightDM will use the web greeter for your login screen from the next login.")
    if kind == "gdm":
        return Requirement("GDM", which("gdm3") is not None, dm in ("gdm3", "gdm"),
                           url="https://wiki.gnome.org/Projects/GDM")
    raise KeyError(kind)


def login_categories(filtered):
    """gnome-look categories for the Login screen tab, optionally only ones this system can use."""
    cats = {"sddm": "101", "webgreeter": "154", "gdm": "131"}
    if not filtered:
        return ",".join(cats.values())
    usable = [c for k, c in cats.items() if k != "gdm" and requirement(k).installed]
    return ",".join(usable)


# ---------------------------------------------------------------- running the helper

@dataclass
class Result:
    ok: bool
    output: str
    cancelled: bool = False


def run_helper(*commands, on_progress=None):
    """Run one or more helper commands as root with a single password prompt. Blocking.
    on_progress(percent, text) is called (from this thread) as long operations report progress."""
    pkexec = which("pkexec")
    if pkexec is None:
        return Result(False, "pkexec was not found. Install polkit (the policykit-1 package).")
    args = []
    for i, cmd in enumerate(commands):
        if i:
            args.append(";;")
        args += [str(a) for a in cmd]
    proc = subprocess.Popen([pkexec, sys.executable or "/usr/bin/python3", str(HELPER), "batch", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for line in proc.stdout:
        if line.startswith("PROGRESS "):
            _, pct, *text = line.rstrip("\n").split(" ", 2)
            if on_progress:
                try:
                    on_progress(float(pct), text[0] if text else "")
                except ValueError:
                    pass
        else:
            lines.append(line)
    code = proc.wait()
    output = "".join(lines).strip()
    cancelled = code in (126, 127)
    if cancelled and not output:
        output = "Authentication was cancelled."
    return Result(code == 0, output, cancelled)


def staging_dir():
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "drape" / "system"
