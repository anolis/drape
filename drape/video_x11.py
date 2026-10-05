"""Request desktop layering through Muffin's EWMH desktop-manager interface.

Ordinary XConfigureWindow/GDK restacks are subject to focus-stealing checks.
Wallpaper management uses the EWMH pager/desktop source, and targets only a
Drape-owned surface relative to an existing desktop window.
"""

import ctypes
from ctypes.util import find_library

from .video_wallpapers import VideoError


class _Data(ctypes.Union):
    _fields_ = [("l", ctypes.c_long * 5)]


class _ClientMessage(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", _Data),
    ]


class _Event(ctypes.Union):
    _fields_ = [("client", _ClientMessage), ("padding", ctypes.c_long * 24)]


def restack_below(window, sibling):
    lib = ctypes.CDLL(find_library("X11") or "libX11.so.6")
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XSendEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_long,
        ctypes.POINTER(_Event),
    ]
    lib.XSendEvent.restype = ctypes.c_int
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = lib.XOpenDisplay(None)
    if not display:
        raise VideoError("Cannot access the Cinnamon X11 desktop.")
    try:
        event = _Event()
        event.client.type = 33  # ClientMessage
        event.client.display = display
        event.client.window = window
        event.client.message_type = lib.XInternAtom(display, b"_NET_RESTACK_WINDOW", 0)
        event.client.format = 32
        event.client.data.l[0] = 2  # EWMH pager / desktop manager
        event.client.data.l[1] = sibling
        event.client.data.l[2] = 1  # Below
        mask = (1 << 20) | (1 << 19)  # SubstructureRedirectMask | SubstructureNotifyMask
        if not lib.XSendEvent(
            display, lib.XDefaultRootWindow(display), 0, mask, ctypes.byref(event)
        ):
            raise VideoError("Cinnamon rejected the wallpaper stacking request.")
        lib.XFlush(display)
    finally:
        lib.XCloseDisplay(display)
