"""Desktop-specific wallpaper hosting, separate from video and animation renderers.

Register another host only after its desktop/icon layering and lock protocol are
verified. A Wayland host will need compositor integration rather than X11 IDs.
"""

import os

from . import desktop


class X11Desktop:
    """Input-transparent desktop surfaces shared by verified X11 hosts."""

    background_without_tray = False

    @classmethod
    def supported(cls):
        return (
            desktop.current_desktop() == cls.desktop_name
            and os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"
            and not os.environ.get("WAYLAND_DISPLAY")
            and bool(os.environ.get("DISPLAY"))
        )

    @staticmethod
    def window(monitor):
        from .ui.gtk import Gdk, Gtk

        window = Gtk.Window(title="Drape Live Wallpaper")
        window.set_wmclass("drape-video-wallpaper", "DrapeVideoWallpaper")
        window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        window.set_decorated(False)
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_keep_below(True)
        # Desktop windows can map above icon hosts. Keep them invisible until
        # the player has rendered and desktop ordering has been verified.
        window.set_opacity(0)
        window.stick()
        rect = monitor.get_geometry()
        window.move(rect.x, rect.y)
        window.set_default_size(rect.width, rect.height)
        return window

    @staticmethod
    def restack(surfaces):
        from . import video_x11
        from .ui.gtk import Gdk

        if not surfaces:
            return True
        owned = {surface.window.get_window().get_xid() for surface in surfaces}
        stack = Gdk.Screen.get_default().get_window_stack() or []
        anchor = next(
            (
                window
                for window in stack
                if window.get_xid() not in owned
                and window.get_type_hint() == Gdk.WindowTypeHint.DESKTOP
            ),
            None,
        )
        present = {window.get_xid() for window in stack}
        correct = owned <= present
        if anchor is not None:
            above_icons = {window.get_xid() for window in stack[stack.index(anchor) + 1 :]}
            for surface in surfaces:
                window = surface.window.get_window()
                if window.get_xid() in above_icons:
                    surface.window.set_opacity(0)
                    video_x11.restack_below(window.get_xid(), anchor.get_xid())
                    correct = False
        return correct


class CinnamonX11(X11Desktop):
    desktop_name = "cinnamon"
    lock_service = "org.cinnamon.ScreenSaver"
    lock_path = "/org/cinnamon/ScreenSaver"
    label = "Cinnamon on X11"


class GnomeX11(X11Desktop):
    desktop_name = "gnome"
    lock_service = "org.gnome.ScreenSaver"
    lock_path = "/org/gnome/ScreenSaver"
    label = "GNOME on X11"
    # GNOME does not expose an XEmbed tray by default. GtkApplication's hold
    # keeps playback controls alive; launching Drape again presents the window.
    background_without_tray = True


HOSTS = (CinnamonX11, GnomeX11)


def current():
    return next((host() for host in HOSTS if host.supported()), None)
