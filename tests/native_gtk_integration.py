"""Opt-in GTK 4/libadwaita render check: python3 -m tests.native_gtk_integration."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from drape import gtk_resources

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    theme = root / "theme"
    theme.mkdir()
    (theme / "payload.css").write_text("window { color: rgb(17, 34, 51); }")
    css = theme / "gtk.css"
    css.write_text('@import url("resource:///org/gnome/theme/gtk.css");')
    manifest = theme / "bundle.xml"
    manifest.write_text(
        '<gresources><gresource prefix="/org/gnome/theme"><file alias="gtk.css">payload.css</file></gresource></gresources>'
    )
    subprocess.run(
        [
            "glib-compile-resources",
            str(manifest),
            "--sourcedir",
            str(theme),
            "--target",
            str(theme / "gtk.gresource"),
        ],
        check=True,
        capture_output=True,
    )
    css = gtk_resources.prepare(css, root)
    user = root / "gtk-4.0/gtk.css"
    user.parent.mkdir()
    user.write_text(f'@import url("{css.as_uri()}");\n')
    environment = dict(
        os.environ,
        XDG_RUNTIME_DIR=directory,
        XDG_CONFIG_HOME=directory,
        XDG_DATA_HOME=directory,
        XDG_CACHE_HOME=directory,
        GDK_BACKEND="broadway",
        BROADWAY_DISPLAY=":92",
    )
    daemon = subprocess.Popen(
        ["gtk4-broadwayd", "--port=8192", ":92"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.3)
        if daemon.poll() is not None:
            raise RuntimeError(daemon.stderr.read().decode())
        code = """
import gi, time
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
Adw.init()
window = Adw.Window()
window.set_content(Gtk.Label(label='Native GNOME style check'))
window.present()
end = time.monotonic() + 0.2
while time.monotonic() < end:
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    time.sleep(0.01)
color = window.get_style_context().get_color()
assert abs(color.red - 17/255) < 0.01, color.to_string()
assert abs(color.green - 34/255) < 0.01, color.to_string()
assert abs(color.blue - 51/255) < 0.01, color.to_string()
window.destroy()
print('Native GTK 4/libadwaita window loaded the user CSS theme import.')
"""
        subprocess.run([sys.executable, "-c", code], env=environment, check=True, timeout=15)
    finally:
        daemon.terminate()
        daemon.wait(timeout=5)
        daemon.stderr.close()
