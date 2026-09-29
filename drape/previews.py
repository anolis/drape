"""Generate a preview of one installed variant from its own files.

A gnome-look item has one screenshot for the whole pack, so variants (Dark, Compact, ...) need
previews drawn from what was actually installed.
"""

import hashlib
import os
import struct
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

import cairo  # noqa: E402

CACHE = Path(GLib.get_user_cache_dir()) / "drape" / "variants"
W, H = 420, 260

SAMPLE_ICONS = [
    ["folder"], ["user-home", "folder-home"], ["folder-documents"], ["folder-download"],
    ["folder-pictures"], ["folder-music"], ["utilities-terminal", "terminal"],
    ["accessories-text-editor", "text-editor"], ["web-browser", "firefox", "internet-web-browser"],
    ["system-file-manager", "file-manager"], ["preferences-system", "preferences-desktop"],
    ["user-trash", "trash-empty"], ["image-x-generic"], ["audio-x-generic"], ["video-x-generic"],
    ["text-x-generic"], ["application-x-executable"], ["accessories-calculator"],
]
SAMPLE_CURSORS = [
    ["left_ptr", "default"], ["pointer", "hand2"], ["text", "xterm"], ["wait", "watch"],
    ["progress", "left_ptr_watch"], ["crosshair"], ["move", "fleur"], ["not-allowed", "crossed_circle"],
    ["help", "question_arrow"], ["ns-resize", "sb_v_double_arrow"], ["ew-resize", "sb_h_double_arrow"],
    ["nwse-resize", "bd_double_arrow"],
]


def _cache_path(theme_dir, kind, marker):
    try:
        stamp = str((theme_dir / marker).stat().st_mtime_ns) if marker else ""
    except OSError:
        stamp = ""
    digest = hashlib.sha1(f"{theme_dir}|{kind}|{stamp}".encode()).hexdigest()[:16]
    return CACHE / f"{theme_dir.name}-{digest}.png"


def _surface_to_pixbuf(surface):
    surface.flush()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())


def _draw_grid(cr, pixbufs, cell, cols, top, height):
    rows = max(1, (len(pixbufs) + cols - 1) // cols)
    gx = (W - cols * cell) / 2
    gy = top + (height - rows * cell) / 2
    for i, pb in enumerate(pixbufs):
        x = gx + (i % cols) * cell + (cell - pb.get_width()) / 2
        y = gy + (i // cols) * cell + (cell - pb.get_height()) / 2
        Gdk.cairo_set_source_pixbuf(cr, pb, round(x), round(y))
        cr.paint()


def _grid(pixbufs, cell, cols, background=None):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surface)
    if background:
        cr.set_source_rgb(*background)
        cr.paint()
    _draw_grid(cr, pixbufs, cell, cols, 0, H)
    return _surface_to_pixbuf(surface)


# ---------------------------------------------------------------- icons (main thread)

def icon_preview(theme_dir):
    """A grid of common icons, taken from this theme only where it has them."""
    theme = Gtk.IconTheme.new()
    theme.set_search_path([str(theme_dir.parent)] + [p for p in Gtk.IconTheme.get_default().get_search_path()
                                                      if Path(p) != theme_dir.parent])
    theme.set_custom_theme(theme_dir.name)
    own, borrowed = [], []
    for names in SAMPLE_ICONS:
        info = theme.choose_icon(names, 48, Gtk.IconLookupFlags.FORCE_SIZE)
        if info is None:
            continue
        try:
            pb = info.load_icon()
        except GLib.Error:
            continue
        mine = (info.get_filename() or "").startswith(str(theme_dir) + os.sep)
        (own if mine else borrowed).append(pb)
    icons = (own + borrowed)[:12] if len(own) < 6 else own[:12]
    if not icons:
        return None
    panel = _panel_icons(theme_dir)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surface)
    grid_h = H - (56 if panel else 0)
    _draw_grid(cr, icons, 70, 6, 0, grid_h)
    if panel:
        # Dark/Light variants mostly differ in their panel icons: show them on a neutral strip
        cr.set_source_rgb(0.5, 0.51, 0.53)
        cr.rectangle(0, grid_h, W, H - grid_h)
        cr.fill()
        _draw_grid(cr, panel, 40, len(panel), grid_h, H - grid_h)
    return _surface_to_pixbuf(surface)


PANEL_PREFERRED = ["audio-volume-high", "network-wireless-signal-excellent", "network-wired", "battery-full",
                   "bluetooth-active", "mail-unread", "system-shutdown", "weather-clear", "nm-signal-100",
                   "indicator-messages", "user-available", "update-none"]


def _panel_icons(theme_dir):
    """Up to 8 tray/panel icons shipped by the theme itself."""
    for size in ("22x22", "24x24", "16x16"):
        for sub in (theme_dir / size / "panel", theme_dir / "panel" / size):
            if not sub.is_dir():
                continue
            files = {p.stem: p for p in sub.iterdir() if p.suffix.lower() in (".svg", ".png")}
            picked = [files[n] for pref in PANEL_PREFERRED
                      for n in sorted(files) if n.startswith(pref)][:8]
            if len(picked) < 8:
                picked += [p for n, p in sorted(files.items()) if p not in picked][:8 - len(picked)]
            pbs = []
            for p in picked[:8]:
                try:
                    pbs.append(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), 24, 24, True))
                except GLib.Error:
                    pass
            if pbs:
                return pbs
    return []


# ---------------------------------------------------------------- cursors

def _read_xcursor(path, want=32):
    """First frame of the size closest to `want`, as a Pixbuf."""
    data = path.read_bytes()
    if data[:4] != b"Xcur":
        return None
    n = struct.unpack_from("<I", data, 12)[0]
    best = None
    for i in range(n):
        typ, size, pos = struct.unpack_from("<III", data, 16 + 12 * i)
        if typ != 0xFFFD0002:
            continue
        if best is None or abs(size - want) < abs(best[0] - want):
            best = (size, pos)
    if best is None:
        return None
    _h, _t, _s, _v, w, h, _xh, _yh, _d = struct.unpack_from("<9I", data, best[1])
    argb = data[best[1] + 36:best[1] + 36 + w * h * 4]
    # Xcursor is premultiplied ARGB (little-endian BGRA bytes); cairo uses the same layout
    surface = cairo.ImageSurface.create_for_data(bytearray(argb), cairo.FORMAT_ARGB32, w, h, w * 4)
    pb = _surface_to_pixbuf(surface)
    if max(w, h) > 48:
        pb = pb.scale_simple(w * 48 // max(w, h), h * 48 // max(w, h), GdkPixbuf.InterpType.BILINEAR)
    return pb


def cursor_preview(theme_dir):
    cursors = theme_dir / "cursors"
    pbs = []
    for names in SAMPLE_CURSORS:
        for n in names:
            p = cursors / n
            if p.exists():
                try:
                    pb = _read_xcursor(p.resolve())
                except (OSError, struct.error, ValueError):
                    pb = None
                if pb:
                    pbs.append(pb)
                break
    # mid-grey so both light and dark cursors stand out
    return _grid(pbs, 64, 6, background=(0.62, 0.63, 0.65)) if pbs else None


# ---------------------------------------------------------------- themes (subprocess)

def render_gtk_theme(theme_name, out):
    """Render a sample window with `theme_name` in a separate process (GTK_THEME only applies at
    startup). Returns True on success."""
    env = dict(os.environ, GTK_THEME=theme_name)
    env.pop("GSETTINGS_BACKEND", None)
    try:
        r = subprocess.run([sys.executable, "-m", "drape.render_gtk", str(out)], env=env, timeout=20,
                           capture_output=True, cwd=str(Path(__file__).resolve().parent.parent))
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0 and Path(out).exists()


def _button_strip(metacity):
    """Title-bar buttons shipped with a Metacity theme, for themes with no GTK part."""
    pbs = []
    for stem in ("min", "max", "close"):
        for p in sorted(metacity.glob(f"*{stem}*")):
            if p.suffix.lower() in (".png", ".svg"):
                try:
                    pbs.append(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), 40, 40, True))
                except GLib.Error:
                    continue
                break
    return _grid(pbs, 64, 3, background=(0.85, 0.85, 0.86)) if pbs else None


def theme_preview_path(theme_dir, kind):
    """Path of a PNG preview for a GTK / window border / desktop theme, rendering it if needed.
    Blocking - call from a worker thread."""
    for name in ("contents/previews/fullscreenpreview.jpg", "contents/previews/preview.png",
                 "contents/screenshot.png", "screenshot.png", "preview.png"):
        if (theme_dir / name).is_file():
            return theme_dir / name
    if kind == "desktop" and (theme_dir / "cinnamon" / "thumbnail.png").is_file():
        return theme_dir / "cinnamon" / "thumbnail.png"
    if kind == "wm" and (theme_dir / "metacity-1" / "thumbnail.png").is_file():
        return theme_dir / "metacity-1" / "thumbnail.png"
    if (theme_dir / "gtk-3.0").is_dir():
        out = _cache_path(theme_dir, "gtk", "gtk-3.0/gtk.css")
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            if not render_gtk_theme(theme_dir.name, out):
                return None
        return out
    return None


def preview(component, kind):
    """Return a Pixbuf for icons/cursors (fast, main thread) or None for themes that need
    theme_preview_path() on a worker thread."""
    theme_dir = Path(component["path"])
    if kind == "icons" or ("icons" in component["provides"] and kind != "cursors"):
        return icon_preview(theme_dir)
    if kind == "cursors":
        return cursor_preview(theme_dir)
    if kind == "wm" and not (theme_dir / "gtk-3.0").is_dir() and (theme_dir / "metacity-1").is_dir() \
            and not (theme_dir / "metacity-1" / "thumbnail.png").is_file():
        return _button_strip(theme_dir / "metacity-1")
    return None
