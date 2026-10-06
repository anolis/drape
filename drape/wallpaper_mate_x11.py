"""Small X11 bridge for Caja's shared background, used only by a helper process.

Caja paints an opaque desktop from its root pixmap. Updating that existing
pixmap lets Caja keep drawing its own icons and handling desktop input. Never
replace root properties or kill the client which owns the original pixmap.
"""

import ctypes as c
from ctypes.util import find_library


class BackgroundError(RuntimeError):
    pass


class X11:
    def __init__(self):
        self.lib = c.CDLL(find_library("X11") or "libX11.so.6")
        self.composite = c.CDLL(find_library("Xcomposite") or "libXcomposite.so.1")
        pointer, xid, integer = c.c_void_p, c.c_ulong, c.c_int
        signatures = {
            "XOpenDisplay": ([c.c_char_p], pointer),
            "XDefaultRootWindow": ([pointer], xid),
            "XInternAtom": ([pointer, c.c_char_p, integer], xid),
            "XGetWindowProperty": (
                [
                    pointer,
                    xid,
                    xid,
                    c.c_long,
                    c.c_long,
                    integer,
                    xid,
                    c.POINTER(xid),
                    c.POINTER(integer),
                    c.POINTER(xid),
                    c.POINTER(xid),
                    c.POINTER(pointer),
                ],
                integer,
            ),
            "XGetGeometry": (
                [
                    pointer,
                    xid,
                    c.POINTER(xid),
                    c.POINTER(integer),
                    c.POINTER(integer),
                    *[c.POINTER(c.c_uint)] * 4,
                ],
                integer,
            ),
            "XCreatePixmap": ([pointer, xid, c.c_uint, c.c_uint, c.c_uint], xid),
            "XCreateGC": ([pointer, xid, xid, pointer], pointer),
            "XCopyArea": (
                [
                    pointer,
                    xid,
                    xid,
                    pointer,
                    integer,
                    integer,
                    c.c_uint,
                    c.c_uint,
                    integer,
                    integer,
                ],
                integer,
            ),
            "XClearArea": ([pointer, xid, integer, integer, c.c_uint, c.c_uint, integer], integer),
            "XSync": ([pointer, integer], integer),
            "XFree": ([pointer], integer),
            "XFreePixmap": ([pointer, xid], integer),
            "XFreeGC": ([pointer, pointer], integer),
            "XCloseDisplay": ([pointer], integer),
            "XGrabServer": ([pointer], integer),
            "XUngrabServer": ([pointer], integer),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
        for name in ("XCompositeRedirectWindow", "XCompositeUnredirectWindow"):
            getattr(self.composite, name).argtypes = [pointer, xid, integer]
        self.composite.XCompositeNameWindowPixmap.argtypes = [pointer, xid]
        self.composite.XCompositeNameWindowPixmap.restype = xid
        self.composite.XCompositeQueryExtension.argtypes = [
            pointer,
            c.POINTER(integer),
            c.POINTER(integer),
        ]
        self.composite.XCompositeQueryExtension.restype = integer
        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise BackgroundError("Cannot access MATE's X11 display.")
        # Xlib's error handler is process-global. This bridge is isolated from
        # GTK so a disappeared drawable cannot terminate the wallpaper player.
        self.errors = []
        self.handler = c.CFUNCTYPE(integer, pointer, pointer)(self._error)
        self.lib.XSetErrorHandler.argtypes = [type(self.handler)]
        self.lib.XSetErrorHandler(self.handler)
        self.root = self.lib.XDefaultRootWindow(self.display)

    def _error(self, _display, _event):
        self.errors[:] = ["MATE's desktop or rendering surface changed during playback."]
        return 0

    def sync(self):
        self.lib.XSync(self.display, 0)
        if self.errors:
            message = self.errors.pop()
            raise BackgroundError(message)

    def property(self, name, expected_type):
        actual, count, remaining = c.c_ulong(), c.c_ulong(), c.c_ulong()
        format_ = c.c_int()
        data = c.c_void_p()
        atom = self.lib.XInternAtom(self.display, name.encode(), 1)
        if not atom:
            return 0
        try:
            result = self.lib.XGetWindowProperty(
                self.display,
                self.root,
                atom,
                0,
                1,
                0,
                expected_type,
                c.byref(actual),
                c.byref(format_),
                c.byref(count),
                c.byref(remaining),
                c.byref(data),
            )
            self.sync()
            if result or actual.value != expected_type or format_.value != 32 or count.value != 1:
                return 0
            return c.cast(data, c.POINTER(c.c_ulong))[0]
        finally:
            if data.value:
                self.lib.XFree(data)

    def geometry(self, drawable):
        root, xx, yy = c.c_ulong(), c.c_int(), c.c_int()
        width, height, border, depth = (c.c_uint() for _ in range(4))
        ok = self.lib.XGetGeometry(
            self.display,
            drawable,
            c.byref(root),
            c.byref(xx),
            c.byref(yy),
            c.byref(width),
            c.byref(height),
            c.byref(border),
            c.byref(depth),
        )
        self.sync()
        if not ok or not width.value or not height.value:
            raise BackgroundError("MATE's desktop background is unavailable.")
        return width.value, height.value, depth.value

    def copy(self, source, target, gc, width, height, x=0, y=0):
        self.lib.XCopyArea(self.display, source, target, gc, 0, 0, width, height, x, y)

    def repaint(self, caja):
        # Expose asks Caja to redraw its background AND icons; copying into its
        # window directly would overwrite icon pixels and desktop selections.
        self.lib.XClearArea(self.display, caja, 0, 0, 0, 0, 1)

    def close(self):
        self.lib.XCloseDisplay(self.display)


class Background:
    def __init__(self, x11, surfaces):
        self.x = x11
        self.caja = x11.property("CAJA_DESKTOP_WINDOW_ID", 33)  # XA_WINDOW
        self.pixmap = 0
        self.backup = 0
        self.gc = None
        self.frames = []
        self.redirected = []
        if not self.caja:
            return  # No icon host: normal desktop surfaces are sufficient.
        try:
            self.pixmap = x11.property("_XROOTPMAP_ID", 20)  # XA_PIXMAP
            if not self.pixmap:
                raise BackgroundError("Caja has no shared background available for live playback.")
            first, second = c.c_int(), c.c_int()
            if not x11.composite.XCompositeQueryExtension(
                x11.display, c.byref(first), c.byref(second)
            ):
                raise BackgroundError("MATE live wallpapers require the X11 Composite extension.")
            self.width, self.height, depth = x11.geometry(self.pixmap)
            self.gc = x11.lib.XCreateGC(x11.display, self.pixmap, 0, None)
            self.backup = x11.lib.XCreatePixmap(
                x11.display, self.pixmap, self.width, self.height, depth
            )
            if not self.gc or not self.backup:
                raise BackgroundError("Cannot allocate MATE background buffers.")
            x11.copy(self.pixmap, self.backup, self.gc, self.width, self.height)
            x11.sync()
            for window, xx, yy in surfaces:
                width, height, surface_depth = x11.geometry(window)
                if (
                    surface_depth != depth
                    or xx < 0
                    or yy < 0
                    or xx + width > self.width
                    or yy + height > self.height
                ):
                    raise BackgroundError(
                        "MATE's background does not match the current monitor layout."
                    )
                # Redirect only our drawing area. Its backing pixmap includes
                # mpv/animation children even when Caja occludes the window.
                x11.composite.XCompositeRedirectWindow(x11.display, window, 0)
                self.redirected.append(window)
                frame = x11.composite.XCompositeNameWindowPixmap(x11.display, window)
                self.frames.append((frame, width, height, xx, yy))
            x11.sync()
        except Exception:
            self.close()
            raise

    def unchanged(self):
        return (
            self.x.property("_XROOTPMAP_ID", 20) == self.pixmap
            and self.x.property("CAJA_DESKTOP_WINDOW_ID", 33) == self.caja
        )

    def draw(self):
        if not self.caja:
            return
        if not self.unchanged():
            raise BackgroundError(
                "MATE's wallpaper changed outside Drape. Live playback has stopped."
            )
        for frame, width, height, xx, yy in self.frames:
            self.x.copy(frame, self.pixmap, self.gc, width, height, xx, yy)
        self.x.repaint(self.caja)
        self.x.sync()

    def close(self):
        x11 = self.x
        # Keep the identity check and restoration atomic with respect to Caja.
        # If another app installed a new wallpaper, leave that wallpaper alone.
        x11.lib.XGrabServer(x11.display)
        try:
            if self.backup and self.unchanged():
                x11.copy(self.backup, self.pixmap, self.gc, self.width, self.height)
                x11.repaint(self.caja)
            for frame, *_ in self.frames:
                x11.lib.XFreePixmap(x11.display, frame)
            for window in self.redirected:
                x11.composite.XCompositeUnredirectWindow(x11.display, window, 0)
            if self.backup:
                x11.lib.XFreePixmap(x11.display, self.backup)
            if self.gc:
                x11.lib.XFreeGC(x11.display, self.gc)
        finally:
            self.frames.clear()
            self.redirected.clear()
            self.backup, self.gc = 0, None
            x11.lib.XUngrabServer(x11.display)
            # Disappeared surfaces during shutdown are harmless. Restoration
            # errors must not prevent ungrabbing the server or disconnecting.
            x11.lib.XSync(x11.display, 0)
            x11.errors.clear()
