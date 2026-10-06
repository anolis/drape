"""Caja background presentation with crash-safe restoration in a separate process."""

import json
import os
import select
import signal
import subprocess
import sys
import time

from .video_wallpapers import VideoError


class Mirror:
    def __init__(self, surfaces, source):
        windows = []
        for surface in surfaces:
            scale = surface.window.get_scale_factor()
            xx, yy = surface.window.get_position()
            windows.append([surface.area.get_window().get_xid(), xx * scale, yy * scale])
        self.process = subprocess.Popen(
            [sys.executable, "-m", "drape.wallpaper_mate", json.dumps(windows), source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.buffer = b""
        self.prepared = False

    def ready(self):
        while True:
            try:
                chunk = os.read(self.process.stdout.fileno(), 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            self.buffer += chunk
            if len(self.buffer) > 8192:
                raise VideoError("MATE's background helper returned an invalid response.")
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            response = json.loads(line)
            if response.get("error"):
                raise VideoError(response["error"])
            self.prepared |= response.get("ready", False)
        if self.process.poll() is not None:
            raise VideoError("MATE's background helper stopped. The static wallpaper was restored.")
        return self.prepared

    def _send(self, command):
        try:
            os.write(self.process.stdin.fileno(), command + b"\n")
        except BlockingIOError:
            pass  # Frame invalidations may coalesce; never stall GTK.
        except BrokenPipeError as exc:
            raise VideoError("MATE's background helper stopped.") from exc

    def pause(self, paused):
        self._send(b"pause" if paused else b"resume")

    def refresh(self):
        self._send(b"draw")

    def close(self):
        # EOF also restores the background if the player crashes or is killed.
        self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()


def run(surfaces, source):
    from .wallpaper_mate_x11 import X11, Background

    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    x11 = background = None
    try:
        x11 = X11()
        background = Background(x11, surfaces)
        # Redirected windows need a rendering interval before their first copy.
        next_frame = time.monotonic() + 0.1
        next_check = next_frame
        print(json.dumps({"ready": True}), flush=True)
        paused, dirty, pending = False, True, b""
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
                    raise ValueError("Invalid MATE wallpaper commands.")
                lines = pending.split(b"\n")
                pending = lines.pop()
                for command in lines:
                    if command == b"pause":
                        paused = True
                    elif command == b"resume":
                        paused, dirty = False, True
                    elif command == b"draw":
                        dirty = True
            now = time.monotonic()
            if now >= next_check:
                if background.caja and not background.unchanged():
                    raise VideoError(
                        "MATE’s wallpaper changed outside Drape. Live playback has stopped."
                    )
                next_check = now + 1
            if now >= next_frame:
                if not paused and (source != "audio" or dirty):
                    background.draw()
                    dirty = False
                next_frame = now + 1 / 30
    except Exception as exc:  # noqa: BLE001 - helper boundary; restore in finally.
        print(json.dumps({"error": str(exc)}), flush=True)
    finally:
        try:
            if background is not None:
                background.close()
        finally:
            if x11 is not None:
                x11.close()


if __name__ == "__main__":
    run(json.loads(sys.argv[1]), sys.argv[2])
