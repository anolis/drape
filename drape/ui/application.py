"""GTK application lifecycle and OCS link dispatch."""

from pathlib import Path
import sys

from .gtk import Gdk, Gio, Gtk
from .common import APP_ID
from .widgets import GLYPH_CSS
from .window import Window


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.IconTheme.get_default().append_search_path(str(Path(__file__).resolve().parents[2] / "data" / "icons"))
        css = Gtk.CssProvider()
        css.load_from_data(GLYPH_CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _window(self):
        return self.get_active_window() or Window(self)

    def do_activate(self):
        self._window().present()

    def do_open(self, files, _n, _hint):
        win = self._window()
        win.present()
        for f in files:
            uri = f.get_uri()
            if uri.startswith(("ocs:", "ocss:")):
                win.install_link(uri)


def main():
    return App().run(sys.argv)
