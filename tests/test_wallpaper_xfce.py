"""Xfce consent, scoped background ownership, compiler caching and restoration."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape import wallpaper_desktop
from drape import wallpaper_xfce_adapter as adapter
from drape import wallpaper_xfce_x11 as backend
from drape.video_wallpapers import VideoError


class HostTests(unittest.TestCase):
    def test_first_use_requires_explicit_restart_consent(self):
        host = wallpaper_desktop.XfceX11()
        with (
            mock.patch.object(adapter, "Guardian") as guardian,
            self.assertRaisesRegex(VideoError, "Enable Xfce"),
        ):
            host.setup()
        guardian.assert_not_called()

    def test_adapter_is_reused_until_explicit_restore(self):
        host = wallpaper_desktop.XfceX11()
        with mock.patch.object(adapter, "Guardian") as guardian:
            host.setup(True)
            host.setup()
            guardian.assert_called_once()
            host.close()
            guardian.return_value.close.assert_called_once()
            self.assertIsNone(host.guardian)

    def test_render_hosts_are_popups_below_desktop_and_never_revealed(self):
        from drape.ui.gtk import Gdk

        monitor = mock.Mock()
        monitor.get_geometry.return_value = SimpleNamespace(x=100, y=200)
        with (
            mock.patch.object(wallpaper_desktop.X11Desktop, "window") as window,
            mock.patch.object(Gdk, "get_default_root_window") as root,
        ):
            root.return_value.get_width.return_value = 1920
            wallpaper_desktop.XfceX11.window(monitor)
            window.assert_called_once_with(monitor, popup=True)
            window.return_value.move.assert_called_once_with(2020, 200)
        surface = mock.Mock()
        surface.window.get_window.return_value.is_visible.return_value = True
        self.assertTrue(wallpaper_desktop.XfceX11.restack([surface]))
        surface.window.get_window.return_value.lower.assert_called_once()
        wallpaper_desktop.XfceX11.reveal([surface])
        surface.window.set_opacity.assert_called_once_with(0)


class BackgroundTests(unittest.TestCase):
    def background(self):
        bg = object.__new__(backend.Background)
        bg.x = mock.Mock()
        bg.x.errors = []
        bg.adapter, bg.pixmap, bg.gc, bg.frame = os.getpid(), 20, "gc", 0
        bg.frames, bg.redirected, bg.windows = [(30, 800, 600, 10, 20)], [40], {41}
        bg.x.property.side_effect = lambda name, _: {
            backend.ADAPTER: os.getpid(),
            backend.OWNER: os.getpid(),
        }[name]
        return bg

    def test_publishes_only_owned_pixmap_and_frame_after_copy(self):
        bg = self.background()
        bg.draw()
        bg.x.copy.assert_called_once_with(30, 20, "gc", 800, 600, 10, 20)
        bg.x.lib.XLowerWindow.assert_called_once_with(bg.x.display, 41)
        self.assertEqual(
            bg.x.set_property.call_args_list,
            [mock.call(backend.PIXMAP, 20, 20), mock.call(backend.FRAME, 1, 6)],
        )
        bg.x.repaint.assert_not_called()

    def test_stop_removes_only_drape_namespace_and_frees_owned_resources(self):
        bg = self.background()
        bg.close()
        self.assertEqual(
            bg.x.delete_property.call_args_list,
            [mock.call(name) for name in (backend.PIXMAP, backend.FRAME, backend.OWNER)],
        )
        bg.x.lib.XFreePixmap.assert_any_call(bg.x.display, 20)
        bg.x.lib.XFreePixmap.assert_any_call(bg.x.display, 30)
        bg.x.lib.XUngrabServer.assert_called_once()
        bg.x.copy.assert_not_called()

    def test_stop_does_not_remove_another_publishers_properties(self):
        bg = self.background()
        bg.x.property.return_value = os.getpid() + 100
        bg.x.property.side_effect = None
        bg.close()
        bg.x.delete_property.assert_not_called()
        bg.x.lib.XUngrabServer.assert_called_once()

    def test_restarted_desktop_does_not_keep_claiming_playback(self):
        bg = self.background()
        with mock.patch.object(backend.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(bg.unchanged())
            with self.assertRaisesRegex(backend.BackgroundError, "restarted"):
                bg.draw()
        bg.x.copy.assert_not_called()

    def test_dead_publisher_properties_do_not_block_the_next_source(self):
        bg = self.background()
        with mock.patch.object(backend.os, "kill", side_effect=ProcessLookupError):
            bg._discard_stale_publisher()
        self.assertEqual(
            bg.x.delete_property.call_args_list,
            [mock.call(name) for name in (backend.PIXMAP, backend.FRAME, backend.OWNER)],
        )
        bg.x.lib.XFreePixmap.assert_not_called()

    def test_live_publishers_are_never_discarded(self):
        bg = self.background()
        with self.assertRaisesRegex(backend.BackgroundError, "Another live"):
            bg._discard_stale_publisher()
        bg.x.delete_property.assert_not_called()


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "adapter.c"
        self.source.write_text("a source revision")
        for patch in (
            mock.patch.object(adapter, "SOURCE", self.source),
            mock.patch.object(adapter, "runtime_dir", return_value=self.root),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_missing_compiler_fails_before_desktop_is_touched(self):
        with (
            mock.patch.object(adapter.shutil, "which", return_value=None),
            mock.patch.object(adapter.subprocess, "run") as run,
            self.assertRaisesRegex(VideoError, "compiler"),
        ):
            adapter.build()
        run.assert_not_called()

    def test_successful_build_is_atomic_and_cached_by_source(self):
        def compile_(args, **_):
            Path(args[args.index("-o") + 1]).write_bytes(b"adapter")
            return SimpleNamespace(returncode=0, stdout="")

        with (
            mock.patch.object(adapter.shutil, "which", return_value="cc"),
            mock.patch.object(adapter.subprocess, "run", side_effect=compile_) as run,
        ):
            first = adapter.build()
            self.assertEqual(adapter.build(), first)
            self.assertEqual(run.call_count, 1)
            self.source.write_text("a different source revision")
            self.assertNotEqual(adapter.build(), first)
            self.assertEqual(run.call_count, 2)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_compiler_failure_does_not_cache_a_broken_adapter(self):
        with (
            mock.patch.object(adapter.shutil, "which", return_value="cc"),
            mock.patch.object(
                adapter.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=1, stdout="link error"),
            ),
            self.assertRaisesRegex(VideoError, "link error"),
        ):
            adapter.build()
        self.assertFalse(list(self.root.glob("*.so")))


class DisplayTests(unittest.TestCase):
    def test_display_matching_preserves_host_names_but_ignores_screen(self):
        self.assertEqual(adapter._display_key("host.example:2.1"), "host.example:2")
        self.assertEqual(adapter._display_key(":0.0"), ":0")
        self.assertNotEqual(adapter._display_key("host.one:0"), adapter._display_key("host.two:0"))


class RecoveryTests(unittest.TestCase):
    def test_orphan_restore_does_not_quit_an_unrelated_desktop(self):
        from drape import wallpaper_x11

        x11 = mock.Mock()
        x11.property.return_value = 42
        with (
            mock.patch.object(wallpaper_x11, "X11", return_value=x11),
            mock.patch.object(adapter, "_display_desktops", return_value=[43]),
            mock.patch.object(adapter.subprocess, "run") as run,
        ):
            adapter.restore(Path("adapter.so"))
        run.assert_not_called()
        x11.close.assert_called_once()

    def test_owned_orphan_is_restored_without_inheriting_player_lifetime(self):
        from drape import wallpaper_x11

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "42").mkdir()
            (root / "42/environ").write_bytes(b"LD_PRELOAD=/owned/adapter.so\0")
            x11 = mock.Mock()
            x11.property.return_value = 42
            with (
                mock.patch.object(wallpaper_x11, "X11", return_value=x11),
                mock.patch.object(adapter, "PROC", root),
                mock.patch.object(adapter, "runtime_dir", return_value=root),
                mock.patch.object(adapter, "_display_desktops", side_effect=[[42], [], []]),
                mock.patch.object(adapter.shutil, "which", return_value="xfdesktop"),
                mock.patch.object(adapter.subprocess, "run") as run,
                mock.patch.object(adapter.subprocess, "Popen") as launch,
            ):
                adapter.restore(Path("/owned/adapter.so"))
            run.assert_called_once()
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            x11.delete_property.assert_called_once_with(backend.ADAPTER)
            x11.close.assert_called_once()
