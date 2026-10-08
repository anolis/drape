"""Desktop selection, icon layering and GNOME's optional-tray lifetime."""

import os
import unittest
from unittest import mock

from drape import desktop, video_x11, wallpaper_desktop
from drape.ui.gtk import Gdk
from drape.ui.tray import WallpaperTray


class DesktopHostTests(unittest.TestCase):
    def test_gnome_x11_uses_its_lock_service_and_rejects_xwayland(self):
        with (
            mock.patch.object(desktop, "current_desktop", return_value="gnome"),
            mock.patch.dict(os.environ, DISPLAY=":1", XDG_SESSION_TYPE="x11", WAYLAND_DISPLAY=""),
        ):
            host = wallpaper_desktop.current()
            self.assertIsInstance(host, wallpaper_desktop.GnomeX11)
            self.assertEqual(host.lock_service, "org.gnome.ScreenSaver")
            self.assertEqual(host.lock_path, "/org/gnome/ScreenSaver")
            with mock.patch.dict(os.environ, XDG_SESSION_TYPE="wayland"):
                self.assertIsNone(wallpaper_desktop.current())
            with mock.patch.dict(os.environ, WAYLAND_DISPLAY="wayland-0"):
                self.assertIsNone(wallpaper_desktop.current())

    def test_mate_uses_its_own_lock_protocol_and_rejects_wayland(self):
        with (
            mock.patch.object(desktop, "current_desktop", return_value="mate"),
            mock.patch.dict(os.environ, DISPLAY=":1", XDG_SESSION_TYPE="x11", WAYLAND_DISPLAY=""),
        ):
            host = wallpaper_desktop.current()
            self.assertIsInstance(host, wallpaper_desktop.MateX11)
            self.assertEqual(host.lock_service, "org.mate.ScreenSaver")
            self.assertEqual(host.lock_path, "/org/mate/ScreenSaver")
            with mock.patch.dict(os.environ, XDG_SESSION_TYPE="wayland"):
                self.assertIsNone(wallpaper_desktop.current())

    def test_xfce_uses_its_own_lock_service_and_rejects_xwayland(self):
        with (
            mock.patch.object(desktop, "current_desktop", return_value="xfce"),
            mock.patch.dict(os.environ, DISPLAY=":1", XDG_SESSION_TYPE="x11", WAYLAND_DISPLAY=""),
        ):
            host = wallpaper_desktop.current()
            self.assertIsInstance(host, wallpaper_desktop.XfceX11)
            self.assertEqual(host.lock_service, "org.xfce.ScreenSaver")
            self.assertEqual(host.lock_path, "/org/xfce/ScreenSaver")
            with mock.patch.dict(os.environ, WAYLAND_DISPLAY="wayland-0"):
                self.assertIsNone(wallpaper_desktop.current())

    def test_gnome_wallpaper_is_restacked_below_existing_icon_windows(self):
        surface = mock.Mock()
        wallpaper = surface.window.get_window.return_value
        wallpaper.get_xid.return_value = 4
        icons = mock.Mock(get_xid=lambda: 1, get_type_hint=lambda: Gdk.WindowTypeHint.DESKTOP)
        app = mock.Mock(get_xid=lambda: 2, get_type_hint=lambda: Gdk.WindowTypeHint.NORMAL)
        with (
            mock.patch.object(Gdk.Screen, "get_default") as screen,
            mock.patch.object(video_x11, "restack_below") as restack,
        ):
            screen.return_value.get_window_stack.return_value = [icons, wallpaper, app]
            self.assertFalse(wallpaper_desktop.GnomeX11.restack([surface]))
            restack.assert_called_once_with(4, 1)
            surface.window.set_opacity.assert_called_once_with(0)
            screen.return_value.get_window_stack.return_value = [wallpaper, icons, app]
            self.assertTrue(wallpaper_desktop.GnomeX11.restack([surface]))

    def test_gnome_without_icons_requires_mapped_wallpaper_before_reveal(self):
        surface = mock.Mock()
        surface.window.get_window.return_value.get_xid.return_value = 4
        app = mock.Mock(get_xid=lambda: 2, get_type_hint=lambda: Gdk.WindowTypeHint.NORMAL)
        with mock.patch.object(Gdk.Screen, "get_default") as screen:
            screen.return_value.get_window_stack.return_value = [app]
            self.assertFalse(wallpaper_desktop.GnomeX11.restack([surface]))
            screen.return_value.get_window_stack.return_value = [surface.window.get_window(), app]
            self.assertTrue(wallpaper_desktop.GnomeX11.restack([surface]))

    def test_gnome_closes_to_background_without_a_tray(self):
        app, window = mock.Mock(), mock.Mock()
        tray = WallpaperTray(app)
        tray.icon = mock.Mock()
        tray.icon.is_embedded.return_value = False
        with mock.patch.object(
            wallpaper_desktop, "current", return_value=wallpaper_desktop.GnomeX11()
        ):
            self.assertTrue(tray.hide_window(window, None))
        window.hide.assert_called_once()
        app.activate.assert_not_called()
        app.quit.assert_not_called()

    def test_cinnamon_still_requires_a_visible_tray_before_hiding(self):
        app, window = mock.Mock(), mock.Mock()
        tray = WallpaperTray(app)
        tray.icon = mock.Mock()
        tray.icon.is_embedded.return_value = False
        with mock.patch.object(
            wallpaper_desktop, "current", return_value=wallpaper_desktop.CinnamonX11()
        ):
            self.assertTrue(tray.hide_window(window, None))
        window.hide.assert_not_called()
        window.notify.assert_called_once()
        app.activate.assert_called_once()
