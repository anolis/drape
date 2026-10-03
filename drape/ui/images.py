"""Preview loading, caching, rendering workers and animation state."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import requests
import threading
import time

from .gtk import GLib, GdkPixbuf, Gtk
from .. import animation, pling, settings


THUMB_DIR = Path(GLib.get_user_cache_dir()) / "drape" / "thumbs"


_images = ThreadPoolExecutor(max_workers=6)


_pixbufs = {}  # (source, width, height) -> Pixbuf, for this session

_PIXBUF_LIMIT = 600


# Image decoding and animated frame preparation


def _decode(path, width, height):
    """Scale an image to fit; animated GIFs use their first frame (the plain loader rejects them)."""
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
    except GLib.Error:
        pb = GdkPixbuf.PixbufAnimation.new_from_file(str(path)).get_static_image()
        scale = min(width / pb.get_width(), height / pb.get_height(), 1)
        return pb.scale_simple(
            max(1, round(pb.get_width() * scale)),
            max(1, round(pb.get_height() * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )


MAX_FRAMES = 150


def _frames(path, width, height):
    """[(Pixbuf, delay_ms)] for an animated image scaled to fit, or None if it isn't animated."""
    from PIL import Image, ImageSequence

    with Image.open(path) as im:
        if not getattr(im, "is_animated", False):
            return None
        scale = min(width / im.width, height / im.height, 1)
        size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        frames = []
        for frame in ImageSequence.Iterator(im):
            delay = frame.info.get("duration") or 100
            rgba = frame.convert("RGBA").resize(size, Image.BILINEAR)
            pb = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(rgba.tobytes()),
                GdkPixbuf.Colorspace.RGB,
                True,
                8,
                size[0],
                size[1],
                size[0] * 4,
            )
            frames.append((pb, max(20, int(delay))))
            if len(frames) >= MAX_FRAMES:
                break
    return frames if len(frames) > 1 else None


_ui_busy_until = [0.0]  # cards being added right now also cost CPU; don't blame the animations


# Playback scheduling and foreground load


def _ui_busy():
    return _foreground["pending"] > 0 or time.monotonic() < _ui_busy_until[0]


ANIMATIONS = animation.Governor(lambda: settings.get("animations"), busy=_ui_busy)


def _play(image, frames):
    """Animate `image`; see animation.Player for when it plays."""
    old = getattr(image, "_drape_anim", None)
    if old:
        old()
    player = animation.Player(image, frames, ANIMATIONS)

    def cancel():
        player.cancel()
        image._drape_anim = None

    image._drape_anim = cancel


def _show(image, result):
    if getattr(image, "_drape_destroyed", False):
        return False
    old = getattr(image, "_drape_anim", None)
    if old:
        old()
    if isinstance(result, list):
        _play(image, result)
    else:
        image.set_from_pixbuf(result)


_foreground = {
    "pending": 0
}  # previews the user is waiting to see; background prefetch waits for these

_fg_lock = threading.Lock()


# Thumbnail fetch and caching


def _fetch_thumb(url, width, height):
    """Download a preview and keep a card-sized copy on disk (no GTK objects touched)."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url.encode()).hexdigest()
    small = THUMB_DIR / f"{digest}-{width}x{height}.png"
    original = THUMB_DIR / digest
    if small.exists() or original.exists():
        return
    r = requests.get(url, timeout=(15, 90), headers={"User-Agent": pling.USER_AGENT})
    r.raise_for_status()
    tmp = original.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(original)
    if url.lower().split("?")[0].endswith(".gif"):
        return  # possibly animated: played from the original
    _decode(original, width, height).savev(str(small), "png", [], [])
    original.unlink(missing_ok=True)


# Widget-safe asynchronous image loading


def load_image(url, image, width, height, on_done=None, alive=None):
    """Show a preview in `image`, scaled to fit. Downloads once, keeps a card-sized copy on disk and
    decoded images in memory, so revisiting a tab is instant. Local paths work too."""
    key = (url, width, height)
    if not hasattr(image, "_drape_destroyed"):
        image._drape_destroyed = False
        image.connect("destroy", lambda *_: setattr(image, "_drape_destroyed", True))

    def deliver(result=None, ok=True):
        # Jobs may finish after navigating away; never deliver to a destroyed GTK widget.
        if image._drape_destroyed or (alive is not None and not alive()):
            return False
        if ok:
            _show(image, result)
        else:
            image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
        if on_done:
            on_done(ok)
        return False

    cached = _pixbufs.get(key)
    if cached is not None:
        deliver(cached)
        return

    def work():
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        local = url.startswith("/")
        digest = hashlib.sha1(url.encode()).hexdigest()
        small = THUMB_DIR / f"{digest}-{width}x{height}.png"
        original = Path(url) if local else THUMB_DIR / digest
        # GIFs may be animated, so they're always played from the original
        maybe_animated = url.lower().split("?")[0].endswith(".gif")
        if not local and small.exists() and not maybe_animated:
            pb = GdkPixbuf.Pixbuf.new_from_file(str(small))
        else:
            path = Path(url) if local else THUMB_DIR / digest
            if not path.exists():
                r = requests.get(url, timeout=(15, 90), headers={"User-Agent": pling.USER_AGENT})
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(path)
            pb = _frames(path, width, height)  # animations keep their original to replay from
            if pb is None:
                pb = _decode(path, width, height)
                if not local:
                    pb.savev(str(small), "png", [], [])
                    path.unlink(missing_ok=True)  # the card-sized copy is all we need
        if len(_pixbufs) > _PIXBUF_LIMIT:
            _pixbufs.clear()
        _pixbufs[key] = pb
        GLib.idle_add(deliver, pb)

    def safe():
        try:
            if alive is not None and not alive():
                return  # the card was thrown away before its turn came
            work()
        except Exception:  # noqa: BLE001 - a missing preview is not worth an error dialog
            GLib.idle_add(deliver, None, False)
        finally:
            with _fg_lock:
                _foreground["pending"] -= 1

    with _fg_lock:
        _foreground["pending"] += 1
    _images.submit(safe)


_renders = ThreadPoolExecutor(max_workers=2)  # theme previews each start a GTK process
