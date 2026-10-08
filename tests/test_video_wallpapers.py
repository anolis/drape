"""Video persistence, IPC boundaries, lifecycle and safe playback defaults."""

import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape import desktop, video_mpv, video_x11, wallpaper_desktop, wallpaper_sources
from drape import video_wallpapers as videos
from drape.ui.gtk import Gdk
from drape.ui.tray import WallpaperTray
from drape.video_player import Handler, Player, Surface


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(wallpaper_sources, "PATH", self.root / "sources.json")
        patch.start()
        self.addCleanup(patch.stop)
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

    def test_socket_reset_during_shutdown_is_unavailable_for_status_but_not_a_successful_play(self):
        (videos.runtime_dir() / "control.sock").touch()
        with mock.patch.object(videos.socket, "socket") as socket:
            socket.return_value.__enter__.return_value.makefile.return_value.__enter__.return_value.readline.side_effect = ConnectionResetError()
            self.assertFalse(videos.request("status")["available"])
            with self.assertRaises(videos.VideoError):
                videos.request("play", path=str(self.video))

    def test_worker_restart_waits_for_its_singleton_lock_after_socket_closes(self):
        (videos.runtime_dir() / "player.lock").touch()
        with (
            mock.patch.object(
                videos.fcntl, "flock", side_effect=[BlockingIOError(), None, None]
            ) as flock,
            mock.patch.object(videos.time, "sleep") as sleep,
        ):
            videos._wait_for_worker_exit()
        sleep.assert_called_once_with(0.05)
        self.assertEqual(flock.call_count, 3)

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
                self.assertTrue(videos.supported())
            with mock.patch.object(desktop, "current_desktop", return_value="mate"):
                self.assertTrue(videos.supported())
            with mock.patch.object(desktop, "current_desktop", return_value="lxqt"):
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

    def test_new_source_restarts_only_a_legacy_worker_through_its_socket(self):
        socket_path = videos.runtime_dir() / "control.sock"
        socket_path.touch()
        with (
            mock.patch.object(
                videos,
                "request",
                side_effect=[
                    {"available": True, "state": "playing"},
                    {"state": "stopped"},
                    {"available": True, "state": "stopped"},
                ],
            ) as request,
            mock.patch.object(videos.subprocess, "Popen") as launch,
            mock.patch.object(videos.time, "sleep", side_effect=lambda _: socket_path.unlink()),
        ):
            videos._ensure_worker("xscreensaver")
        launch.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            ["status", "quit", "status"],
        )

    def test_source_capable_worker_is_reused_for_animation_playback(self):
        with (
            mock.patch.object(
                videos,
                "request",
                return_value={
                    "available": True,
                    "state": "stopped",
                    "sources": ["video", "xscreensaver", "audio"],
                    "audio_visuals": 1,
                },
            ),
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            videos._ensure_worker("audio")
        launch.assert_not_called()

    def test_new_audio_options_replace_an_older_audio_capable_worker(self):
        with (
            mock.patch.object(
                videos,
                "request",
                side_effect=[
                    {"available": True, "state": "playing", "sources": ["audio"]},
                    {"state": "stopped"},
                    {"available": True, "state": "stopped", "audio_visuals": 1},
                ],
            ) as request,
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            videos._ensure_worker("audio")
        launch.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in request.call_args_list], ["status", "quit", "status"]
        )

    def test_xfce_replaces_a_worker_without_adapter_support_through_its_socket(self):
        with (
            mock.patch.object(
                wallpaper_desktop, "current", return_value=wallpaper_desktop.XfceX11()
            ),
            mock.patch.object(
                videos,
                "request",
                side_effect=[
                    {"available": True, "state": "stopped", "mate_background": 1},
                    {"state": "stopped"},
                    {"available": True, "state": "stopped"},
                ],
            ) as request,
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            videos._ensure_worker("video")
        launch.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in request.call_args_list], ["status", "quit", "status"]
        )

    def test_mate_replaces_worker_without_caja_support_through_private_socket(self):
        with (
            mock.patch.object(
                wallpaper_desktop, "current", return_value=wallpaper_desktop.MateX11()
            ),
            mock.patch.object(
                videos,
                "request",
                side_effect=[
                    {"available": True, "state": "stopped", "audio_visuals": 1},
                    {"state": "stopped"},
                    {"available": True, "state": "stopped"},
                ],
            ) as request,
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            videos._ensure_worker("video")
        launch.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in request.call_args_list], ["status", "quit", "status"]
        )

    def test_plasma_replaces_legacy_or_other_desktop_worker(self):
        for state in (
            {"available": True, "desktop_host": "kde"},
            {"available": True, "desktop_host": "kde", "plasma_background": 1},
            {"available": True, "desktop_host": "mate", "plasma_background": 1},
        ):
            with (
                self.subTest(state=state),
                mock.patch.object(
                    wallpaper_desktop, "current", return_value=wallpaper_desktop.PlasmaX11()
                ),
                mock.patch.object(
                    videos, "request", side_effect=[state, {}, {"available": True}]
                ) as request,
                mock.patch.object(videos.subprocess, "Popen") as launch,
            ):
                videos._ensure_worker("video")
                launch.assert_called_once()
                self.assertEqual(
                    [call.args[0] for call in request.call_args_list],
                    ["status", "quit", "status"],
                )

    def test_plasma_worker_with_lock_recovery_is_reused(self):
        with (
            mock.patch.object(
                wallpaper_desktop, "current", return_value=wallpaper_desktop.PlasmaX11()
            ),
            mock.patch.object(
                videos,
                "request",
                return_value={"available": True, "desktop_host": "kde", "plasma_background": 2},
            ),
            mock.patch.object(videos.subprocess, "Popen") as launch,
        ):
            videos._ensure_worker("video")
        launch.assert_not_called()

    def test_plasma_restore_recovers_a_lease_even_without_a_player(self):
        from drape import wallpaper_plasma

        order = []
        with (
            mock.patch.object(
                wallpaper_desktop, "current", return_value=wallpaper_desktop.PlasmaX11()
            ),
            mock.patch.object(videos, "stop", side_effect=lambda: order.append("stop")),
            mock.patch.object(
                wallpaper_plasma, "restore", side_effect=lambda: order.append("restore")
            ),
            mock.patch.object(videos, "request", return_value={"state": "stopped"}) as request,
        ):
            self.assertEqual(videos.restore_desktop(), {"state": "stopped"})
        self.assertEqual(order, ["stop", "restore"])
        request.assert_called_once_with("status")

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
        player.surfaces[0].log = Path("/missing/drape-animation.log")
        player.state, player.path, player.fit, player.error = "playing", "/video.mp4", "fill", ""
        player.paused, player.locked = False, False
        player.renderer = "gpu"
        player.source, player.name = "video", "video.mp4"
        player.options = wallpaper_sources.DEFAULTS.copy()
        player.audio, player.audio_timer = None, None
        player.presentation = None
        player.host = wallpaper_desktop.CinnamonX11()
        return player

    def test_mate_background_restores_before_rendering_windows_close(self):
        player = self.player()
        player.presentation = mock.Mock()
        order = []
        player.presentation.close.side_effect = lambda: order.append("restore")
        player.surfaces[0].close.side_effect = lambda: order.append("destroy")
        player._clear()
        self.assertEqual(order, ["restore", "destroy"])
        self.assertIsNone(player.presentation)

    def test_mate_helper_exit_stops_renderers_and_reports_failure(self):
        player = self.player()
        player.presentation = mock.Mock()
        player.presentation.ready.side_effect = videos.VideoError("MATE helper exited")
        surfaces = player.surfaces.copy()
        player._tick()
        self.assertEqual(player.state, "error")
        self.assertIn("MATE helper exited", player.error)
        surfaces[0].close.assert_called_once()

    def test_unresponsive_mate_helper_does_not_bypass_startup_timeout(self):
        player = self.player()
        player.state, player.source, player.deadline = "starting", "xscreensaver", 0
        player.surfaces[0].started = 0
        player.surfaces[0].process.poll.return_value = None
        player.presentation = mock.Mock()
        player.presentation.ready.return_value = False
        with (
            mock.patch.object(videos.time, "monotonic", return_value=20),
            mock.patch.object(player, "_stack_below_icons", return_value=True),
        ):
            player._tick()
        self.assertEqual(player.state, "error")
        self.assertIn("did not start", player.error)

    def test_restore_desktop_stops_surfaces_and_releases_xfce_adapter(self):
        player = self.player()
        player.host = wallpaper_desktop.XfceX11()
        player.host.guardian = mock.Mock()
        guardian = player.host.guardian
        surfaces = player.surfaces.copy()
        player.dispatch({"action": "restore_desktop"})
        surfaces[0].close.assert_called_once()
        guardian.close.assert_called_once()
        self.assertIsNone(player.host.guardian)
        self.assertEqual(player.state, "stopped")

    def test_lock_resume_preserves_manual_pause(self):
        player = self.player()
        player.paused = True
        with mock.patch.object(video_mpv, "send") as send:
            player._lock_changed(None, None, None, None, None, mock.Mock(unpack=lambda: (True,)))
            player._lock_changed(None, None, None, None, None, mock.Mock(unpack=lambda: (False,)))
            self.assertEqual(player.state, "paused")
            self.assertEqual(send.call_args.args[1], ["set_property", "pause", True])

    def test_lock_unlock_controls_every_source_and_preserves_manual_pause(self):
        for source in ("video", "xscreensaver", "audio"):
            for manual_pause in (False, True):
                with self.subTest(source=source, manual_pause=manual_pause):
                    player = self.player()
                    player.source, player.paused = source, manual_pause
                    player.presentation, player.audio = mock.Mock(), mock.Mock()
                    surface = player.surfaces[0]
                    with mock.patch.object(video_mpv, "send") as send:
                        player._lock_changed(
                            None, None, None, None, None, mock.Mock(unpack=lambda: (True,))
                        )
                        self.assertEqual(player.state, "paused")
                        player.presentation.pause.assert_called_with(True)
                        player._lock_changed(
                            None, None, None, None, None, mock.Mock(unpack=lambda: (False,))
                        )
                        self.assertEqual(player.state, "paused" if manual_pause else "playing")
                        player.presentation.pause.assert_called_with(manual_pause)
                        if source == "video":
                            send.assert_called_with(
                                surface.ipc, ["set_property", "pause", manual_pause]
                            )
                        elif source == "xscreensaver":
                            surface.pause_animation.assert_called_with(manual_pause)
                            send.assert_not_called()
                        else:
                            player.audio.pause.assert_called_with(manual_pause)
                            send.assert_not_called()

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

    def test_animation_pause_uses_only_owned_child_groups_and_no_mpv_ipc(self):
        player = self.player()
        player.source, player.path = "xscreensaver", "flakes"
        surface = player.surfaces[0]
        with mock.patch.object(video_mpv, "send") as send:
            player.dispatch({"action": "pause", "paused": True})
            self.assertEqual(player.state, "paused")
            surface.pause_animation.assert_called_once_with(True)
            player.dispatch({"action": "pause", "paused": False})
            surface.pause_animation.assert_called_with(False)
            send.assert_not_called()

    def test_animation_exit_reports_a_bounded_helper_error_from_its_log(self):
        player = self.player()
        player.source = "xscreensaver"
        surface = player.surfaces[0]
        surface.process.poll.return_value = 1
        with tempfile.TemporaryDirectory() as directory:
            surface.log = Path(directory) / "animation.log"
            surface.log.write_text(
                "old noise\n" * 1000
                + "xscreensaver-getimage-file: not found\nglitchpeg: too many errors loading images\n"
            )
            player._tick()
        self.assertEqual(player.state, "error")
        self.assertIn("xscreensaver-getimage-file: not found", player.error)
        self.assertNotIn("old noise\nold noise\nold noise\nold noise", player.error)
        self.assertLess(len(player.error), 1000)

    def test_animation_exit_has_no_video_decoder_retry_and_releases_windows(self):
        player = self.player()
        player.source = "xscreensaver"
        player.surfaces[0].process.poll.return_value = 1
        with mock.patch.object(player, "_build") as build:
            player._tick()
            build.assert_not_called()
        self.assertEqual(player.state, "error")
        self.assertEqual(player.surfaces, [])

    def test_audio_capture_failure_closes_session_and_desktop_windows(self):
        player = self.player()
        player.source = "audio"
        player.audio = mock.Mock(advance=mock.Mock(side_effect=videos.VideoError("capture failed")))
        session, surface = player.audio, player.surfaces[0]
        self.assertFalse(player._animate_audio())
        session.close.assert_called_once()
        surface.close.assert_called_once()
        self.assertEqual(player.state, "error")
        self.assertIsNone(player.audio)

    def test_audio_pause_and_stop_keep_capture_shared_and_close_it_once(self):
        player = self.player()
        player.source = "audio"
        session = player.audio = mock.Mock()
        player.dispatch({"action": "pause", "paused": True})
        session.pause.assert_called_once_with(True)
        player.dispatch({"action": "stop"})
        session.close.assert_called_once()
        self.assertIsNone(player.audio)

    def test_active_audio_inputs_are_reported_from_worker_choices(self):
        player = self.player()
        player.source = "audio"
        self.assertEqual(player.status()["audio_inputs"], ["Desktop audio"])
        player.options.update(desktop_audio=False, microphone=True)
        self.assertEqual(player.status()["audio_inputs"], ["Microphone"])
        player.options["desktop_audio"] = True
        self.assertEqual(player.status()["audio_inputs"], ["Desktop audio", "Microphone"])

    def test_silent_audio_does_not_repaint_full_monitor_frames(self):
        player = self.player()
        player.source = "audio"
        player.audio = mock.Mock(values=[0.0] * 64)
        player._animate_audio()
        player.surfaces[0].area.queue_draw.assert_not_called()
        player.audio.advance.side_effect = lambda: setattr(player.audio, "values", [0.8] * 64)
        player._animate_audio()
        player.surfaces[0].area.queue_draw.assert_called_once()

    def test_color_motion_repaints_steady_audio_but_not_silence_or_pause(self):
        player = self.player()
        player.source, player.path = "audio", "bars"
        player.options["cycle_colors"] = True
        player.audio = mock.Mock(values=[0.8] * 64)
        area = player.surfaces[0].area
        player._animate_audio()
        area.queue_draw.assert_called_once()
        area.queue_draw.reset_mock()
        player.audio.values = [0.0] * 64
        player._animate_audio()
        area.queue_draw.assert_not_called()
        player.state = "paused"
        player.audio.values = [0.8] * 64
        player._animate_audio()
        area.queue_draw.assert_not_called()

    def test_invalid_new_source_preserves_existing_playback(self):
        player = self.player()
        with (
            mock.patch.object(videos, "supported", return_value=True),
            self.assertRaises(videos.VideoError),
        ):
            player.dispatch({"action": "play", "source": "unknown", "path": "/bin/sh"})
        self.assertEqual(player.source, "video")
        self.assertEqual(player.path, "/video.mp4")
        player.surfaces[0].close.assert_not_called()

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


class HandlerTests(unittest.TestCase):
    def handler(self, action, stopping=True):
        handler = object.__new__(Handler)
        handler.request = mock.Mock()
        handler.rfile = io.BytesIO(json.dumps({"action": action}).encode() + b"\n")
        handler.wfile = io.BytesIO()
        handler.server = SimpleNamespace(stopping=stopping, player=mock.Mock())
        return handler

    def test_shutdown_status_does_not_wait_for_the_exited_gtk_loop(self):
        handler = self.handler("status")
        with mock.patch("drape.video_player.GLib.idle_add") as idle:
            handler.handle()
        idle.assert_not_called()
        reply = json.loads(handler.wfile.getvalue())
        self.assertEqual(reply["state"], "stopped")
        self.assertTrue(reply["stopping"])

    def test_shutdown_rejects_play_without_launching_any_renderer(self):
        handler = self.handler("play")
        handler.handle()
        self.assertIn("shutting down", json.loads(handler.wfile.getvalue())["error"])
        handler.server.player.dispatch.assert_not_called()

    def test_quit_marks_the_server_stopping_before_main_loop_exit(self):
        handler = self.handler("quit", stopping=False)
        handler.server.player.dispatch.return_value = {"state": "stopped"}
        with mock.patch(
            "drape.video_player.GLib.idle_add", side_effect=lambda callback: callback()
        ):
            handler.handle()
        self.assertTrue(handler.server.stopping)


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
