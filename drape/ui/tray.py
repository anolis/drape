"""Background playback lifetime and optional tray controls."""

from pathlib import Path

from .. import video_wallpapers as videos
from .common import APP_ID, error_dialog, run_async
from .gtk import GLib, Gtk


class WallpaperTray:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self.menu = None
        self.timer = None
        self.checking = False
        self.quitting = False
        self.state = {"state": "stopped", "path": ""}

    def enable(self, state):
        self.state = state
        if self.icon is None:
            self.app.hold()
            if Gtk.IconTheme.get_default().has_icon(APP_ID):
                self.icon = Gtk.StatusIcon.new_from_icon_name(APP_ID)
            else:
                # A source checkout need not have the application icon installed.
                logo = Path(__file__).resolve().parents[2] / "data" / f"{APP_ID}.svg"
                self.icon = Gtk.StatusIcon.new_from_file(str(logo))
            self.icon.set_title("Drape")
            self.icon.connect("activate", lambda *_: self.app.activate())
            self.icon.connect("popup-menu", self._popup)
            self.icon.set_visible(True)
            self.timer = GLib.timeout_add_seconds(2, self._poll)
        self._update(state)

    def reconnect(self):
        """Recover controls for a player surviving a previous browser crash."""
        if videos.supported():

            def done(state):
                if state["state"] in {"starting", "playing", "paused"}:
                    self.enable(state)

            run_async(lambda: videos.request("status"), done)
        return False

    def hide_window(self, window, _event):
        if self.icon is None or self.quitting:
            return False
        # GNOME can reopen its held application without a tray. Cinnamon keeps
        # the window visible when its expected tray applet is disabled.
        if not self.icon.is_embedded():
            from .. import wallpaper_desktop

            host = wallpaper_desktop.current()
            if host is not None and host.background_without_tray:
                window.hide()
                return True
            self.app.activate()
            window.notify(
                "Enable Cinnamon's system tray applet to hide Drape during wallpaper playback."
            )
            return True
        window.hide()
        return True

    def _update(self, state):
        self.state = state
        if self.icon:
            name = state.get("name") or Path(state.get("path", "")).name
            self.icon.set_tooltip_text(
                "Drape · "
                + (name or "Static wallpaper")
                + " · "
                + state["state"]
                + (" · " + " + ".join(state["audio_inputs"]) if state.get("audio_inputs") else "")
            )
        for window in self.app.get_windows():
            page = getattr(window, "videopage", None)
            if page is not None and page.alive and not page.busy:
                page.update_status(state)

    def _poll(self):
        if self.quitting:
            return False
        if not self.checking:
            self.checking = True

            def finish(state):
                self.checking = False
                self._update(state)

            def failed(error):
                finish(
                    {"state": "error", "path": self.state.get("path", ""), "message": str(error)}
                )

            run_async(lambda: videos.request("status"), finish, failed)
        return True

    def _action(self, work, quit_after=False):
        if self.checking:
            return
        self.checking = True

        def done(state):
            self.checking = False
            self._update(state)
            if quit_after:
                self.quitting = True
                self.app.quit()

        def failed(error):
            self.checking = False
            self.app.activate()
            error_dialog(self.app.get_active_window(), "Could not control live wallpaper", error)

        run_async(work, done, failed)

    def _popup(self, _icon, button, timestamp):
        if self.menu is not None:
            self.menu.destroy()
        self.menu = Gtk.Menu()
        items = [
            ("Show Drape", lambda: self.app.activate(), True),
            (
                "Resume wallpaper" if self.state["state"] == "paused" else "Pause wallpaper",
                lambda: self._action(
                    lambda: videos.request("pause", paused=self.state["state"] != "paused")
                ),
                self.state["state"] in {"playing", "paused"},
            ),
            (
                "Stop wallpaper",
                lambda: self._action(videos.stop),
                self.state["state"] in {"playing", "paused", "starting", "error"},
            ),
        ]
        for label, callback, sensitive in items:
            item = Gtk.MenuItem(label=label, sensitive=sensitive and not self.checking)
            item.connect("activate", lambda _item, action=callback: action())
            self.menu.append(item)
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit Drape", sensitive=not self.checking)
        quit_item.connect(
            "activate", lambda *_: self._action(lambda: videos.request("quit"), quit_after=True)
        )
        self.menu.append(quit_item)
        self.menu.show_all()
        self.menu.popup(None, None, Gtk.StatusIcon.position_menu, self.icon, button, timestamp)

    def cleanup(self):
        self.quitting = True
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.icon is not None:
            self.icon.set_visible(False)
            self.icon = None
            self.app.release()
        if self.menu is not None:
            self.menu.destroy()
            self.menu = None
