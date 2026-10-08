"""Bounded X11 render capture and local frame transport for native wallpapers.

Only the helper's main thread accesses Xlib. HTTP clients receive immutable
frame copies through a loopback-only server with a private capability URL.
"""

import ctypes as c
import io
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from .video_wallpapers import VideoError


class XImage(c.Structure):
    """Public Xlib XImage prefix; the function table following it is not used."""

    _fields_ = [
        ("width", c.c_int),
        ("height", c.c_int),
        ("xoffset", c.c_int),
        ("format", c.c_int),
        ("data", c.c_void_p),
        ("byte_order", c.c_int),
        ("bitmap_unit", c.c_int),
        ("bitmap_bit_order", c.c_int),
        ("bitmap_pad", c.c_int),
        ("depth", c.c_int),
        ("bytes_per_line", c.c_int),
        ("bits_per_pixel", c.c_int),
        ("red_mask", c.c_ulong),
        ("green_mask", c.c_ulong),
        ("blue_mask", c.c_ulong),
    ]


class Capture:
    def __init__(self, x11, windows):
        self.x, self.frames, self.redirected = x11, [], []
        x11.lib.XGetImage.argtypes = [
            c.c_void_p,
            c.c_ulong,
            c.c_int,
            c.c_int,
            c.c_uint,
            c.c_uint,
            c.c_ulong,
            c.c_int,
        ]
        x11.lib.XGetImage.restype = c.POINTER(XImage)
        x11.lib.XDestroyImage.argtypes = [c.POINTER(XImage)]
        try:
            for window, _, _ in windows:
                width, height, _ = x11.geometry(window)
                if width * height > 40_000_000:
                    raise VideoError("The wallpaper render surface is too large.")
                x11.composite.XCompositeRedirectWindow(x11.display, window, 0)
                self.redirected.append(window)
                pixmap = x11.composite.XCompositeNameWindowPixmap(x11.display, window)
                self.frames.append((pixmap, width, height))
            x11.sync()
        except Exception:
            self.close()
            raise

    def read(self, index):
        pixmap, width, height = self.frames[index]
        pointer = self.x.lib.XGetImage(
            self.x.display, pixmap, 0, 0, width, height, c.c_ulong(-1).value, 2
        )
        if not pointer:
            self.x.sync()
            raise VideoError("Cannot read Plasma's wallpaper render buffer.")
        try:
            self.x.sync()
            info = pointer.contents
            if info.bits_per_pixel != 32 or (
                info.red_mask,
                info.green_mask,
                info.blue_mask,
            ) not in {(0, 0, 0), (0xFF0000, 0xFF00, 0xFF)}:
                raise VideoError("This X11 pixel format is unsupported for Plasma live wallpapers.")
            # XGetImage leaves color masks empty for pixmaps. GTK's true-color
            # X11 render surfaces use the same RGB channel layout as the display.
            pixels = c.string_at(info.data, info.bytes_per_line * height)
            mode = "BGRX" if info.byte_order == 0 else "XRGB"
            image = Image.frombytes(
                "RGB", (width, height), pixels, "raw", mode, info.bytes_per_line
            )
            result = io.BytesIO()
            image.save(result, format="JPEG", quality=88)
            return result.getvalue()
        finally:
            self.x.lib.XDestroyImage(pointer)

    def close(self):
        for pixmap, *_ in self.frames:
            self.x.lib.XFreePixmap(self.x.display, pixmap)
        for window in self.redirected:
            self.x.composite.XCompositeUnredirectWindow(self.x.display, window, 0)
        self.frames.clear()
        self.redirected.clear()
        self.x.lib.XSync(self.x.display, 0)
        self.x.errors.clear()


class Frames(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, count):
        self.token = secrets.token_hex(24)
        self.frames = [(-1, b"") for _ in range(count)]
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", 0), FrameRequest)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class FrameRequest(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *_):
        pass  # Never log the private capability URL or each frame request.

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("/")
        if (
            len(path) != 4
            or path[1] != self.server.token
            or not path[2].isdigit()
            or len(path[2]) > 3
            or int(path[2]) >= len(self.server.frames)
            or path[3] not in {"status", "frame.jpg"}
        ):
            self.send_error(404)
            return
        sequence, frame = self.server.frames[int(path[2])]
        data = json.dumps({"sequence": sequence}).encode() if path[3] == "status" else frame
        self.send_response(200 if data else 503)
        self.send_header(
            "Content-Type", "application/json" if path[3] == "status" else "image/jpeg"
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
