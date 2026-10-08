"""Live-wallpaper lifecycle, kept outside the theme-browser process.

Only this process owns its renderers and capture children. A private control
socket marshals GTK mutations onto the main loop; Stop never kills other apps.
"""

import fcntl
import json
import os
import signal
import socketserver
import threading
import time

import gi

gi.require_version("GdkX11", "3.0")
from gi.repository import GdkX11

from . import (
    video_mpv,
    wallpaper_audio,
    wallpaper_desktop,
    wallpaper_sources,
    wallpaper_xscreensaver,
)
from . import video_wallpapers as videos
from .ui.gtk import Gdk, GLib, Gtk
from .wallpaper_surface import Surface


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
        self.renderer = video_mpv.PROFILES[0]
        self.source = "video"
        self.name = ""
        self.host = wallpaper_desktop.current()
        if self.host is None:
            raise videos.VideoError("No supported live-wallpaper desktop host is available.")
        self.options = wallpaper_sources.DEFAULTS.copy()
        self.audio = None
        self.audio_timer = None
        self.presentation = None
        display = Gdk.Display.get_default()
        display.connect("monitor-added", self._monitors_changed)
        display.connect("monitor-removed", self._monitors_changed)
        display.get_default_screen().connect("size-changed", self._monitors_changed)
        # Pause on lock without resuming a wallpaper the user paused manually.
        from .ui.gtk import Gio

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.subscription = self.bus.signal_subscribe(
            self.host.lock_service,
            self.host.lock_service,
            "ActiveChanged",
            None,
            None,
            Gio.DBusSignalFlags.NONE,
            self._lock_changed,
        )
        try:
            result = self.bus.call_sync(
                self.host.lock_service,
                self.host.lock_path,
                self.host.lock_service,
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
            "manual_paused": self.paused,
            "path": self.path,
            "fit": self.fit,
            "source": self.source,
            "name": self.name,
            "audio_inputs": [
                label
                for key, label in (("desktop_audio", "Desktop audio"), ("microphone", "Microphone"))
                if self.source == "audio" and self.options[key]
            ],
            "sources": sorted(wallpaper_sources.SOURCES),
            "audio_visuals": 1,
            "desktop_host": self.host.desktop_name,
            "mate_background": 1,
            "xfce_background": 1,
            "xfce_adapter": bool(
                getattr(self.host, "guardian", None) and self.host.guardian.process.poll() is None
            ),
            "message": self.error
            or (
                (
                    f"Using software playback for {self.host.label}’s desktop icon layer."
                    if self.host.copy_background
                    else "Using compatibility playback after a graphics-driver failure."
                )
                if self.source == "video"
                and self.renderer != "gpu"
                and self.state in {"starting", "playing", "paused"}
                else ""
            ),
            "renderer": self.renderer,
            "available": True,
        }

    def _clear(self):
        if self.presentation is not None:
            self.presentation.close()
            self.presentation = None
        if self.audio_timer is not None:
            GLib.source_remove(self.audio_timer)
            self.audio_timer = None
        if self.audio is not None:
            self.audio.close()
            self.audio = None
        for surface in self.surfaces:
            surface.close()
        self.surfaces.clear()

    def _stack_below_icons(self):
        return self.host.restack(self.surfaces)

    def _maintain_stacking(self):
        try:
            if self._stack_below_icons() and self.state in {"playing", "paused"}:
                self.host.reveal(self.surfaces)
        except (OSError, videos.VideoError) as exc:
            self._failed(str(exc))
        return True

    def _build(self):
        self._clear()
        display = Gdk.Display.get_default()
        try:
            if self.source == "audio":
                self.audio = wallpaper_audio.Session(
                    self.options["desktop_audio"], self.options["microphone"]
                )
            for number in range(display.get_n_monitors()):
                self.surfaces.append(
                    Surface(
                        display.get_monitor(number),
                        self.path,
                        self.fit,
                        number,
                        self.renderer,
                        self.source,
                        self.options,
                        self.audio,
                    )
                )
            if not self.surfaces:
                raise videos.VideoError("No monitor is available for video playback.")
        except (OSError, videos.VideoError):
            self._clear()
            raise
        self.state = "starting"
        self.deadline = time.monotonic() + 15
        if self.source == "audio":
            self.audio_timer = GLib.timeout_add(33, self._animate_audio)

    def _animate_audio(self):
        try:
            if self.audio is not None and self.state in {"playing", "starting"}:
                previous = self.audio.values
                self.audio.advance()
                moving = (
                    self.options.get("cycle_colors")
                    or self.path in {"ribbons", "particles", "spiral"}
                ) and any(self.audio.values)
                if (
                    self.state == "starting"
                    or moving
                    or any(abs(a - b) > 0.001 for a, b in zip(previous, self.audio.values))
                ):
                    for surface in self.surfaces:
                        surface.area.queue_draw()
                    if self.presentation is not None:
                        self.presentation.refresh()
        except (OSError, videos.VideoError) as exc:
            # _clear removes this timer too; mark it absent before returning.
            self.audio_timer = None
            self._failed(str(exc))
            return False
        return self.audio is not None

    def dispatch(self, data):
        action = data.get("action")
        if action == "play":
            if not videos.supported():
                raise videos.VideoError(
                    "Live wallpapers currently require Cinnamon, GNOME, MATE or Xfce on X11."
                )
            source = data.get("source", "video")
            options = data.get("options", {})
            if not isinstance(options, dict):
                raise videos.VideoError("Invalid wallpaper playback options.")
            fit = data.get("fit", "fill")
            path, options = videos.selection(data.get("path"), fit, source, **options)
            self.host.setup(data.get("allow_desktop_restart") is True)
            self.source, self.options = source, options
            if source == "xscreensaver":
                self.name = wallpaper_xscreensaver.validate(path)["label"]
            elif source == "audio":
                self.name = wallpaper_sources.STYLES[path]
            else:
                self.name = os.path.basename(path)
            self.path, self.fit, self.paused, self.error = path, fit, False, ""
            self.renderer = self.host.video_profiles()[0]
            try:
                self._build()
            except (OSError, videos.VideoError) as exc:
                self._failed(str(exc))
                raise
        elif action == "pause":
            if self.state not in {"playing", "paused", "starting"}:
                raise videos.VideoError("No live wallpaper is playing.")
            if not isinstance(data.get("paused"), bool):
                raise videos.VideoError("Invalid pause command.")
            self.paused = data["paused"]
            if self.state != "starting":
                self._set_pause()
        elif action == "stop":
            self._clear()
            self.state, self.path, self.error, self.name = "stopped", "", "", ""
        elif action == "restore_desktop":
            self._clear()
            self.host.close()
            self.state, self.path, self.error, self.name = "stopped", "", "", ""
        elif action == "quit":
            self._clear()
            self.host.close()
            self.state, self.path, self.error, self.name = "stopped", "", "", ""
            GLib.idle_add(Gtk.main_quit)
        elif action != "status":
            raise videos.VideoError("Unknown video wallpaper action.")
        return self.status()

    def _set_pause(self):
        pause = self.paused or self.locked
        if self.presentation is not None:
            self.presentation.pause(pause)
        if self.source == "audio":
            self.audio.pause(pause)
        else:
            for surface in self.surfaces:
                if self.source == "xscreensaver":
                    surface.pause_animation(pause)
                else:
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
        self.host.close()
        self.state, self.error = "error", message

    def _retry(self, reason):
        """Each play request may try each backend once; never crash-loop."""
        if self.source != "video":
            return False
        profiles = self.host.video_profiles()
        index = profiles.index(self.renderer) + 1
        if index == len(profiles):
            return False
        self.renderer = profiles[index]
        print(f"{reason} Retrying wallpaper with {self.renderer}.", flush=True)
        try:
            self._build()
        except (OSError, videos.VideoError) as exc:
            self._failed(str(exc))
        return True

    def _tick(self):
        try:
            self.host.check()
        except (OSError, ValueError, videos.VideoError) as exc:
            self._failed(str(exc))
            return True
        if self.state not in {"playing", "paused", "starting"}:
            return True
        if self.presentation is not None:
            try:
                self.presentation.ready()
            except (OSError, ValueError, videos.VideoError) as exc:
                self._failed(str(exc))
                return True
        for surface in self.surfaces:
            surface.limit_log()
        exits = [s.process.poll() for s in self.surfaces if s.process is not None]
        if any(code is not None for code in exits):
            reason = f"{self.source} player exited during playback (monitor exit codes: {exits})."
            if not self._retry(reason):
                self._failed(reason + " Check the logs in Drape's video runtime folder.")
        elif self.state == "starting":
            try:
                if self.source == "audio":
                    ready = self.audio.ready()
                elif self.source == "xscreensaver":
                    # Hacks have no mpv-style readiness IPC. Give the child a
                    # first rendering interval, then verify desktop ordering.
                    ready = all(time.monotonic() - s.started >= 1 for s in self.surfaces)
                else:
                    ready = all(
                        video_mpv.send(s.ipc, ["get_property", "video-params"])
                        for s in self.surfaces
                    )
                if ready and self._stack_below_icons():
                    if self.presentation is None:
                        self.presentation = self.host.present(self.surfaces, self.source)
                    prepared = self.presentation is None or self.presentation.ready()
                    if prepared:
                        self.host.reveal(self.surfaces)
                        self._set_pause()
            except (OSError, ValueError, videos.VideoError):
                pass  # sockets and decoded video parameters appear after process startup
            if self.state == "starting" and time.monotonic() > self.deadline:
                reason = "Wallpaper playback did not start within 15 seconds."
                if not self._retry(reason):
                    self._failed(reason)
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
            if getattr(self.server, "stopping", False):
                if data.get("action") not in {"status", "stop", "quit"}:
                    raise videos.VideoError("The wallpaper player is shutting down. Try again.")
                self.wfile.write(
                    json.dumps(
                        {
                            "state": "stopped",
                            "path": "",
                            "fit": "fill",
                            "available": True,
                            "stopping": True,
                        }
                    ).encode()
                    + b"\n"
                )
                return
            event, reply = threading.Event(), {}

            def dispatch():
                try:
                    reply.update(self.server.player.dispatch(data))
                    if data.get("action") == "quit":
                        self.server.stopping = True
                except Exception as exc:  # noqa: BLE001 - keep the control loop alive
                    reply.update(error=str(exc))
                finally:
                    event.set()
                return False

            GLib.idle_add(dispatch)
            deadline = time.monotonic() + 10
            while not event.wait(0.1):
                if self.server.stopping:
                    raise videos.VideoError("The wallpaper player is shutting down. Try again.")
                if time.monotonic() > deadline:
                    raise videos.VideoError("Wallpaper player is not responding.")
        except (OSError, ValueError, videos.VideoError) as exc:
            reply = {"error": str(exc)}
        try:
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass


def main():
    if not videos.supported():
        raise videos.VideoError(
            "Live wallpapers currently require Cinnamon, GNOME, MATE or Xfce on X11."
        )
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
                control.stopping = False
                thread = threading.Thread(target=control.serve_forever, daemon=True)
                thread.start()

                def shutdown():
                    control.stopping = True
                    Gtk.main_quit()
                    return False

                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
                try:
                    Gtk.main()
                finally:
                    control.shutdown()
        finally:
            player._clear()
            player.host.close()
            player.bus.signal_unsubscribe(player.subscription)
            socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
