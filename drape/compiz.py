"""Window managers and Compiz.

Cinnamon is its own window manager (Muffin runs inside it), so Compiz can't replace it there. On
Cinnamon, drape installs a small MATE + Compiz session the user picks at the login screen, leaving
Cinnamon untouched. On MATE (and Xfce), Compiz can be switched on directly.

ccsm (CompizConfig Settings Manager) is Python + GTK 3 like drape, so its pages are hosted inside
drape instead of running as a separate app.
"""

import importlib.util
import os
import re
import shutil
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

# what "Install Compiz" installs (all whitelisted in the root helper)
COMPIZ_PACKAGES = ["compiz", "compiz-mate", "compizconfig-settings-manager", "compiz-plugins",
                   "compiz-plugins-extra", "emerald"]
# a minimal MATE session for Compiz to run in, for desktops (like Cinnamon) that can't host it
MATE_SESSION_PACKAGES = ["mate-session-manager", "mate-panel", "mate-settings-daemon", "caja", "marco"]

WINDOW_MANAGERS = {
    # key: (label, command to take over live)
    "compiz": ("Compiz", ["compiz", "--replace"]),
    "marco": ("Marco (MATE's own)", ["marco", "--replace"]),
    "xfwm4": ("Xfwm (Xfce's own)", ["xfwm4", "--replace"]),
}


def session():
    """'cinnamon', 'mate', 'xfce' or another lowercase name from XDG_CURRENT_DESKTOP."""
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    for name in ("cinnamon", "mate", "xfce"):
        if name in de:
            return name
    return de.split(":")[0] or "unknown"


def running_wm():
    """Name the running window manager reports about itself (e.g. 'Mutter (Muffin)', 'compiz')."""
    try:
        root = subprocess.run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], capture_output=True,
                              text=True, timeout=3).stdout
        wid = re.search(r"window id # (0x[0-9a-f]+)", root)
        if not wid:
            return None
        out = subprocess.run(["xprop", "-id", wid.group(1), "_NET_WM_NAME"], capture_output=True,
                             text=True, timeout=3).stdout
        name = re.search(r'= "([^"]*)"', out)
        return name.group(1) if name else None
    except (OSError, subprocess.SubprocessError):
        return None


def installed(program):
    return shutil.which(program) is not None


def compiz_installed():
    return installed("compiz")


def mate_session_installed():
    return os.path.exists("/usr/share/xsessions/mate.desktop") and installed("mate-session")


def ccsm_available():
    """ccsm's Python pieces are on disk (python3-compizconfig + compizconfig-settings-manager). Checks the
    files themselves, so a removal is noticed even after ccsm was loaded into drape."""
    importlib.invalidate_caches()
    for name in ("compizconfig", "ccm"):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            return False
        if spec is None or not spec.origin or not os.path.exists(spec.origin):
            return False
    return True


DPKG_LOCKS = ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock")
PACKAGE_TOOLS = {"dpkg", "apt", "apt-get", "aptitude", "unattended-upgr"}


def package_manager_busy():
    """True while software is being installed or removed. apt's lock files aren't readable by users,
    but the kernel's list of held locks is, and so are process names."""
    inodes = set()
    for path in DPKG_LOCKS:
        try:
            inodes.add(str(os.stat(path).st_ino))
        except OSError:
            pass
    try:
        with open("/proc/locks") as f:
            for line in f:
                fields = line.split()
                if len(fields) > 5 and fields[5].rsplit(":", 1)[-1] in inodes:
                    return True
    except OSError:
        pass
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm") as f:
                    if f.read().strip() in PACKAGE_TOOLS:
                        return True
            except OSError:
                continue
    return False


def install_state():
    """What's installed right now; the Window manager page redraws when this changes."""
    return (compiz_installed(), mate_session_installed(), ccsm_available())


def packages_to_install():
    pkgs = list(COMPIZ_PACKAGES)
    if session() not in ("mate", "xfce"):
        pkgs += MATE_SESSION_PACKAGES
    return pkgs


def use_compiz_in_mate(enabled=True):
    """Make the MATE session start Compiz (or its own Marco). Per user; no root needed. Uses the
    gsettings tool so a schema installed after drape started is still found."""
    wm = "compiz" if enabled else "marco"
    try:
        r = subprocess.run(["gsettings", "set", "org.mate.session.required-components", "windowmanager", wm],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mate_wm_setting():
    try:
        out = subprocess.run(["gsettings", "get", "org.mate.session.required-components", "windowmanager"],
                             capture_output=True, text=True, timeout=10).stdout.strip().strip("'")
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def switch_live(wm):
    """Replace the running window manager right now (MATE / Xfce sessions)."""
    label, cmd = WINDOW_MANAGERS[wm]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------- ccsm, hosted inside drape

# ccsm's pages call back into "the window they're in" (widget.get_toplevel()) with these names;
# drape's window forwards them to the hosted ccsm.
FORWARDED = ("SetPage", "BackToMain", "RefreshPage", "MainPage", "CurrentPage", "ShowingPlugin", "Context")


def make_ccsm():
    """ccsm's whole interface as a widget for a drape page, or raise ImportError if it isn't installed."""
    import compizconfig
    import ccm
    from ccm.Pages import MainPage
    from ccm.Utils import GlobalUpdater

    try:
        context = compizconfig.Context(ccm.GetDefaultScreenNum())
    except (AttributeError, TypeError):
        context = compizconfig.Context(ccm.GetScreenNums())  # Compiz 0.8
    GlobalUpdater.SetContext(context)

    class HostedCcsm(Gtk.Box):
        """The body of ccm.MainWin, in a box instead of its own window."""
        SetPage = ccm.MainWin.SetPage
        BackToMain = ccm.MainWin.BackToMain
        RefreshPage = ccm.MainWin.RefreshPage

        def __init__(self):
            super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
            self.ShowingPlugin = None
            self.Context = context
            self.MainBox = self
            self.LeftPane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.RightPane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=5)
            self.pack_start(self.LeftPane, False, False, 0)
            self.pack_end(self.RightPane, True, True, 0)
            self.MainPage = MainPage(self, context)
            self.CurrentPage = None
            self.SetPage(self.MainPage)
            self.idle = ccm.IdleSettingsParser(context, self)
            self.hide_close_button()

        def set_title(self, *_):  # ccsm sometimes retitles its window; drape keeps its own title
            pass

        def Quit(self, *_):  # ccsm's Close button; there's nothing to close inside drape
            pass

        def hide_close_button(self):
            def walk(w):
                if isinstance(w, Gtk.Button) and (w.get_label() or "").replace("_", "") == "Close":
                    w.set_no_show_all(True)
                    w.hide()
                elif isinstance(w, Gtk.Container):
                    for c in w.get_children():
                        walk(c)
            walk(self.MainPage.LeftWidget)

    return HostedCcsm()
