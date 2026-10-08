"""Plasma host selection, native lease rollback and private frame transport."""

import ctypes as c
import http.client
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from drape import desktop, kde, wallpaper_desktop
from drape import wallpaper_frame_server as frames
from drape import wallpaper_plasma as plasma
from drape.ui.gtk import Gio, GLib
from drape.video_wallpapers import VideoError


class OwnershipTests(unittest.TestCase):
    def test_paused_playback_does_not_query_the_shell(self):
        lease = mock.Mock()
        self.assertEqual(plasma.check_ownership(lease, True), 1)
        lease.unchanged.assert_not_called()

    def test_timeout_retries_without_restoring_or_invalidating_the_lease(self):
        for domain, code in (
            (Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT),
            (Gio.dbus_error_quark(), Gio.DBusError.NO_REPLY),
            (Gio.dbus_error_quark(), Gio.DBusError.TIMEOUT),
        ):
            with self.subTest(code=code):
                error = GLib.Error.new_literal(domain, "Shell is temporarily unavailable", code)
                lease = mock.Mock()
                lease.unchanged.side_effect = [error, True, False]
                self.assertEqual(plasma.check_ownership(lease, False), 5)
                self.assertEqual(plasma.check_ownership(lease, False), 1)
                with self.assertRaisesRegex(VideoError, "changed outside Drape"):
                    plasma.check_ownership(lease, False)
                lease.close.assert_not_called()

    def test_other_script_errors_are_still_reported(self):
        error = GLib.Error.new_literal(
            Gio.dbus_error_quark(), "Denied", Gio.DBusError.ACCESS_DENIED
        )
        lease = mock.Mock()
        lease.unchanged.side_effect = error
        with self.assertRaises(GLib.Error):
            plasma.check_ownership(lease, False)


class HostTests(unittest.TestCase):
    def test_plasma6_x11_uses_standard_screen_locker_and_rejects_wayland(self):
        with (
            mock.patch.object(desktop, "current_desktop", return_value="kde"),
            mock.patch.dict(
                os.environ,
                DISPLAY=":1",
                XDG_SESSION_TYPE="x11",
                WAYLAND_DISPLAY="",
                KDE_SESSION_VERSION="6",
            ),
        ):
            host = wallpaper_desktop.current()
            self.assertIsInstance(host, wallpaper_desktop.PlasmaX11)
            self.assertEqual(host.lock_service, "org.freedesktop.ScreenSaver")
            self.assertEqual(host.lock_path, "/ScreenSaver")
            with mock.patch.dict(os.environ, WAYLAND_DISPLAY="wayland-0"):
                self.assertIsNone(wallpaper_desktop.current())
            with mock.patch.dict(os.environ, KDE_SESSION_VERSION="5"):
                self.assertIsNone(wallpaper_desktop.current())

    def test_setup_installs_only_the_native_wallpaper_package(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(kde, "DATA_HOME", Path(directory)),
        ):
            wallpaper_desktop.PlasmaX11().setup()
            target = Path(directory) / "plasma/wallpapers" / plasma.PLUGIN
            self.assertEqual(
                json.loads((target / "metadata.json").read_text())["KPackageStructure"],
                "Plasma/Wallpaper",
            )
            self.assertTrue((target / "contents/ui/main.qml").is_file())
            self.assertEqual(list(Path(directory).iterdir()), [Path(directory) / "plasma"])


@unittest.skipUnless(
    shutil.which("node"), "Node is needed to execute Plasma's native scripts in the fixture"
)
class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patch = mock.patch.object(plasma, "STATE", Path(self.temporary.name) / "lease.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.rows = [
            {"id": 1, "plugin": "org.kde.image", "geometry": [1200, 420, 1755, 987], "lease": ""},
            {"id": 2, "plugin": "org.kde.color", "geometry": [0, 0, 1097, 1755], "lease": ""},
        ]
        self.endpoints = [
            ([0, 0, 1200, 1920], "http://private/portrait"),
            ([1200, 420, 1920, 1080], "http://private/landscape"),
        ]
        self.patch = mock.patch.object(plasma, "evaluate", side_effect=self.evaluate)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.fail_application = False

    def evaluate(self, script):
        # Execute the actual emitted JavaScript against the documented Plasma
        # API, so wrong IDs, lease ownership and partial rollback are exercised.
        prelude = (
            "const rows="
            + json.dumps(self.rows)
            + "; const fail="
            + json.dumps(self.fail_application)
            + ";\n"
            + r"""
        for (const row of rows) {
            row.wallpaperPlugin=row.plugin;
            row.readConfig=function(key,fallback){return key==='Lease'?this.lease:this[key]??fallback;};
            row.writeConfig=function(key,value){if(key==='Lease')this.lease=value;else this[key]=value;};
        }
        function desktopById(id) {
            const row=rows.find(row=>row.id===id);
            if(fail && id===2 && script.includes('Wallpaper changed during activation'))throw Error('interrupted');
            return row;
        }
        function desktopsForActivity(){return rows.map((row,index)=>Object.assign(row,{screen:index}));}
        function currentActivity(){return 'current';}
        function screenGeometry(screen){const g=rows[screen].geometry;return {x:g[0],y:g[1],width:g[2],height:g[3]};}
        let output=''; function print(text){output+=text;}
        """
        )
        program = (
            prelude
            + "\nconst script="
            + json.dumps(script)
            + r""";
        let error=''; try {(function(){eval(script);})();} catch(exc){error=String(exc);}
        for(const row of rows)row.plugin=row.wallpaperPlugin;
        console.log(JSON.stringify({rows,output,error}));
        """
        )
        result = subprocess.run(
            [shutil.which("node"), "-e", program],
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        state = json.loads(result.stdout)
        self.rows = state["rows"]
        if state["error"]:
            raise VideoError(state["error"])
        return state["output"]

    def test_per_screen_plugins_and_fractional_geometry_are_restored(self):
        lease = plasma.Lease(self.endpoints)
        self.assertEqual(self.rows[0]["Endpoint"], "http://private/landscape")
        self.assertEqual(self.rows[1]["Endpoint"], "http://private/portrait")
        self.assertTrue(lease.unchanged())
        lease.close()
        self.assertEqual([row["plugin"] for row in self.rows], ["org.kde.image", "org.kde.color"])
        self.assertFalse(plasma.STATE.exists())

    def test_external_replacement_is_preserved_while_other_screen_restores(self):
        lease = plasma.Lease(self.endpoints)
        self.rows[0]["plugin"] = "org.kde.slideshow"
        self.assertFalse(lease.unchanged())
        lease.close()
        self.assertEqual(
            [row["plugin"] for row in self.rows], ["org.kde.slideshow", "org.kde.color"]
        )

    def test_partial_application_can_be_recovered_from_durable_backup(self):
        self.fail_application = True
        with self.assertRaisesRegex(VideoError, "interrupted"):
            plasma.Lease(self.endpoints)
        self.assertEqual(self.rows[0]["plugin"], plasma.PLUGIN)
        self.assertTrue(plasma.STATE.exists())
        self.fail_application = False
        plasma.restore()
        self.assertEqual([row["plugin"] for row in self.rows], ["org.kde.image", "org.kde.color"])

    def test_foreign_lease_is_not_overwritten(self):
        lease = plasma.Lease(self.endpoints)
        self.rows[0]["lease"] = "new-owner"
        lease.close()
        self.assertEqual(self.rows[0]["plugin"], plasma.PLUGIN)
        self.assertEqual(self.rows[1]["plugin"], "org.kde.color")

    def test_mismatched_layout_does_not_switch_any_wallpaper(self):
        with self.assertRaisesRegex(VideoError, "monitor layout"):
            plasma.Lease([([500, 500, 100, 100], "http://private/invalid")])
        self.assertFalse(plasma.STATE.exists())
        self.assertEqual([row["plugin"] for row in self.rows], ["org.kde.image", "org.kde.color"])


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.server = frames.Frames(1)
        self.server.frames[0] = (42, b"jpeg-bytes")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_private_token_is_required_and_bad_routes_are_rejected(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for path in (
            "/wrong/0/status",
            f"/{self.server.token}/1/status",
            f"/{self.server.token}/../status",
        ):
            self.assertEqual(self.get(path)[0], 404)

    def test_status_and_frame_share_a_sequence_without_browser_caching(self):
        path = f"/{self.server.token}/0/"
        code, body, _ = self.get(path + "status")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"sequence": 42})
        code, body, headers = self.get(path + "frame.jpg?sequence=42")
        self.assertEqual((code, body), (200, b"jpeg-bytes"))
        self.assertEqual(headers["Cache-Control"], "no-store")


class CaptureTests(unittest.TestCase):
    def test_maskless_pixmap_is_decoded_as_rgb_and_released(self):
        capture = object.__new__(frames.Capture)
        capture.x = mock.Mock()
        capture.frames = [(123, 2, 1)]
        buffer = c.create_string_buffer(bytes([0, 0, 255, 0] * 2))
        image = frames.XImage(
            width=2, height=1, data=c.cast(buffer, c.c_void_p), bytes_per_line=8, bits_per_pixel=32
        )
        pointer = c.pointer(image)
        capture.x.lib.XGetImage.return_value = pointer
        decoded = Image.open(io.BytesIO(capture.read(0)))
        r, g, b = decoded.getpixel((0, 0))
        self.assertGreater(r, 245)
        self.assertLess(max(g, b), 10)
        capture.x.lib.XDestroyImage.assert_called_once_with(pointer)

    def test_unsupported_format_still_releases_the_ximage(self):
        capture = object.__new__(frames.Capture)
        capture.x = mock.Mock()
        capture.frames = [(123, 2, 1)]
        pointer = c.pointer(frames.XImage(bits_per_pixel=16))
        capture.x.lib.XGetImage.return_value = pointer
        with self.assertRaisesRegex(VideoError, "pixel format"):
            capture.read(0)
        capture.x.lib.XDestroyImage.assert_called_once_with(pointer)
