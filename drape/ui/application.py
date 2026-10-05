"""GTK application lifecycle and OCS link dispatch."""

from pathlib import Path
import sys

from .gtk import Gdk, Gio, GLib, Gtk
from .. import installer
from .common import APP_ID, error_dialog
from .widgets import GLYPH_CSS
from .window import Window


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        from .tray import WallpaperTray

        self.wallpaper_tray = WallpaperTray(self)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.idle_add(self.wallpaper_tray.reconnect)
        Gtk.IconTheme.get_default().append_search_path(
            str(Path(__file__).resolve().parents[2] / "data" / "icons")
        )
        css = Gtk.CssProvider()
        css.load_from_data(GLYPH_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _window(self):
        try:
            installer.load_manifest()
        except installer.InstallError as exc:
            error_dialog(self.get_active_window(), "Cannot load installed themes", exc)
            return None
        return self.get_active_window() or Window(self)

    def do_activate(self):
        win = self._window()
        if win is not None:
            win.present()

    def do_open(self, files, _n, _hint):
        win = self._window()
        if win is None:
            return
        win.present()
        for f in files:
            uri = f.get_uri()
            if uri.startswith(("ocs:", "ocss:")):
                win.install_link(uri)

    def do_shutdown(self):
        if self.wallpaper_tray.icon is not None and not self.wallpaper_tray.quitting:
            from .. import video_wallpapers

            try:
                video_wallpapers.request("quit")
            except video_wallpapers.VideoError as exc:
                print(f"drape: {exc}", file=sys.stderr)
        self.wallpaper_tray.cleanup()
        Gtk.Application.do_shutdown(self)


def main():
    return App().run(sys.argv)
