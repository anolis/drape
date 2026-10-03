import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import desktop, xfce, previews, app


class XfceTest(unittest.TestCase):
    def setUp(self):
        self.values = {}
        patch = mock.patch.object(xfce, "query", side_effect=self.query)
        patch.start()
        self.addCleanup(patch.stop)
        live = mock.patch.object(xfce, "live_wallpaper_targets", return_value={})
        self.live = live.start()
        self.addCleanup(live.stop)

    def query(self, channel, *args):
        if args == ("-l",):
            return "\n".join(self.values)
        key = args[args.index("-p") + 1]
        if "-s" in args:
            self.values[key] = args[args.index("-s") + 1]
        elif "-r" in args:
            self.values.pop(key, None)
        else:
            return self.values[key]
        return ""

    def test_wallpaper_all_monitors_workspaces_and_legacy(self):
        bases = [
            "/backdrop/screen0/monitorHDMI-1/workspace0",
            "/backdrop/screen0/monitorHDMI-1/workspace1",
            "/backdrop/screen1/monitorDP-2/workspace0",
        ]
        for base in bases:
            self.values[base + "/last-image"] = "/old.png"
            self.values[base + "/backdrop-cycle-enable"] = "true"
        self.values[bases[0] + "/image-style"] = "3"
        self.values[bases[1] + "/image-style"] = "0"
        legacy = "/backdrop/screen0/monitorHDMI-1/image-path"
        self.values[legacy] = "/legacy.png"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "night sky ü.png"
            path.touch()
            self.assertTrue(xfce.apply_wallpaper(path.as_uri()))
            for base in bases:
                self.assertEqual(self.values[base + "/last-image"], str(path))
                self.assertEqual(self.values[base + "/backdrop-cycle-enable"], "false")
            self.assertEqual(xfce.wallpaper(), path.as_uri())
        self.assertEqual(self.values[bases[0] + "/image-style"], "3")
        self.assertEqual(self.values[bases[1] + "/image-style"], "5")
        self.assertEqual(self.values[legacy], "/legacy.png")

    def test_wallpaper_creates_live_target_and_validates_local_file(self):
        base = "/backdrop/screen0/monitoreDP-1/workspace0"
        key = base + "/last-image"
        self.values["/backdrop/screen0/monitor0/image-path"] = "/old.png"
        self.live.return_value = {base: key}
        with tempfile.NamedTemporaryFile() as image:
            xfce.apply_wallpaper(image.name)
            self.assertEqual(self.values[key], image.name)
            self.assertEqual(self.values["/backdrop/screen0/monitor0/image-path"], "/old.png")
        for value in ("https://example.org/pic.png", "file://server/pic.png", "/missing/image.png"):
            with self.assertRaises(xfce.ApplyError):
                xfce.apply_wallpaper(value)

    def test_uninitialized_desktop_never_creates_fake_monitor(self):
        with (
            tempfile.NamedTemporaryFile() as image,
            self.assertRaisesRegex(xfce.ApplyError, "Desktop Settings"),
        ):
            xfce.apply_wallpaper(image.name)
        self.assertEqual(self.values, {})

    def test_workspace_with_only_style_gets_an_image(self):
        base = "/backdrop/screen0/monitorDP-1/workspace2"
        self.values[base + "/image-style"] = "0"
        with tempfile.NamedTemporaryFile() as image:
            xfce.apply_wallpaper(image.name)
            self.assertEqual(self.values[base + "/last-image"], image.name)

    def test_legacy_only_keys_cannot_report_false_success(self):
        self.values["/backdrop/screen0/monitor0/last-image"] = "/old.png"
        with tempfile.NamedTemporaryFile() as image, self.assertRaises(xfce.ApplyError):
            xfce.apply_wallpaper(image.name)

    def test_failed_transaction_restores_values_and_removes_new_properties(self):
        self.values["/old"] = "original"

        def fail(channel, *args):
            if "-s" in args and "/fail" in args:
                raise xfce.ApplyError("locked setting")
            return self.query(channel, *args)

        with (
            mock.patch.object(xfce, "query", side_effect=fail),
            self.assertRaisesRegex(xfce.ApplyError, "locked"),
        ):
            xfce.change(
                "test", {"/old": ("new", "string"), "/new": (True, "bool"), "/fail": (1, "int")}
            )
        self.assertEqual(self.values, {"/old": "original"})

    def test_panel_preset_and_undo_preserve_widgets_monitor_and_other_panels(self):
        self.values = {
            "/panels": "Value is an array with 2 items:\n\n1\n3",
            "/panels/panel-1/position": "p=6;x=2200;y=300",
            "/panels/panel-1/plugin-ids": "1\n2\n3",
            "/panels/panel-1/output-name": "DP-2",
            "/panels/panel-3/size": "48",
        }
        before = dict(self.values)
        self.assertEqual(xfce.panels(), [1, 3])
        undo = xfce.apply_panel(1, {"background-style": 0}, "Left bar")
        self.assertEqual(self.values["/panels/panel-1/position"], "p=7;x=2200;y=300")
        self.assertEqual(self.values["/panels/panel-1/mode"], "1")
        for key in (
            "/panels/panel-1/plugin-ids",
            "/panels/panel-1/output-name",
            "/panels/panel-3/size",
        ):
            self.assertEqual(self.values[key], before[key])
        xfce.restore(xfce.PANEL_CHANNEL, undo)
        self.assertEqual(self.values, before)

    def test_removed_panel_and_bad_input_do_not_write(self):
        self.values["/panels"] = "1"
        for panel, values in ((2, {}), (1, {"size": 1000}), (1, {"plugin-ids": 3})):
            with self.assertRaises(xfce.ApplyError):
                xfce.apply_panel(panel, values)
        self.assertEqual(self.values, {"/panels": "1"})

    def test_xfce_wallpaper_routing_and_sidebar(self):
        with (
            mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}),
            mock.patch.object(desktop.shutil, "which", return_value="/usr/bin/xfconf-query"),
            mock.patch.object(xfce, "apply_wallpaper", return_value=True) as apply,
        ):
            self.assertTrue(desktop.supported("wallpapers"))
            self.assertTrue(desktop.set_("wallpapers", "file:///tmp/image.png"))
            apply.assert_called_once_with("file:///tmp/image.png")
            self.assertTrue(app.Window.page_visible(mock.Mock(), "xfcepanel"))
        with mock.patch.object(desktop, "current_desktop", return_value="cinnamon"):
            self.assertFalse(app.Window.page_visible(mock.Mock(), "xfcepanel"))


class XfceCommandTest(unittest.TestCase):
    def test_live_outputs_and_workspaces_skip_disconnected_and_disabled(self):
        outputs = "Screen 0: minimum 320 x 200\neDP-1 connected primary 1920x1200+0+0\nDP-1 connected 1280x1024-1280+0\nHDMI-1 disconnected\nDP-2 connected (normal)"
        with (
            mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11"}),
            mock.patch.object(
                xfce.subprocess,
                "run",
                side_effect=[
                    mock.Mock(stdout=outputs),
                    mock.Mock(stdout="_NET_NUMBER_OF_DESKTOPS(CARDINAL) = 2"),
                ],
            ),
        ):
            targets = xfce.live_wallpaper_targets(set())
        self.assertEqual(
            set(targets),
            {
                f"/backdrop/screen0/monitor{monitor}/workspace{workspace}"
                for monitor in ("eDP-1", "DP-1")
                for workspace in (0, 1)
            },
        )

    def test_existing_types_are_preserved_and_new_values_are_typed(self):
        with mock.patch.object(
            xfce.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="")
        ) as run:
            xfce.write("test", "/length", 100, "double")
            self.assertEqual(
                run.call_args.args[0], ["xfconf-query", "-c", "test", "-p", "/length", "-s", "100"]
            )
            xfce.write("test", "/locked", True, "bool", False)
            self.assertEqual(run.call_args.args[0][-3:], ["-n", "-t", "bool"])
        for result in (OSError("missing"), subprocess.TimeoutExpired("xfconf-query", 5)):
            with (
                mock.patch.object(xfce.subprocess, "run", side_effect=result),
                self.assertRaises(xfce.ApplyError),
            ):
                xfce.read("test", "/key")

    def test_xfconf_failure_is_reported(self):
        with (
            mock.patch.object(
                xfce.subprocess,
                "run",
                return_value=mock.Mock(returncode=1, stdout="", stderr="permission denied"),
            ),
            self.assertRaisesRegex(xfce.ApplyError, "permission denied"),
        ):
            xfce.write("test", "/key", "value", "string")


class XfwmPreviewTest(unittest.TestCase):
    def test_artwork_preview_beats_gtk_and_invalidates_after_artwork_changes(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(previews, "CACHE", Path(temp) / "cache"),
            mock.patch.object(previews, "render_gtk_theme") as gtk,
        ):
            theme = Path(temp) / "Example"
            (theme / "xfwm4").mkdir(parents=True)
            (theme / "gtk-3.0").mkdir()
            pb = previews.GdkPixbuf.Pixbuf.new(previews.GdkPixbuf.Colorspace.RGB, True, 8, 24, 24)
            pb.fill(0xFF0000FF)
            asset = theme / "xfwm4" / "close-active.png"
            pb.savev(str(asset), "png", [], [])
            first = previews.theme_preview_path(theme, "xfwm")
            self.assertTrue(first.is_file())
            os.utime(asset, ns=(1, 1))
            second = previews.theme_preview_path(theme, "xfwm")
            self.assertNotEqual(first, second)
            gtk.assert_not_called()

    def test_missing_artwork_returns_no_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "xfwm4").mkdir()
            self.assertIsNone(previews.xfwm_preview(Path(temp)))
