"""Caja background ownership, restoration and player presentation lifecycle."""

import json
import unittest
from unittest import mock

from drape import video_mpv, wallpaper_desktop, wallpaper_mate
from drape.video_wallpapers import VideoError
from drape.wallpaper_mate_x11 import Background, BackgroundError


class BackgroundTests(unittest.TestCase):
    def background(self):
        bg = object.__new__(Background)
        bg.x = mock.Mock()
        bg.x.errors = []
        bg.caja, bg.pixmap, bg.backup, bg.gc = 10, 20, 30, "gc"
        bg.width, bg.height = 1920, 1080
        bg.frames = [(40, 1920, 1080, 0, 0)]
        bg.redirected = [50]
        bg.x.property.side_effect = lambda name, _: {
            "_XROOTPMAP_ID": 20,
            "CAJA_DESKTOP_WINDOW_ID": 10,
        }[name]
        return bg

    def test_frames_update_background_and_request_icon_redraw(self):
        bg = self.background()
        bg.draw()
        bg.x.copy.assert_called_once_with(40, 20, "gc", 1920, 1080, 0, 0)
        bg.x.repaint.assert_called_once_with(10)

    def test_stop_restores_original_pixels_before_freeing_backup(self):
        bg = self.background()
        bg.close()
        bg.x.copy.assert_called_once_with(30, 20, "gc", 1920, 1080)
        bg.x.repaint.assert_called_once_with(10)
        bg.x.lib.XGrabServer.assert_called_once()
        bg.x.lib.XUngrabServer.assert_called_once()
        self.assertEqual(bg.backup, 0)
        self.assertEqual(bg.frames, [])

    def test_new_static_wallpaper_is_never_overwritten_or_restored_over(self):
        bg = self.background()
        bg.x.property.return_value = 99
        bg.x.property.side_effect = None
        with self.assertRaisesRegex(BackgroundError, "changed outside Drape"):
            bg.draw()
        bg.close()
        bg.x.copy.assert_not_called()
        bg.x.repaint.assert_not_called()
        bg.x.lib.XFreePixmap.assert_any_call(bg.x.display, 30)

    def test_missing_desktop_during_cleanup_always_releases_server(self):
        bg = self.background()
        bg.x.property.side_effect = BackgroundError("Desktop disappeared")
        with self.assertRaises(BackgroundError):
            bg.close()
        bg.x.lib.XUngrabServer.assert_called_once()

    def test_no_caja_does_not_touch_background(self):
        x11 = mock.Mock()
        x11.property.return_value = 0
        bg = Background(x11, [[4, 0, 0]])
        bg.draw()
        x11.copy.assert_not_called()
        x11.composite.XCompositeRedirectWindow.assert_not_called()


class MirrorTests(unittest.TestCase):
    def mirror(self):
        mirror = object.__new__(wallpaper_mate.Mirror)
        mirror.process = mock.Mock()
        mirror.process.poll.return_value = None
        mirror.buffer, mirror.prepared = b"", False
        return mirror

    def test_nonblocking_ready_preserves_partial_messages(self):
        mirror = self.mirror()
        with mock.patch.object(
            wallpaper_mate.os, "read", side_effect=[b'{"rea', BlockingIOError()]
        ):
            self.assertFalse(mirror.ready())
        with mock.patch.object(
            wallpaper_mate.os, "read", side_effect=[b'dy": true}\n', BlockingIOError()]
        ):
            self.assertTrue(mirror.ready())

    def test_helper_failure_is_reported_instead_of_claiming_playback(self):
        mirror = self.mirror()
        with (
            mock.patch.object(
                wallpaper_mate.os,
                "read",
                side_effect=[json.dumps({"error": "missing pixmap"}).encode() + b"\n", b""],
            ),
            self.assertRaisesRegex(VideoError, "missing pixmap"),
        ):
            mirror.ready()

    def test_close_signals_eof_and_waits_for_restoration(self):
        mirror = self.mirror()
        mirror.close()
        mirror.process.stdin.close.assert_called_once()
        mirror.process.wait.assert_called_once_with(timeout=3)
        mirror.process.terminate.assert_not_called()
        mirror.process.stdout.close.assert_called_once()

    def test_mate_video_copies_software_frames_without_hardware_overlays(self):
        host = wallpaper_desktop.MateX11()
        self.assertEqual(host.video_profiles(), ("software",))
        args = video_mpv.command(
            "video.mp4", 5, "ipc", "fill", "software", copy_background=host.copy_background
        )
        self.assertIn("--vo=x11", args)
        self.assertIn("--vf=fps=30", args)
        self.assertIn("--hwdec=no", args)
        self.assertIn("--loop-file=inf", args)
        self.assertIn("--no-audio", args)
        ordinary = video_mpv.command("video.mp4", 5, "ipc", "fill")
        self.assertIn("--vo=gpu,xv,x11", ordinary)
        self.assertNotIn("--vf=fps=30", ordinary)

    def test_only_mate_uses_caja_presentation(self):
        with mock.patch.object(wallpaper_mate, "Mirror") as mirror:
            wallpaper_desktop.MateX11.present(["surface"], "video")
            mirror.assert_called_once_with(["surface"], "video")
            self.assertIsNone(wallpaper_desktop.CinnamonX11.present([], "audio"))
            self.assertIsNone(wallpaper_desktop.GnomeX11.present([], "video"))
