"""Opt-in real Xfconf/GTK check: python3 -m tests.xfce_integration.

Needs dbus-run-session, xfconf-query and broadwayd. No real desktop is modified:
the temporary XDG environment is set BEFORE starting the D-Bus activation service.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def session_check():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib
    from drape import xfce
    from drape.xfcepage import XfcePanelPage

    channel = xfce.PANEL_CHANNEL
    assert xfce.properties(channel) == set(), "Test settings must start empty"
    xfce.query(channel, "-p", "/panels", "-n", "-a", "-t", "int", "-s", "1")
    xfce.write(channel, "/panels/panel-1/position", "p=6;x=2200;y=300", "string", False)
    xfce.write(channel, "/panels/panel-1/length", 75, "uint", False)
    before = xfce.apply_panel(1, {"background-style": 0}, "Bottom dock")
    assert xfce.read(channel, "/panels/panel-1/length") == "35"
    assert xfce.read(channel, "/panels/panel-1/position") == "p=10;x=2200;y=300"
    xfce.restore(channel, before)
    assert xfce.read(channel, "/panels/panel-1/length") == "75"
    assert "/panels/panel-1/background-style" not in xfce.properties(channel)
    base = "/backdrop/screen0/monitorDP-2/workspace1"
    xfce.write("xfce4-desktop", base + "/last-image", "/old.png", "string", False)
    picture = Path(os.environ["XDG_CONFIG_HOME"]) / "night ü sky.png"
    picture.touch()
    xfce.apply_wallpaper(picture.as_uri())
    assert xfce.wallpaper() == picture.as_uri()
    assert xfce.read("xfce4-desktop", base + "/image-style") == "5"
    assert xfce.read("xfce4-desktop", base + "/backdrop-cycle-enable") == "false"

    page = XfcePanelPage(None)
    window = Gtk.Window()
    window.add(page)
    window.show_all()

    def settle():
        end = time.monotonic() + 10
        while page.busy and time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(0.01)
        assert not page.busy, "UI timed out"
        assert not page.status.get_text(), page.status.get_text()

    page.load()
    settle()
    assert page.selector.get_active_id() == "1"
    assert page.controls.get_sensitive()
    page.preset.set_active(4)
    page._apply()
    settle()
    assert xfce.read(channel, "/panels/panel-1/mode") == "1"
    assert page.undo_button.get_sensitive()
    page._undo()
    settle()
    assert xfce.read(channel, "/panels/panel-1/position") == "p=6;x=2200;y=300"
    assert not page.undo_button.get_sensitive()
    window.destroy()
    print("Isolated Xfconf + GTK: wallpaper, preset, legacy length type, panel controls and Undo passed.")


def main():
    if "--session" in sys.argv:
        with subprocess.Popen(["broadwayd", "--address=127.0.0.1", "--port=0", ":83"],
                              stdout=subprocess.DEVNULL) as display:
            try:
                time.sleep(0.3)
                session_check()
            finally:
                display.terminate()
        return
    # Include the files touched by the check in a before/after isolation assertion.
    real_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    files = [real_config / "xfce4/xfconf/xfce-perchannel-xml" / f"{channel}.xml"
             for channel in ("xfce4-panel", "xfce4-desktop")]
    original = {path: path.read_bytes() if path.exists() else None for path in files}
    with tempfile.TemporaryDirectory(prefix="drape-xfce-integration-") as temp:
        env = dict(os.environ, XDG_CONFIG_HOME=temp + "/config", XDG_CONFIG_DIRS=temp + "/defaults",
                   XDG_CACHE_HOME=temp + "/cache", XDG_RUNTIME_DIR=temp,
                   XDG_CURRENT_DESKTOP="XFCE", GDK_BACKEND="broadway", BROADWAY_DISPLAY=":83",
                   GIO_USE_VFS="local", NO_AT_BRIDGE="1", DISPLAY="")
        for name in ("config", "defaults", "cache"):
            (Path(temp) / name).mkdir()
        result = subprocess.run(["dbus-run-session", "--", sys.executable, "-m",
                                 "tests.xfce_integration", "--session"], env=env)
    for path, data in original.items():
        assert (path.read_bytes() if path.exists() else None) == data, f"Real config changed: {path}"
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
