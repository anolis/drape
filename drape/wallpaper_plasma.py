"""Native Plasma wallpaper lease and bounded local frame presentation.

Plasma keeps its desktop, icons, widgets and panels. A wallpaper plugin displays
frames rendered offscreen; a separate helper restores the original plugins on
Stop or a lost player connection. User-selected replacements are never undone.
"""

import json
import os
import secrets
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import kde
from .records import _atomic_write, file_lock
from .video_wallpapers import VideoError
from .wallpaper_background import Mirror
from .wallpaper_frame_server import Capture, Frames

PLUGIN = "org.anolis.drape.wallpaper"
PACKAGE = Path(__file__).parent / "plasma_wallpaper"
STATE = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "drape/plasma-wallpaper.json"
)


def evaluate(script):
    from .ui.gtk import GLib

    return kde._dbus(
        "org.kde.plasmashell",
        "/PlasmaShell",
        "org.kde.PlasmaShell",
        "evaluateScript",
        GLib.Variant("(s)", (script,)),
    )[0]


def install():
    """Install only Drape's package; never rewrite a desktop layout or launcher."""
    target = kde.DATA_HOME / "plasma/wallpapers" / PLUGIN
    for source in PACKAGE.rglob("*"):
        if source.is_file():
            destination = target / source.relative_to(PACKAGE)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file() or destination.read_bytes() != source.read_bytes():
                _atomic_write(destination, source.read_text())


def snapshot():
    return json.loads(
        evaluate("""
        var rows = [], ds = desktopsForActivity(currentActivity());
        for (var i = 0; i < ds.length; i++) {
            var d = ds[i];
            if (d.screen < 0) continue;
            d.currentConfigGroup = ["Wallpaper", "org.anolis.drape.wallpaper", "General"];
            var g = screenGeometry(d.screen);
            rows.push({id:d.id, plugin:d.wallpaperPlugin, lease:d.readConfig("Lease", ""),
                       geometry:[g.x,g.y,g.width,g.height]});
        }
        print(JSON.stringify(rows));
    """)
    )


def restore():
    """Restore only still-owned desktops, preserving external wallpaper changes."""
    with file_lock(STATE.with_suffix(".lock")):
        if not STATE.exists():
            return
        data = json.loads(STATE.read_text())
        evaluate(
            f"""
            var data = {json.dumps(data)};
            for (var i = 0; i < data.desktops.length; i++) {{
                var row = data.desktops[i], d = desktopById(row.id);
                if (!d || d.wallpaperPlugin !== {json.dumps(PLUGIN)}) continue;
                d.currentConfigGroup = ["Wallpaper", {json.dumps(PLUGIN)}, "General"];
                if (d.readConfig("Lease", "") !== data.lease) continue;
                d.wallpaperPlugin = row.plugin;
            }}
        """
        )
        STATE.unlink()


class Lease:
    def __init__(self, endpoints):
        restore()  # Recover a lease left by a killed helper or previous login.
        self.lease = secrets.token_hex(16)
        rows = snapshot()
        if not rows:
            raise VideoError("No active Plasma desktop is available.")
        for row in rows:
            # Geometry rather than screen number matches GTK and Plasma even
            # when primary monitor ordering differs between their toolkits.
            matches = [
                index
                for index, (rect, _) in enumerate(endpoints)
                if row["geometry"][:2] == rect[:2]
            ]
            if not matches:
                raise VideoError(
                    "Plasma and Drape disagree about the monitor layout. Retry after configuring displays."
                )
            row["endpoint"] = endpoints[matches[0]][1]
        self.rows = rows
        with file_lock(STATE.with_suffix(".lock")):
            STATE.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(STATE, json.dumps({"lease": self.lease, "desktops": rows}))
            # Write the lease before switching plugins, allowing rollback after
            # partial application or an unexpected helper termination.
            evaluate(
                """
                var data = {};
                for (var i = 0; i < data.desktops.length; i++) {{
                    var row = data.desktops[i], d = desktopById(row.id);
                    if (!d || d.wallpaperPlugin !== row.plugin) throw "Wallpaper changed during activation";
                    d.currentConfigGroup = ["Wallpaper", {}, "General"];
                    d.writeConfig("Lease", data.lease);
                    d.writeConfig("Endpoint", row.endpoint);
                    d.wallpaperPlugin = {};
                }}
            """.format(
                    json.dumps({"lease": self.lease, "desktops": rows}),
                    json.dumps(PLUGIN),
                    json.dumps(PLUGIN),
                )
            )

    def unchanged(self):
        active = {row["id"]: row for row in snapshot()}
        return all(
            row["id"] in active
            and active[row["id"]]["plugin"] == PLUGIN
            and active[row["id"]]["lease"] == self.lease
            for row in self.rows
        )

    def close(self):
        restore()


def check_ownership(lease, paused):
    """Return the next check delay without treating an unavailable shell as a change."""
    from .ui.gtk import Gio, GLib

    # The shell may stop servicing scripting calls while the screen is locked.
    # Playback and ownership checks can wait together until the player resumes.
    if paused:
        return 1
    try:
        unchanged = lease.unchanged()
    except GLib.Error as exc:
        if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT) or any(
            exc.matches(Gio.dbus_error_quark(), code)
            for code in (Gio.DBusError.NO_REPLY, Gio.DBusError.TIMEOUT)
        ):
            # A timeout supplies no ownership evidence. Keep the lease and try
            # again slowly, including when unlock races a query already in flight.
            return 5
        raise
    if not unchanged:
        raise VideoError("The Plasma wallpaper changed outside Drape. Playback stopped.")
    return 1


class Presentation(Mirror):
    def __init__(self, surfaces, source):
        windows = []
        for surface in surfaces:
            rect = surface.monitor.get_geometry()
            scale = surface.window.get_scale_factor()
            windows.append(
                [
                    surface.area.get_window().get_xid(),
                    [rect.x, rect.y, rect.width, rect.height],
                    scale,
                ]
            )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "drape.wallpaper_plasma", json.dumps(windows), source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.buffer, self.prepared = b"", False

    def close(self):
        disconnected = self.process.poll() is not None
        super().close()
        if disconnected:
            subprocess.run(
                [sys.executable, "-m", "drape.wallpaper_plasma", "--restore"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )


def run(windows, source):
    from .wallpaper_x11 import X11

    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    x11 = capture = server = lease = None
    try:
        x11 = X11()
        capture = Capture(x11, windows)
        server = Frames(len(windows))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # Capture before activating the native plugin, avoiding an empty first frame.
        for i in range(len(windows)):
            server.frames[i] = (0, capture.read(i))
        base = f"http://127.0.0.1:{server.server_port}/{server.token}"
        lease = Lease([(row[1], f"{base}/{i}") for i, row in enumerate(windows)])
        print(json.dumps({"ready": True}), flush=True)
        paused, dirty, pending, sequence = False, True, b"", 0
        next_frame = next_check = time.monotonic()
        while not stopped:
            now = time.monotonic()
            readable, _, _ = select.select(
                [sys.stdin.buffer], [], [], max(0, min(0.1, next_frame - now))
            )
            if readable:
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 8192:
                    raise VideoError("Invalid wallpaper commands.")
                lines = pending.split(b"\n")
                pending = lines.pop()
                for command in lines:
                    if command == b"pause":
                        paused = True
                    elif command == b"resume":
                        paused, dirty = False, True
                        next_check = time.monotonic() + 3
                    elif command == b"draw":
                        dirty = True
            now = time.monotonic()
            if now >= next_check:
                next_check = now + check_ownership(lease, paused)
            if now >= next_frame:
                if not paused and (source != "audio" or dirty):
                    sequence += 1
                    for i in range(len(windows)):
                        server.frames[i] = (sequence, capture.read(i))
                    dirty = False
                next_frame = now + 1 / 20
    except Exception as exc:  # noqa: BLE001 - helper boundary; restore in finally.
        print(json.dumps({"error": str(exc)}), flush=True)
    finally:
        try:
            # Also covers partially applied Lease construction.
            restore()
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if capture is not None:
                capture.close()
            if x11 is not None:
                x11.close()


if __name__ == "__main__":
    if sys.argv[1] == "--restore":
        restore()
    else:
        run(json.loads(sys.argv[1]), sys.argv[2])
