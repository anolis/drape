"""Publish owned render frames for Xfdesktop's temporary background adapter.

Only Drape's property namespace and pixmaps are changed. Xfdesktop keeps owning
its icons, input, workspace backgrounds and saved appearance settings.
"""

import ctypes as c
import os

from .wallpaper_x11 import BackgroundError

PIXMAP = "_DRAPE_LIVE_WALLPAPER_PIXMAP"
FRAME = "_DRAPE_LIVE_WALLPAPER_FRAME"
OWNER = "_DRAPE_LIVE_WALLPAPER_OWNER"
ADAPTER = "_DRAPE_XFDESKTOP_ADAPTER"


class Background:
    def __init__(self, x11, surfaces):
        self.x = x11
        self.pixmap, self.gc, self.frames, self.redirected = 0, None, [], []
        self.frame = 0
        self.windows = set()
        self.adapter = x11.property(ADAPTER, 6)
        if not 0 < self.adapter <= 0x7FFFFFFF:
            raise BackgroundError(
                "Xfce’s desktop adapter is not running. Enable it from Live wallpapers."
            )
        if x11.property(PIXMAP, 20):
            self._discard_stale_publisher()
        try:
            first, second = c.c_int(), c.c_int()
            if not x11.composite.XCompositeQueryExtension(
                x11.display, c.byref(first), c.byref(second)
            ):
                raise BackgroundError("Xfce live wallpapers require the X11 Composite extension.")
            width, height, depth = x11.geometry(x11.root)
            self.pixmap = x11.lib.XCreatePixmap(x11.display, x11.root, width, height, depth)
            self.gc = x11.lib.XCreateGC(x11.display, self.pixmap, 0, None)
            if not self.pixmap or not self.gc:
                raise BackgroundError("Cannot allocate Xfce background buffers.")
            x11.lib.XSetForeground(x11.display, self.gc, 0)
            x11.lib.XFillRectangle(x11.display, self.pixmap, self.gc, 0, 0, width, height)
            for window, xx, yy, *top in surfaces:
                if top:
                    self.windows.add(top[0])
                ww, hh, dd = x11.geometry(window)
                if dd != depth or xx < 0 or yy < 0 or xx + ww > width or yy + hh > height:
                    raise BackgroundError(
                        "Xfce’s rendering surfaces do not match the monitor layout."
                    )
                x11.composite.XCompositeRedirectWindow(x11.display, window, 0)
                self.redirected.append(window)
                frame = x11.composite.XCompositeNameWindowPixmap(x11.display, window)
                self.frames.append((frame, ww, hh, xx, yy))
            x11.sync()
            x11.set_property(OWNER, os.getpid(), 6)
            # Publish only after the first copy, so uninitialized pixels cannot
            # flash through Xfdesktop's normal background.
        except Exception:
            self.close()
            raise

    def _discard_stale_publisher(self):
        owner = self.x.property(OWNER, 6)
        if not 0 < owner <= 0x7FFFFFFF:
            raise BackgroundError("Another live background is already registered on this display.")
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            # X frees a dead helper's pixmap, but properties may outlive it.
            # Remove only that dead publisher's names, never another live one.
            if self.x.property(OWNER, 6) == owner:
                for name in (PIXMAP, FRAME, OWNER):
                    self.x.delete_property(name)
                self.x.sync()
                return
        except PermissionError:
            pass  # An inaccessible publisher must be treated as alive.
        raise BackgroundError("Another live background is already registered on this display.")

    def unchanged(self):
        if self.x.property(ADAPTER, 6) != self.adapter:
            return False
        try:
            os.kill(self.adapter, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def draw(self):
        if not self.unchanged():
            raise BackgroundError("Xfce’s desktop was restarted. Live playback has stopped.")
        for frame, width, height, xx, yy in self.frames:
            self.x.copy(frame, self.pixmap, self.gc, width, height, xx, yy)
        # Hacks may raise their supplied window behind GDK’s cached stack.
        # Lower our actual root children, rather than relying on cached ordering.
        for window in self.windows:
            self.x.lib.XLowerWindow(self.x.display, window)
        self.frame = (self.frame % 0xFFFFFFFF) + 1
        self.x.set_property(PIXMAP, self.pixmap, 20)
        self.x.set_property(FRAME, self.frame, 6)
        self.x.sync()

    def close(self):
        x11 = self.x
        x11.lib.XGrabServer(x11.display)
        try:
            if x11.property(OWNER, 6) == os.getpid():
                for name in (PIXMAP, FRAME, OWNER):
                    x11.delete_property(name)
            for frame, *_ in self.frames:
                x11.lib.XFreePixmap(x11.display, frame)
            for window in self.redirected:
                x11.composite.XCompositeUnredirectWindow(x11.display, window, 0)
            if self.gc:
                x11.lib.XFreeGC(x11.display, self.gc)
            if self.pixmap:
                x11.lib.XFreePixmap(x11.display, self.pixmap)
        finally:
            self.frames.clear()
            self.redirected.clear()
            self.pixmap, self.gc = 0, None
            x11.lib.XUngrabServer(x11.display)
            x11.lib.XSync(x11.display, 0)
            x11.errors.clear()
