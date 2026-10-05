"""Cinnamon/X11 video surfaces, kept outside the theme-browser process.

Only this process owns its mpv children. A private control socket marshals all
GTK mutations onto the main loop; Stop never kills unrelated media players.
"""

import fcntl
import json
import os
import signal
import socketserver
import subprocess
import threading
import time

import cairo
import gi

gi.require_version("GdkX11", "3.0")
from gi.repository import GdkX11

from . import video_mpv, video_x11
from . import video_wallpapers as videos
from .ui.gtk import Gdk, GLib, Gtk


class Surface:
    def __init__(self, monitor, path, fit, number):
        self.ipc = videos.runtime_dir() / f"mpv-{number}.sock"
        self.ipc.unlink(missing_ok=True)
        self.process = None
        self.window = Gtk.Window(title="Drape Video Wallpaper")
        self.window.set_wmclass("drape-video-wallpaper", "DrapeVideoWallpaper")
        self.window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.window.set_decorated(False)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_keep_below(True)
        # Do not expose a black/new topmost desktop surface while Muffin is
        # mapping it. Reveal only after decoding and ordering beneath Nemo.
        self.window.set_opacity(0)
        self.window.stick()
        area = Gtk.DrawingArea()
        self.window.add(area)
        rect = monitor.get_geometry()
        self.window.move(rect.x, rect.y)
        self.window.set_default_size(rect.width, rect.height)
        self.window.show_all()
        # Keep desktop clicks and drags available to Nemo, including when an mpv
        # child covers the entire surface. The panel and icons stay above us.
        self.window.get_window().input_shape_combine_region(cairo.Region(), 0, 0)
        self.window.get_window().lower()
        try:
            self.process = subprocess.Popen(
                video_mpv.command(path, area.get_window().get_xid(), self.ipc, fit),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self.close()
            raise

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        self.window.destroy()
        self.ipc.unlink(missing_ok=True)


class Player:
    def __init__(self):
        self.surfaces = []
        self.state = "stopped"
        self.path = ""
        self.fit = "fill"
        self.error = ""
        self.paused = False
        self.locked = False
        self.deadline = 0
        display = Gdk.Display.get_default()
        display.connect("monitor-added", self._monitors_changed)
        display.connect("monitor-removed", self._monitors_changed)
        display.get_default_screen().connect("size-changed", self._monitors_changed)
        # Pause on lock without resuming a wallpaper the user paused manually.
        from .ui.gtk import Gio

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.subscription = self.bus.signal_subscribe(
            "org.cinnamon.ScreenSaver",
            "org.cinnamon.ScreenSaver",
            "ActiveChanged",
            None,
            None,
            Gio.DBusSignalFlags.NONE,
            self._lock_changed,
        )
        try:
            result = self.bus.call_sync(
                "org.cinnamon.ScreenSaver",
                "/org/cinnamon/ScreenSaver",
                "org.cinnamon.ScreenSaver",
                "GetActive",
                None,
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            self.locked = result.unpack()[0]
        except GLib.Error:
            pass
        GLib.timeout_add(300, self._tick)
        GLib.timeout_add_seconds(1, self._maintain_stacking)

    def status(self):
        return {
            "state": self.state,
            "path": self.path,
            "fit": self.fit,
            "message": self.error,
            "available": True,
        }

    def _clear(self):
        for surface in self.surfaces:
            surface.close()
        self.surfaces.clear()

    def _stack_below_icons(self):
        """Muffin puts new DESKTOP windows above existing Nemo surfaces.

        GDK requests are ignored when the focused app has newer user-time.
        Use the EWMH desktop-manager request and verify its result before making
        a video visible. Recheck when Nemo or the monitor layout changes.
        """
        if not self.surfaces:
            return True
        owned = {surface.window.get_window().get_xid() for surface in self.surfaces}
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
            anchor_index = stack.index(anchor)
            above_icons = {window.get_xid() for window in stack[anchor_index + 1 :]}
            for surface in self.surfaces:
                window = surface.window.get_window()
                if window.get_xid() in above_icons:
                    surface.window.set_opacity(0)
                    video_x11.restack_below(window.get_xid(), anchor.get_xid())
                    correct = False
        return correct

    def _maintain_stacking(self):
        try:
            if self._stack_below_icons() and self.state in {"playing", "paused"}:
                for surface in self.surfaces:
                    surface.window.set_opacity(1)
        except (OSError, videos.VideoError) as exc:
            self._failed(str(exc))
        return True

    def _build(self):
        self._clear()
        display = Gdk.Display.get_default()
        try:
            for number in range(display.get_n_monitors()):
                self.surfaces.append(
                    Surface(display.get_monitor(number), self.path, self.fit, number)
                )
            if not self.surfaces:
                raise videos.VideoError("No monitor is available for video playback.")
        except (OSError, videos.VideoError):
            self._clear()
            raise
        self.state = "starting"
        self.deadline = time.monotonic() + 15

    def dispatch(self, data):
        action = data.get("action")
        if action == "play":
            if not videos.supported():
                raise videos.VideoError("Video wallpapers currently require Cinnamon on X11.")
            path = videos.validate_video(data.get("path"))
            fit = data.get("fit")
            if fit not in {"fill", "fit"}:
                raise videos.VideoError("Unknown video layout.")
            self.path, self.fit, self.paused, self.error = path, fit, False, ""
            try:
                self._build()
            except (OSError, videos.VideoError) as exc:
                self._failed(str(exc))
                raise
        elif action == "pause":
            if self.state not in {"playing", "paused", "starting"}:
                raise videos.VideoError("No video wallpaper is playing.")
            if not isinstance(data.get("paused"), bool):
                raise videos.VideoError("Invalid pause command.")
            self.paused = data["paused"]
            if self.state != "starting":
                self._set_pause()
        elif action == "stop":
            self._clear()
            self.state, self.path, self.error = "stopped", "", ""
        elif action == "quit":
            self._clear()
            self.state, self.path, self.error = "stopped", "", ""
            GLib.idle_add(Gtk.main_quit)
        elif action != "status":
            raise videos.VideoError("Unknown video wallpaper action.")
        return self.status()

    def _set_pause(self):
        pause = self.paused or self.locked
        for surface in self.surfaces:
            video_mpv.send(surface.ipc, ["set_property", "pause", pause])
        self.state = "paused" if pause else "playing"

    def _lock_changed(self, _bus, _sender, _path, _interface, _signal, parameters):
        self.locked = parameters.unpack()[0]
        if self.state in {"playing", "paused"}:
            try:
                self._set_pause()
            except (OSError, ValueError, videos.VideoError) as exc:
                self._failed(str(exc))

    def _failed(self, message):
        self._clear()
        self.state, self.error = "error", message

    def _tick(self):
        if self.state not in {"playing", "paused", "starting"}:
            return True
        if any(s.process.poll() is not None for s in self.surfaces):
            self._failed("mpv could not play this video. Check the file and your video drivers.")
        elif self.state == "starting":
            try:
                ready = all(
                    video_mpv.send(s.ipc, ["get_property", "video-params"]) for s in self.surfaces
                )
                if ready and self._stack_below_icons():
                    for surface in self.surfaces:
                        surface.window.set_opacity(1)
                    self._set_pause()
            except (OSError, ValueError, videos.VideoError):
                pass  # sockets and decoded video parameters appear after process startup
            if self.state == "starting" and time.monotonic() > self.deadline:
                self._failed("Video playback did not start within 15 seconds.")
        return True

    def _monitors_changed(self, *_):
        if self.state in {"playing", "paused", "starting"}:
            try:
                self._build()
            except (OSError, videos.VideoError) as exc:
                self._failed(str(exc))


# One bounded connection at a time keeps request floods from spawning threads.
# The socket thread does not touch GTK; the main loop dispatches every command.


class Control(socketserver.UnixStreamServer):
    allow_reuse_address = True


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        try:
            raw = self.rfile.readline(videos.LIMIT + 1)
            if len(raw) > videos.LIMIT or not raw.endswith(b"\n"):
                raise videos.VideoError("Invalid wallpaper request.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise videos.VideoError("Invalid wallpaper request.")
            event, reply = threading.Event(), {}

            def dispatch():
                try:
                    reply.update(self.server.player.dispatch(data))
                except Exception as exc:  # noqa: BLE001 - keep the control loop alive
                    reply.update(error=str(exc))
                finally:
                    event.set()
                return False

            GLib.idle_add(dispatch)
            if not event.wait(10):
                raise videos.VideoError("Wallpaper player is not responding.")
        except (OSError, ValueError, videos.VideoError) as exc:
            reply = {"error": str(exc)}
        try:
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass


def main():
    if not videos.supported():
        raise videos.VideoError("Video wallpapers currently require Cinnamon on X11.")
    directory = videos.runtime_dir()
    # Held for the worker's entire lifetime; a stale socket never owns a player.
    with (directory / "player.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        Gtk.init([])
        if not isinstance(Gdk.Display.get_default(), GdkX11.X11Display):
            raise videos.VideoError("Video wallpapers require an X11 display.")
        socket_path = directory / "control.sock"
        socket_path.unlink(missing_ok=True)
        player = Player()
        try:
            with Control(str(socket_path), Handler) as control:
                os.chmod(socket_path, 0o600)
                control.player = player
                thread = threading.Thread(target=control.serve_forever, daemon=True)
                thread.start()
                GLib.unix_signal_add(
                    GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (Gtk.main_quit(), False)[1]
                )
                GLib.unix_signal_add(
                    GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (Gtk.main_quit(), False)[1]
                )
                try:
                    Gtk.main()
                finally:
                    control.shutdown()
        finally:
            player._clear()
            player.bus.signal_unsubscribe(player.subscription)
            socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
