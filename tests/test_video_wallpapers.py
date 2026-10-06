"""Video persistence, IPC boundaries, lifecycle and safe playback defaults."""

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from drape import desktop, video_mpv, video_x11
from drape import video_wallpapers as videos
from drape.ui.gtk import Gdk
from drape.ui.tray import WallpaperTray
from drape.video_player import Player, Surface


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(videos, "PATH", self.root / "videos.json")
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.root)})
        patch.start()
        self.addCleanup(patch.stop)
        self.video = self.root / "A video; $(do not execute).MP4"
        self.video.write_bytes(b"fixture")

    def test_library_keeps_files_and_merges_changes(self):
        videos.remember(str(self.video), "fit")
        second = self.root / "second.webm"
        second.touch()
        videos.remember(str(second))
        self.assertEqual(len(videos.library()["videos"]), 2)
        videos.forget(str(second))
        self.assertTrue(second.exists())
        self.assertEqual(videos.library()["selected"], str(self.video))
        self.video.unlink()
        self.assertIn(str(self.video), videos.library()["videos"])

    def test_corrupt_library_is_never_replaced(self):
        videos.PATH.write_text('{"version": 200}')
        with self.assertRaises(videos.VideoError):
            videos.remember(str(self.video))
        self.assertEqual(videos.PATH.read_text(), '{"version": 200}')

    def test_only_local_regular_videos_are_accepted(self):
        for value in ("https://example.com/file.mp4", str(self.root), "/missing.mp4", None):
            with self.subTest(value=value), self.assertRaises(videos.VideoError):
                videos.validate_video(value)
        self.assertEqual(videos.validate_video(str(self.video)), str(self.video))

    def test_stopped_status_does_not_create_runtime_files(self):
        self.assertFalse(videos.request("status")["available"])
        videos.stop()
        self.assertFalse((self.root / "drape-video").exists())

    def test_runtime_rejects_shared_directory_and_symlink(self):
        directory = videos.runtime_dir()
        directory.chmod(0o755)
        with self.assertRaises(videos.VideoError):
            videos.runtime_dir()
        directory.rmdir()
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(videos.VideoError):
            videos.runtime_dir()

    def test_wayland_and_other_desktops_are_not_supported(self):
        with (
            mock.patch.dict(
                os.environ, {"DISPLAY": ":1", "XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}
            ),
            mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
        ):
            self.assertTrue(videos.supported())
            with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}):
                self.assertFalse(videos.supported())
            with mock.patch.object(desktop, "current_desktop", return_value="gnome"):
                self.assertFalse(videos.supported())

    def test_player_argv_cannot_load_scripts_audio_or_external_references(self):
        args = video_mpv.command(str(self.video), 42, self.root / "mpv.sock", "fit")
        self.assertEqual(args[-2:], ["--", str(self.video)])
        for arg in (
            "--load-scripts=no",
            "--access-references=no",
            "--no-audio",
            "--stop-screensaver=no",
            "--panscan=0",
            "--wid=42",
        ):
            self.assertIn(arg, args)

    def test_decoding_avoids_direct_gpu_frames_and_vulkan_fallback(self):
        args = video_mpv.command(str(self.video), 42, self.root / "mpv.sock", "fit")
        self.assertIn("--gpu-api=opengl", args)
        self.assertIn("--hwdec=nvdec-copy,vaapi-copy,vdpau-copy", args)
        self.assertIn("--vd-lavc-dr=no", args)
        self.assertIn("--vd-lavc-threads=4", args)
        software = video_mpv.command(str(self.video), 42, self.root / "mpv.sock", "fit", "software")
        self.assertIn("--hwdec=no", software)
        self.assertIn("--vo=xv,x11", software)

    def test_noisy_player_log_keeps_tail_and_append_has_no_sparse_holes(self):
        surface = object.__new__(Surface)
        surface.log = self.root / "mpv-0.log"
        tail = b"newest error\n" * 6000
        surface.log.write_bytes(b"old data\n" * 30000 + tail)
        with surface.log.open("ab") as child_output:
            surface.limit_log()
            self.assertEqual(surface.log.stat().st_size, 64 * 1024)
            self.assertTrue(surface.log.read_bytes().endswith(tail[-100:]))
            child_output.write(b"next error\n")
        self.assertEqual(surface.log.stat().st_size, 64 * 1024 + len(b"next error\n"))

    def test_mpv_ipc_ignores_events_before_response(self):
        path = self.root / "mpv.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            server.listen(1)

            def reply():
                connection, _ = server.accept()
                with connection:
                    connection.recv(4096)
                    connection.sendall(b'{"event":"video-reconfig"}\n')
                    connection.sendall(b'{"request_id":1,"error":"success","data":true}\n')

            thread = threading.Thread(target=reply)
            thread.start()
            self.assertTrue(video_mpv.send(path, ["get_property", "pause"]))
            thread.join(2)

    def test_record_failure_happens_before_player_launch(self):
        with (
            mock.patch.object(videos, "supported", return_value=True),
            mock.patch.object(videos.shutil, "which", return_value="mpv"),
            mock.patch.object(videos, "remember", side_effect=videos.VideoError("disk full")),
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            with self.assertRaisesRegex(videos.VideoError, "disk full"):
                videos.play(str(self.video))
            launch.assert_not_called()

    def test_existing_worker_is_reused_even_when_stopped(self):
        with (
            mock.patch.object(videos, "supported", return_value=True),
            mock.patch.object(videos.shutil, "which", return_value="mpv"),
            mock.patch.object(
                videos,
                "request",
                side_effect=[{"available": True, "state": "stopped"}, {"state": "starting"}],
            ),
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            videos.play(str(self.video))
            launch.assert_not_called()

    def test_still_wallpaper_stops_video_before_gsettings_write(self):
        order = []
        with (
            mock.patch.object(videos, "supported", return_value=True),
            mock.patch.object(videos, "stop", side_effect=lambda: order.append("stop")),
            mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
            mock.patch.object(
                desktop, "_key", return_value=("org.cinnamon.desktop.background", "picture-uri")
            ),
            mock.patch.object(desktop.Gio.Settings, "new") as new,
            mock.patch.object(desktop.Gio.Settings, "sync"),
        ):
            new.return_value.set_string.side_effect = lambda *_: order.append("image") or True
            self.assertTrue(desktop.set_("wallpapers", self.video.as_uri()))
        self.assertEqual(order, ["stop", "image"])


class PlayerTests(unittest.TestCase):
    def player(self):
        player = object.__new__(Player)
        player.surfaces = [mock.Mock()]
        player.state, player.path, player.fit, player.error = "playing", "/video.mp4", "fill", ""
        player.paused, player.locked = False, False
        player.renderer = "gpu"
        return player

    def test_lock_resume_preserves_manual_pause(self):
        player = self.player()
        player.paused = True
        with mock.patch.object(video_mpv, "send") as send:
            player._lock_changed(None, None, None, None, None, mock.Mock(unpack=lambda: (True,)))
            player._lock_changed(None, None, None, None, None, mock.Mock(unpack=lambda: (False,)))
            self.assertEqual(player.state, "paused")
            self.assertEqual(send.call_args.args[1], ["set_property", "pause", True])

    def test_failed_player_releases_all_surfaces(self):
        player = self.player()
        surfaces = player.surfaces.copy()
        player._failed("bad video")
        surfaces[0].close.assert_called_once()
        self.assertEqual(player.surfaces, [])
        self.assertEqual(player.status()["message"], "bad video")

    def test_player_crash_recovers_without_losing_manual_pause(self):
        player = self.player()
        player.paused = True
        previous = player.surfaces[0]
        previous.process.poll.return_value = -11

        def build():
            player._clear()
            surface = mock.Mock()
            surface.process.poll.return_value = None
            player.surfaces.append(surface)
            player.state, player.deadline = "starting", time.monotonic() + 15

        with (
            mock.patch.object(player, "_build", side_effect=build),
            mock.patch.object(player, "_stack_below_icons", return_value=True),
            mock.patch.object(video_mpv, "send", return_value={"w": 3840}) as send,
        ):
            player._tick()
            self.assertEqual(player.renderer, "gpu-software")
            previous.close.assert_called_once()
            player._tick()
            self.assertEqual(player.state, "paused")
            self.assertEqual(send.call_args.args[1], ["set_property", "pause", True])
            self.assertEqual(player.path, "/video.mp4")

    def test_repeated_player_crashes_exhaust_recovery_and_restore_desktop(self):
        player = self.player()
        player.surfaces[0].process.poll.return_value = -11

        def build():
            player._clear()
            surface = mock.Mock()
            surface.process.poll.return_value = -11
            player.surfaces.append(surface)
            player.state = "starting"

        with mock.patch.object(player, "_build", side_effect=build) as rebuild:
            for _ in range(5):
                player._tick()
            self.assertEqual(rebuild.call_count, 2)
        self.assertEqual(player.state, "error")
        self.assertEqual(player.surfaces, [])
        self.assertIn("-11", player.error)

    def test_startup_timeout_also_has_bounded_recovery(self):
        player = self.player()
        player.state, player.deadline = "starting", time.monotonic() - 1
        player.surfaces[0].process.poll.return_value = None
        with (
            mock.patch.object(video_mpv, "send", side_effect=videos.VideoError("not ready")),
            mock.patch.object(player, "_build") as rebuild,
        ):
            player._tick()
        rebuild.assert_called_once()
        self.assertEqual(player.renderer, "gpu-software")

    def test_stop_only_closes_owned_surfaces(self):
        player = self.player()
        surface = player.surfaces[0]
        self.assertEqual(player.dispatch({"action": "stop"})["state"], "stopped")
        surface.close.assert_called_once()
        self.assertEqual(player.path, "")

    def test_video_is_restacked_below_lowest_nemo_desktop(self):
        player = self.player()
        video = player.surfaces[0].window.get_window.return_value
        video.get_xid.return_value = 4
        nemo = mock.Mock(get_xid=lambda: 1, get_type_hint=lambda: Gdk.WindowTypeHint.DESKTOP)
        second = mock.Mock(get_xid=lambda: 2, get_type_hint=lambda: Gdk.WindowTypeHint.DESKTOP)
        with (
            mock.patch.object(Gdk.Screen, "get_default") as screen,
            mock.patch.object(video_x11, "restack_below") as restack,
        ):
            screen.return_value.get_window_stack.return_value = [nemo, second, video]
            self.assertFalse(player._stack_below_icons())
            restack.assert_called_once_with(4, 1)
            player.surfaces[0].window.set_opacity.assert_called_with(0)
            restack.reset_mock()
            screen.return_value.get_window_stack.return_value = [video, nemo, second]
            self.assertTrue(player._stack_below_icons())
            restack.assert_not_called()

    def test_ewmh_request_only_targets_wallpaper_below_desktop(self):
        with mock.patch.object(video_x11.ctypes, "CDLL") as loader:
            lib = loader.return_value
            lib.XOpenDisplay.return_value = 123
            lib.XDefaultRootWindow.return_value = 456
            lib.XInternAtom.return_value = 789
            video_x11.restack_below(4, 1)
            args = lib.XSendEvent.call_args.args
            event = args[-1]._obj.client
            self.assertEqual(event.window, 4)
            self.assertEqual(list(event.data.l), [2, 1, 1, 0, 0])
            self.assertEqual(event.message_type, 789)
            self.assertEqual(args[1], 456)
            lib.XCloseDisplay.assert_called_once_with(123)

    def test_decoded_video_stays_hidden_until_order_is_verified(self):
        player = self.player()
        player.state, player.deadline = "starting", time.monotonic() + 10
        player.surfaces[0].process.poll.return_value = None
        with (
            mock.patch.object(video_mpv, "send", return_value={"w": 1920}),
            mock.patch.object(player, "_stack_below_icons", return_value=False) as stack,
        ):
            player._tick()
            player.surfaces[0].window.set_opacity.assert_not_called()
            self.assertEqual(player.state, "starting")
            stack.return_value = True
            player._tick()
            player.surfaces[0].window.set_opacity.assert_called_once_with(1)
            self.assertEqual(player.state, "playing")


class TrayTests(unittest.TestCase):
    def test_close_hides_window_and_retains_app_when_tray_exists(self):
        app = mock.Mock()
        tray = WallpaperTray(app)
        with (
            mock.patch("drape.ui.tray.Gtk.StatusIcon") as icon,
            mock.patch("drape.ui.tray.Gtk.IconTheme.get_default") as theme,
            mock.patch("drape.ui.tray.GLib.timeout_add_seconds", return_value=7),
        ):
            theme.return_value.has_icon.return_value = True
            icon.new_from_icon_name.return_value.is_embedded.return_value = True
            app.get_windows.return_value = []
            tray.enable({"state": "playing", "path": "/video.mp4"})
            icon.new_from_icon_name.assert_called_once_with("io.github.anolis.Drape")
            window = mock.Mock()
            self.assertTrue(tray.hide_window(window, None))
            window.hide.assert_called_once()
            app.hold.assert_called_once()
            app.quit.assert_not_called()
            with mock.patch("drape.ui.tray.GLib.source_remove"):
                tray.cleanup()
            app.release.assert_called_once()

    def test_missing_tray_never_strands_hidden_window(self):
        tray = WallpaperTray(mock.Mock())
        tray.icon = mock.Mock(is_embedded=lambda: False)
        window = mock.Mock()
        self.assertTrue(tray.hide_window(window, None))
        window.hide.assert_not_called()
        window.notify.assert_called_once()

    def test_quit_stops_worker_before_application_exit(self):
        app = mock.Mock()
        app.get_windows.return_value = []
        tray = WallpaperTray(app)
        order = []

        def run(work, done, _error):
            done(work())

        app.quit.side_effect = lambda: order.append("quit")
        with mock.patch("drape.ui.tray.run_async", side_effect=run):
            tray._action(
                lambda: order.append("stop") or {"state": "stopped", "path": ""}, quit_after=True
            )
        self.assertEqual(order, ["stop", "quit"])
