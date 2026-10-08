"""Desktop-specific wallpaper hosting, separate from video and animation renderers.

Register another host only after its desktop/icon layering and lock protocol are
verified. A Wayland host will need compositor integration rather than X11 IDs.
"""

import os

from . import desktop


class X11Desktop:
    """Input-transparent desktop surfaces shared by verified X11 hosts."""

    background_without_tray = False
    copy_background = False

    @staticmethod
    def video_profiles():
        from .video_mpv import PROFILES

        return PROFILES

    @classmethod
    def supported(cls):
        return (
            desktop.current_desktop() == cls.desktop_name
            and os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"
            and not os.environ.get("WAYLAND_DISPLAY")
            and bool(os.environ.get("DISPLAY"))
        )

    @staticmethod
    def window(monitor, *, popup=False):
        from .ui.gtk import Gdk, Gtk

        window = Gtk.Window(
            title="Drape Live Wallpaper",
            type=Gtk.WindowType.POPUP if popup else Gtk.WindowType.TOPLEVEL,
        )
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

    def setup(self, allow_restart=False):
        pass

    def close(self):
        pass

    def check(self):
        pass

    @staticmethod
    def reveal(surfaces):
        for surface in surfaces:
            surface.window.set_opacity(1)

    @staticmethod
    def present(surfaces, source):
        return None

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


class MateX11(X11Desktop):
    desktop_name = "mate"
    lock_service = "org.mate.ScreenSaver"
    lock_path = "/org/mate/ScreenSaver"
    label = "MATE on X11"
    copy_background = True

    @staticmethod
    def video_profiles():
        # GPU/XVideo overlays are not guaranteed to enter Caja’s pixmap.
        # X11 software frames can be copied reliably with or without compositing.
        return ("software",)

    @staticmethod
    def present(surfaces, source):
        from .wallpaper_background import Mirror

        return Mirror(surfaces, source)


class XfceX11(X11Desktop):
    desktop_name = "xfce"
    lock_service = "org.xfce.ScreenSaver"
    lock_path = "/org/xfce/ScreenSaver"
    label = "Xfce on X11"
    copy_background = True

    def __init__(self):
        self.guardian = None

    @staticmethod
    def video_profiles():
        return ("software",)

    @staticmethod
    def window(monitor):
        # Xfwm keeps its managed desktop above other desktop-type clients.
        # These unmapped-to-the-WM render hosts are copied into Xfdesktop;
        # the adapter, rather than the render host, draws the visible wallpaper.
        from .ui.gtk import Gdk

        window = X11Desktop.window(monitor, popup=True)
        rect = monitor.get_geometry()
        # Render outside the root viewport. Some hacks raise their host;
        # an offscreen host cannot cover icons even with compositing enabled.
        window.move(Gdk.get_default_root_window().get_width() + rect.x, rect.y)
        return window

    @staticmethod
    def restack(surfaces):
        for surface in surfaces:
            surface.window.get_window().lower()
        return all(surface.window.get_window().is_visible() for surface in surfaces)

    @staticmethod
    def reveal(surfaces):
        for surface in surfaces:
            surface.window.set_opacity(0)

    def setup(self, allow_restart=False):
        from .video_wallpapers import VideoError
        from .wallpaper_xfce_adapter import Guardian

        if self.guardian is not None:
            try:
                self.guardian.ready()
                return
            except VideoError:
                self.close()
        if not allow_restart:
            raise VideoError(
                "Enable Xfce’s desktop adapter from Live wallpapers before starting playback."
            )
        self.guardian = Guardian()

    def check(self):
        if self.guardian is not None:
            self.guardian.ready()

    def present(self, surfaces, source):
        from .wallpaper_background import Mirror

        self.guardian.ready()
        return Mirror(surfaces, source, "xfce")

    def close(self):
        if self.guardian is not None:
            self.guardian.close()
            self.guardian = None


class PlasmaX11(X11Desktop):
    desktop_name = "kde"
    lock_service = "org.freedesktop.ScreenSaver"
    lock_path = "/ScreenSaver"
    label = "Plasma 6 on X11"
    copy_background = True
    background_without_tray = True

    @classmethod
    def supported(cls):
        from . import kde

        return super().supported() and kde.major_version() == 6

    @staticmethod
    def video_profiles():
        return ("software",)

    @staticmethod
    def window(monitor):
        # Plasma's wallpaper plugin presents the copied frames, while the
        # original render windows remain outside the visible desktop.
        return XfceX11.window(monitor)

    restack = staticmethod(XfceX11.restack)
    reveal = staticmethod(XfceX11.reveal)

    def setup(self, allow_restart=False):
        from . import wallpaper_plasma

        wallpaper_plasma.install()

    @staticmethod
    def present(surfaces, source):
        from .wallpaper_plasma import Presentation

        return Presentation(surfaces, source)


HOSTS = (CinnamonX11, GnomeX11, MateX11, XfceX11, PlasmaX11)


def current():
    return next((host() for host in HOSTS if host.supported()), None)
