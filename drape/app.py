"""drape GTK app: browse gnome-look.org by category and install/apply each piece in one click."""

import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

import requests  # noqa: E402

from . import animation, desktop, installer, pling, previews, settings, system  # noqa: E402
from . import helper as root_helper  # noqa: E402
from .installer import system_copies, system_file_name  # noqa: E402,F401

APP_ID = "io.github.anolis.Drape"
THUMB_DIR = Path(GLib.get_user_cache_dir()) / "drape" / "thumbs"
CARD_W, CARD_H = 260, 160
# Window borders only reach apps that let the window manager draw their title bar
WM_NOTE = ("Window borders only show on apps with a classic title bar, like Files (Nemo). Apps that draw "
           "their own title bar, like drape and most GNOME apps, follow your Controls theme instead.")
class WindowBordersHelp(Gtk.Box):
    """Explains which apps window borders reach, with a sample window to see them on. Checking the open
    windows only happens when the user asks, and the answer isn't kept."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12, margin_bottom=0)
        self.win = window
        note = Gtk.Label(label=WM_NOTE, xalign=0, wrap=True)
        note.get_style_context().add_class("dim-label")
        self.pack_start(note, False, False, 0)
        buttons = Gtk.Box(spacing=6)
        sample = Gtk.Button(label="Show a sample window")
        sample.set_tooltip_text("Opens a small window with a classic title bar, so you can see the borders")
        sample.connect("clicked", lambda _b: window.show_border_sample())
        buttons.pack_start(sample, False, False, 0)
        self.check = Gtk.Button(label="Check my open windows")
        self.check.set_tooltip_text("Shows which of the apps you have open right now use these borders")
        self.check.connect("clicked", lambda _b: self.run_check())
        buttons.pack_start(self.check, False, False, 0)
        self.pack_start(buttons, False, False, 0)
        self.apps = Gtk.Label(xalign=0, wrap=True, use_markup=True, no_show_all=True)
        self.pack_start(self.apps, False, False, 0)
        # forget the answer when the user leaves this tab
        self.connect("unmap", lambda *_: (self.apps.set_text(""), self.apps.hide()))
        # only if the user chose "Always allow"
        self.connect("map", lambda *_: settings.get("window_check") == "always" and self.run_check(asked=True))

    def _consent(self):
        """Ask before looking at the user's open windows. Returns True to go ahead."""
        d = Gtk.MessageDialog(transient_for=self.win, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text="Check your open windows?")
        d.format_secondary_text(
            "To show which apps use these borders, drape looks at the windows you have open right now: each "
            "app's name and whether it draws its own title bar. It doesn't look at anything else, and nothing "
            "is saved or sent anywhere.")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Just this once", 1, "Always allow", 2)
        d.set_default_response(1)
        resp = d.run()
        d.destroy()
        if resp == 2:
            settings.set("window_check", "always")
            self.win.sync_menu()
        return resp in (1, 2)

    def run_check(self, asked=False):
        if not self.check.get_sensitive():
            return
        if not asked and settings.get("window_check") != "always" and not self._consent():
            return
        self.check.set_sensitive(False)

        def done(windows):
            self.check.set_sensitive(True)
            esc = GLib.markup_escape_text
            if windows is None:
                self.apps.set_text("drape can't tell on this desktop session.")
            else:
                classic = [n for n, c in windows if c]
                own = [n for n, c in windows if not c]
                lines = []
                if classic:
                    lines.append("<b>Show these borders:</b> " + esc(", ".join(classic)))
                if own:
                    lines.append("<b>Draw their own title bar:</b> " + esc(", ".join(own)))
                lines.append("<small>Only looks at the windows you have open right now. Nothing is saved or "
                             "sent anywhere." + (" Turn off automatic checks in the ☰ menu." if
                                                 settings.get("window_check") == "always" else "") + "</small>")
                self.apps.set_markup("\n".join(lines))
            self.apps.show()
        run_async(desktop.open_windows, done)


CHUNK = 10          # results per request: small batches paint sooner on slow connections
FIRST_CHUNKS = 3    # batches requested up front when a tab opens

_images = ThreadPoolExecutor(max_workers=6)


# tabs whose installed items are identified by other part names
TAB_PARTS = {"login": {"sddm", "webgreeter"}, "boot": {"plymouth"}}


LOGIN_KEYS = {"gtk": ("gtk", "theme-name"), "icons": ("icons", "icon-theme-name"),
              "cursors": ("icons", "cursor-theme-name")}


def login_commands(greeter, kind, component):
    """Helper commands that copy an installed item into /usr/share and point the greeter at it
    (the login screen runs as its own user and can't read your home folder)."""
    if kind == "wallpapers":
        name = system_file_name(component)
        return [["install", "background", component["path"], "--name", name],
                ["greeter-set", greeter, f"background={root_helper.DIRS['background'] / name}"]]
    target, key = LOGIN_KEYS[kind]
    name = component["name"]
    dest = root_helper.DIRS[target] / name
    cmds = []
    # a system theme of the same name is already readable by the login screen
    if not dest.exists() or (dest / root_helper.MARKER).exists():
        cmds.append(["install", target, component["path"], "--name", name])
    cmds.append(["greeter-set", greeter, f"{key}={name}"])
    return cmds


def matches(kind, component):
    return bool(TAB_PARTS.get(kind, {kind}) & set(component["provides"]))


def run_async(work, done, error=None):
    """Run work() on a thread, then done(result) or error(exc) on the main loop."""
    def target():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            if error:
                GLib.idle_add(error, e)
            else:
                print(f"drape: {e}", file=sys.stderr)
            return
        GLib.idle_add(done, result)
    threading.Thread(target=target, daemon=True).start()


_pixbufs = {}  # (source, width, height) -> Pixbuf, for this session
_PIXBUF_LIMIT = 600


def _decode(path, width, height):
    """Scale an image to fit; animated GIFs use their first frame (the plain loader rejects them)."""
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
    except GLib.Error:
        pb = GdkPixbuf.PixbufAnimation.new_from_file(str(path)).get_static_image()
        scale = min(width / pb.get_width(), height / pb.get_height(), 1)
        return pb.scale_simple(max(1, round(pb.get_width() * scale)), max(1, round(pb.get_height() * scale)),
                               GdkPixbuf.InterpType.BILINEAR)


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
            pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgba.tobytes()), GdkPixbuf.Colorspace.RGB,
                                                 True, 8, size[0], size[1], size[0] * 4)
            frames.append((pb, max(20, int(delay))))
            if len(frames) >= MAX_FRAMES:
                break
    return frames if len(frames) > 1 else None


_ui_busy_until = [0.0]  # cards being added right now also cost CPU; don't blame the animations


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
    old = getattr(image, "_drape_anim", None)
    if old:
        old()
    if isinstance(result, list):
        _play(image, result)
    else:
        image.set_from_pixbuf(result)


_foreground = {"pending": 0}  # previews the user is waiting to see; background prefetch waits for these
_fg_lock = threading.Lock()


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


def load_image(url, image, width, height, on_done=None, alive=None):
    """Show a preview in `image`, scaled to fit. Downloads once, keeps a card-sized copy on disk and
    decoded images in memory, so revisiting a tab is instant. Local paths work too."""
    key = (url, width, height)
    cached = _pixbufs.get(key)
    if cached is not None:
        _show(image, cached)
        if on_done:
            on_done(True)
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
        GLib.idle_add(_show, image, pb)
        if on_done:
            GLib.idle_add(on_done, True)

    def safe():
        try:
            if alive is not None and not alive():
                return  # the card was thrown away before its turn came
            work()
        except Exception:  # noqa: BLE001 - a missing preview is not worth an error dialog
            GLib.idle_add(image.set_from_icon_name, "image-missing", Gtk.IconSize.DIALOG)
            if on_done:
                GLib.idle_add(on_done, False)
        finally:
            with _fg_lock:
                _foreground["pending"] -= 1
    with _fg_lock:
        _foreground["pending"] += 1
    _images.submit(safe)


def in_use(component):
    """True if this installed component is what the desktop is currently using."""
    kind = component.get("system")
    if kind == "plymouth":
        return system.current_plymouth() == component["name"]
    if kind == "sddm":
        return system.display_manager() == "sddm" and system.current_sddm_theme() == component["name"]
    if kind == "webgreeter":
        return system.current_web_greeter_theme() == component["name"]
    for part in component["provides"]:
        value = Path(component["path"]).as_uri() if part == "wallpapers" else component["name"]
        if desktop.get(part) == value:
            return True
    return False


def error_dialog(parent, title, err):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
                          buttons=Gtk.ButtonsType.CLOSE, text=title)
    d.format_secondary_text(str(err))
    d.run()
    d.destroy()


_renders = ThreadPoolExecutor(max_workers=2)  # theme previews each start a GTK process


class VariantPicker(Gtk.Popover):
    """Variants on the left; hovering (or arrowing through) one shows its preview on the right."""

    def __init__(self, window, key, kind, comps, relative_to):
        super().__init__(relative_to=relative_to, position=Gtk.PositionType.BOTTOM)
        self.win, self.key, self.kind = window, key, kind
        self.cache = {}  # path -> Pixbuf, or None while rendering
        self.showing = None

        box = Gtk.Box(spacing=12, margin=10)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=True)
        self.list.connect("row-selected", lambda _l, row: row and self.show(row.comp))
        self.list.connect("row-activated", lambda _l, row: self.choose(row.comp))
        for c in comps:
            row = Gtk.ListBoxRow()
            row.comp = c
            ev = Gtk.EventBox(above_child=False)
            ev.connect("enter-notify-event", lambda _e, _ev, row=row: self.list.select_row(row))
            line = Gtk.Box(spacing=8, margin=6, margin_end=12)
            mark = Gtk.Image.new_from_icon_name("object-select-symbolic" if in_use(c) else "", Gtk.IconSize.MENU)
            mark.set_size_request(16, -1)
            line.pack_start(mark, False, False, 0)
            line.pack_start(Gtk.Label(label=c["name"], xalign=0), True, True, 0)
            ev.add(line)
            row.add(ev)
            self.list.add(row)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                max_content_height=previews.H + 40, min_content_width=220)
        sw.add(self.list)
        box.pack_start(sw, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("view")
        self.image = Gtk.Image()
        self.image.set_size_request(previews.W, previews.H)
        frame.add(self.image)
        right.pack_start(frame, False, False, 0)
        self.caption = Gtk.Label(xalign=0)
        right.pack_start(self.caption, False, False, 0)
        hint = Gtk.Label(label="Click a variant to apply it", xalign=0)
        hint.get_style_context().add_class("dim-label")
        right.pack_start(hint, False, False, 0)
        box.pack_start(right, True, True, 0)
        self.add(box)
        box.show_all()

        # start on the variant in use, or the first one
        rows = self.list.get_children()
        self.list.select_row(next((r for r in rows if in_use(r.comp)), rows[0]))
        # warm up the rest in the background so hovering feels instant
        for c in comps:
            self._ensure(c)

    def _ensure(self, c):
        path = c["path"]
        if path in self.cache:
            return
        pb = previews.preview(c, self.kind)
        if pb is not None:
            self.cache[path] = pb
            return
        self.cache[path] = None

        def work():
            out = previews.theme_preview_path(Path(path), self.kind)
            if out:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(out), previews.W, previews.H, True)
            else:
                pb = Gtk.IconTheme.get_default().load_icon("image-missing", 64, 0)
            GLib.idle_add(self._rendered, path, pb)
        _renders.submit(lambda: _safe(work))

    def _rendered(self, path, pb):
        self.cache[path] = pb
        if self.showing == path:
            self.image.set_from_pixbuf(pb)

    def show(self, c):
        self.showing = c["path"]
        self.caption.set_markup(f"<b>{GLib.markup_escape_text(c['name'])}</b>"
                                + ("  <small>✓ in use</small>" if in_use(c) else "")
                                + ("  <small>⚠ for older Cinnamon: password prompts won't be styled</small>"
                                   if self.kind == "desktop" and desktop.cinnamon_theme_outdated(c["path"]) else ""))
        self._ensure(c)
        pb = self.cache.get(c["path"])
        if pb is None:
            self.image.set_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
        else:
            self.image.set_from_pixbuf(pb)

    def choose(self, c):
        self.popdown()
        installer.set_chosen(self.key, self.kind, c["name"])
        self.win.apply(c, self.kind)


def _safe(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"drape: preview failed: {e}", file=sys.stderr)


class ApplyControl(Gtk.Box):
    """[Apply | ▾]: Apply uses the variant in use or last picked; ▾ lists variants with previews."""

    def __init__(self, window, key, kind):
        super().__init__()
        self.win, self.key, self.kind = window, key, kind
        self.get_style_context().add_class("linked")
        apply = Gtk.Button(label="Apply")
        apply.get_style_context().add_class("suggested-action")
        apply.connect("clicked", self._apply)
        self.pack_start(apply, False, False, 0)
        comps = self._components()
        if len(comps) > 1:
            more = Gtk.Button(image=Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON))
            more.get_style_context().add_class("suggested-action")
            more.set_tooltip_text(f"Choose from {len(comps)} variants")
            more.connect("clicked", self._pick)
            self.pack_start(more, False, False, 0)
            apply.set_tooltip_text(f"Apply {self._target(comps)['name']}")

    def _components(self):
        entry = installer.load_manifest().get(self.key)
        if not entry:
            return []
        comps = entry["components"]
        if self.kind and self.kind != "wallpapers":
            comps = [c for c in comps if matches(self.kind, c)] or comps
        return comps

    def _target(self, comps):
        using = [c for c in comps if in_use(c)]
        if using:
            return using[0]
        chosen = installer.load_manifest().get(self.key, {}).get("chosen", {}).get(self.kind)
        return next((c for c in comps if c["name"] == chosen), comps[0])

    def _apply(self, _btn):
        comps = self._components()
        if self.kind == "wallpapers" and len(comps) > 1:
            self.win.choose_wallpaper(comps)
        elif comps:
            self.win.apply(self._target(comps), self.kind)

    def _pick(self, btn):
        comps = self._components()
        if self.kind == "wallpapers":
            self.win.choose_wallpaper(comps)
            return
        VariantPicker(self.win, self.key, self.kind, comps, btn).popup()


class Card(Gtk.FlowBoxChild):
    def __init__(self, window, kind, item):
        super().__init__()
        self.win, self.kind, self.item = window, kind, item

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("view")
        box.pack_start(frame, False, False, 0)

        self.image = Gtk.Image()
        self.image.set_size_request(CARD_W, CARD_H)
        overlay = Gtk.Overlay()
        overlay.add(self.image)
        ev = Gtk.EventBox()
        ev.add(overlay)
        ev.connect("button-release-event", lambda *_: window.show_details(kind, item))
        frame.add(ev)
        if item.previews:
            # starts when the card is first drawn: a running spinner redraws 60 times a second even off screen
            spinner = Gtk.Spinner(active=False, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            spinner.set_size_request(32, 32)
            overlay.add_overlay(spinner)
            alive = threading.Event()
            alive.set()
            self.connect("destroy", lambda *_: alive.clear())

            def first_draw(*_):
                # GTK only draws what's on screen, so off-screen cards and hidden tabs wait their turn
                self.image.disconnect(handler[0])
                spinner.start()
                load_image(pling.thumb_url(item.previews[0]), self.image, CARD_W, CARD_H,
                           on_done=lambda _ok: spinner.destroy(), alive=alive.is_set)
                return False
            handler = [self.image.connect("draw", first_draw)]
        else:
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)

        title = Gtk.Label(label=item.name, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        title.set_markup(f"<b>{GLib.markup_escape_text(item.name)}</b>")
        box.pack_start(title, False, False, 0)

        meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=32)
        meta.set_markup(f"<small>{GLib.markup_escape_text(item.author)} · ★ {item.score / 10:.1f} · "
                        f"{item.downloads:,} downloads</small>")
        meta.get_style_context().add_class("dim-label")
        box.pack_start(meta, False, False, 0)

        self.actions = Gtk.Box(spacing=6)
        box.pack_start(self.actions, False, False, 0)
        self.add(box)
        self.refresh()

    def refresh(self):
        for c in self.actions.get_children():
            c.destroy()
        installed = self.item.id in installer.load_manifest()
        if self.win.busy.get(self.item.id):
            bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
            self.win.busy[self.item.id]["bars"].append(bar)
            self.actions.pack_start(bar, True, True, 0)
        elif installed:
            self.actions.pack_start(ApplyControl(self.win, self.item.id, self.kind), False, False, 0)
            rm = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
            rm.set_tooltip_text("Remove")
            rm.connect("clicked", lambda _b: self.win.remove(self.item.id))
            self.actions.pack_end(rm, False, False, 0)
        elif self.item.files:
            b = Gtk.Button(label="Install")
            b.connect("clicked", lambda _b: self.win.install(self.item))
            self.actions.pack_start(b, False, False, 0)
            b2 = Gtk.Button(label="Install & apply")
            b2.get_style_context().add_class("suggested-action")
            b2.connect("clicked", lambda _b: self.win.install(self.item, apply_kind=self.kind))
            self.actions.pack_start(b2, False, False, 0)
        else:
            l = Gtk.Label(label="External download only")
            l.get_style_context().add_class("dim-label")
            self.actions.pack_start(l, False, False, 0)
        self.actions.show_all()


class BrowsePage(Gtk.Box):
    """One category's results: first batch as soon as it arrives, the rest behind it, more on scroll."""

    def __init__(self, window, kind):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.kind = window, kind
        self.generation = 0
        self.total = 0
        self.next_chunk = 0
        self.fetching = False
        self.loaded = False

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                valign=Gtk.Align.START, max_children_per_line=8,
                                margin=12, row_spacing=6, column_spacing=6)
        # "Asking gnome-look.org…" with a spinner, or a message
        self.status = Gtk.Box(spacing=10, margin=24, halign=Gtk.Align.CENTER, no_show_all=True)
        self.status_spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(wrap=True)
        self.status_label.get_style_context().add_class("dim-label")
        self.status.pack_start(self.status_spinner, False, False, 0)
        self.status.pack_start(self.status_label, False, False, 0)
        # spinner at the bottom while the next batch loads
        self.more = Gtk.Box(spacing=8, margin=16, halign=Gtk.Align.CENTER, no_show_all=True)
        more_spinner = Gtk.Spinner(active=True)
        more_label = Gtk.Label(label="Loading more…")
        more_label.get_style_context().add_class("dim-label")
        self.more.pack_start(more_spinner, False, False, 0)
        self.more.pack_start(more_label, False, False, 0)
        more_spinner.show()
        more_label.show()

        # explains what this tab shows on this computer (login screens, boot splash)
        self.banner = Gtk.Label(xalign=0, wrap=True, margin=12, margin_bottom=0, no_show_all=True, use_markup=True)
        self.banner.get_style_context().add_class("dim-label")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.banner, False, False, 0)
        if kind == "wm":
            inner.pack_start(WindowBordersHelp(window), False, False, 0)
        inner.pack_start(self.status, False, False, 0)
        inner.pack_start(self.flow, False, False, 0)
        inner.pack_start(self.more, False, False, 0)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.add(inner)
        self.scroller.get_vadjustment().connect("value-changed", lambda *_: self._maybe_more())
        self.scroller.get_vadjustment().connect("changed", lambda *_: self._maybe_more())
        self.connect("map", lambda *_: GLib.idle_add(self._maybe_more))
        self.pack_start(self.scroller, True, True, 0)

    def cards(self):
        return self.flow.get_children()

    def _set_status(self, text, busy=False):
        self.status_label.set_text(text or "")
        self.status_spinner.set_visible(busy)
        self.status_spinner.set_property("active", busy)
        self.status.set_visible(bool(text))
        self.status_label.show()

    def _params(self):
        return self.win.query(), self.win.sort()

    def _add(self, items):
        _ui_busy_until[0] = time.monotonic() + 2
        for it in items:
            self.flow.add(Card(self.win, self.kind, it))
        self.flow.show_all()

    def load(self):
        self.loaded = True
        self.generation += 1
        gen = self.generation
        for c in self.cards():
            c.destroy()
        self.next_chunk, self.total, self.fetching = 0, 0, False
        self.more.hide()
        categories, banner = self._scope()
        self.banner.set_markup(banner)
        self.banner.set_visible(bool(banner))
        self.categories = categories
        if categories == "":
            self._set_status("")
            return
        query, sort = self._params()

        # results from last time show instantly, then get refreshed
        shown = []
        for i in range(FIRST_CHUNKS):
            cached = pling.cached_search(self.kind, query, sort, i, CHUNK, categories)
            if not cached or not cached[0]:
                break
            self._add(cached[0])
            shown += [it.id for it in cached[0]]
            self.total = cached[1]
        if shown:
            self._set_status("")
        else:
            self._set_status("Asking gnome-look.org…", busy=True)

        def slow():
            if gen == self.generation and not self.cards() and self.status.get_visible():
                self._set_status("Still waiting for gnome-look.org - the connection seems slow. "
                                 "Results will show as soon as they arrive.", busy=True)
            return False
        GLib.timeout_add_seconds(6, slow)

        # the first few batches in parallel; shown in order as they arrive
        results, fresh = {}, []
        state = {"next": 0, "replaced": not shown}

        def arrived(i, result):
            if gen != self.generation:
                return
            results[i] = result
            while state["next"] in results:
                items, total = results.pop(state["next"])
                state["next"] += 1
                self.total = total
                fresh.extend(items)
                if state["replaced"]:
                    self._add(items)
                elif [it.id for it in fresh] != shown[:len(fresh)]:
                    # gnome-look changed since last time: swap in the fresh results
                    for c in self.cards():
                        c.destroy()
                    self._add(fresh)
                    state["replaced"] = True
            if state["next"]:
                self._set_status("" if self.cards() else "Nothing found.")
            self._maybe_more()

        def failed(e):
            if gen == self.generation and not self.cards():
                self._set_status(f"Couldn't reach gnome-look.org: {e}")

        for i in range(FIRST_CHUNKS):
            run_async(lambda i=i: pling.search(self.kind, query, sort, i, CHUNK, categories),
                      lambda r, i=i: arrived(i, r), failed)
        self.next_chunk = FIRST_CHUNKS

    def _has_more(self, chunks):
        return chunks * CHUNK < self.total

    def _maybe_more(self):
        """Infinite scroll: start the next batch when the bottom is within a couple of rows."""
        if self.fetching or not self.loaded or not self.cards() or not self._has_more(self.next_chunk):
            return
        # a hidden tab has no height, so it would always look "at the bottom" and load forever
        if self.win.stack.get_visible_child() is not self or not self.get_mapped():
            return
        adj = self.scroller.get_vadjustment()
        if adj.get_page_size() <= 0:
            return
        if adj.get_value() + adj.get_page_size() < adj.get_upper() - 2 * (CARD_H + 120):
            return
        self.fetching = True
        self.more.show()
        gen, chunk = self.generation, self.next_chunk
        query, sort = self._params()

        def done(result):
            if gen != self.generation:
                return
            self.fetching = False
            self.more.hide()
            self.next_chunk = chunk + 1
            self.total = result[1]
            known = {c.item.id for c in self.cards()}
            self._add([it for it in result[0] if it.id not in known])
            GLib.idle_add(self._maybe_more)  # still near the bottom (tall window)? keep going

        def failed(_e):
            if gen == self.generation:
                self.fetching = False
                self.more.hide()
        run_async(lambda: pling.search(self.kind, query, sort, chunk, CHUNK, self.categories), done, failed)

    def _scope(self):
        """(categories to search or None for the default, explanation) for this computer."""
        only = settings.get("only_applicable")
        if self.kind == "login":
            cats = system.login_categories(only)
            dm, greeter = system.display_manager(), system.lightdm_greeter()
            current = f"LightDM with {greeter}" if dm == "lightdm" else (dm or "unknown")
            if cats == "":
                return "", (f"Your login screen is <b>{GLib.markup_escape_text(current)}</b>. It doesn't use "
                            "downloadable themes: choose <b>⋯ → Use for login screen</b> on any installed "
                            "wallpaper, Controls theme, icon set or cursor, or see <b>Lock &amp; login</b>.\n\n"
                            "To browse themes for other login screens (SDDM, web greeters), turn off "
                            "<b>Only show themes that work on this computer</b> in the ☰ menu.")
            note = f"Your login screen is <b>{GLib.markup_escape_text(current)}</b>."
            if not only:
                note += " Showing themes for every login screen - drape tells you if one needs something installed."
            return cats, note
        if self.kind == "desktop":
            current = desktop.get("desktop")
            d = desktop.find_theme_dir(current) if current else None
            if d and desktop.cinnamon_theme_outdated(d):
                return None, (f"⚠ Your Desktop theme <b>{GLib.markup_escape_text(current)}</b> was "
                              f"{desktop.OUTDATED_NOTE}. Pick a newer one to fix that.")
        if self.kind == "boot" and not system.plymouth_installed():
            return None, ("Plymouth, which draws the boot splash, isn't installed. drape will offer to install "
                          "it when you apply one.")
        return None, ""

    def refresh_cards(self, item_id=None):
        for c in self.cards():
            if item_id in (None, c.item.id):
                c.refresh()


def entry_image(key, entry, image, width, height, wallpaper=None):
    """Show a picture people recognise: the gnome-look preview, or the wallpaper itself."""
    walls = [c for c in entry["components"] if c["provides"] == ["wallpapers"]]
    if wallpaper:
        load_image(wallpaper["path"], image, width, height)
    elif entry.get("preview"):
        load_image(pling.thumb_url(entry["preview"]) if width <= 300 else entry["preview"], image, width, height)
    elif walls:
        load_image(walls[0]["path"], image, width, height)
    elif key.isdigit():
        # installed before previews were recorded: look it up once and remember it
        def found(item):
            if item.previews:
                installer.set_preview(key, item.previews[0])
                load_image(item.previews[0], image, width, height)
            else:
                image.set_from_icon_name("preferences-desktop-theme", Gtk.IconSize.DIALOG)
        run_async(lambda: pling.get(key), found,
                  lambda _e: image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG))
    else:
        image.set_from_icon_name("preferences-desktop-theme", Gtk.IconSize.DIALOG)


class InstalledCard(Gtk.FlowBoxChild):
    """An installed item in one category's grid; for wallpapers, a single image of a pack."""

    def __init__(self, window, kind, key, entry, update=None, wallpaper=None):
        super().__init__()
        self.win, self.kind, self.key, self.entry = window, kind, key, entry
        comps = [wallpaper] if wallpaper else \
            [c for c in entry["components"] if matches(kind, c)] or entry["components"]
        using = [c for c in comps if in_use(c)]

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("view")
        image = Gtk.Image.new_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
        image.set_size_request(CARD_W, CARD_H)
        ev = Gtk.EventBox()
        ev.add(image)
        if wallpaper:
            ev.set_tooltip_text("Set as wallpaper")
            ev.connect("button-release-event", lambda *_: window.apply(wallpaper))
        elif key.isdigit():
            ev.set_tooltip_text("Details")
            ev.connect("button-release-event", lambda *_: window.show_details_by_id(kind, key))
        frame.add(ev)
        entry_image(key, entry, image, CARD_W, CARD_H, wallpaper)
        box.pack_start(frame, False, False, 0)

        title_text = Path(wallpaper["path"]).stem if wallpaper else entry["title"]
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        title.set_markup(f"<b>{GLib.markup_escape_text(title_text)}</b>")
        box.pack_start(title, False, False, 0)

        if wallpaper:
            detail = entry["title"]
        elif using:
            detail = using[0]["name"] if len(comps) > 1 else ""
        else:
            detail = f"{len(comps)} variants" if len(comps) > 1 else ""
        meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=32)
        markup = "<b>✓ In use</b>" if using else ""
        outdated = kind == "desktop" and any(desktop.cinnamon_theme_outdated(c["path"]) for c in comps)
        if outdated:
            markup += (" · " if markup else "") + "⚠ For older Cinnamon"
            meta.set_tooltip_text("This theme was " + desktop.OUTDATED_NOTE + ".")
        if detail:
            markup += (" · " if markup else "") + GLib.markup_escape_text(detail)
        meta.set_markup(f"<small>{markup or ' '}</small>")
        if not using:
            meta.get_style_context().add_class("dim-label")
        box.pack_start(meta, False, False, 0)

        actions = Gtk.Box(spacing=6)
        if wallpaper:
            b = Gtk.Button(label="Set wallpaper")
            b.get_style_context().add_class("suggested-action")
            b.connect("clicked", lambda _b: window.apply(wallpaper))
            actions.pack_start(b, False, False, 0)
        else:
            actions.pack_start(ApplyControl(window, key, kind), False, False, 0)
        if update:
            ub = Gtk.Button(label="Update")
            ub.connect("clicked", lambda _b: window.install(update))
            actions.pack_start(ub, False, False, 0)
        extra = [("Show installed files…", lambda *_: window.show_files(key, wallpaper))]
        target = wallpaper or (comps[0] if len(comps) == 1 else None)
        if kind in ("wallpapers", "gtk", "icons", "cursors"):
            extra.append(("Use for login screen…", lambda *_: window.use_for_login(kind, target) if target
                          else window.pick_variant_for_login(kind, comps)))
        if kind == "wallpapers" and desktop.current_desktop() == "gnome":
            extra.append(("Use for lock screen", lambda *_: window.use_for_lock(wallpaper)))
        if extra:
            menu = Gtk.Menu()
            for label, cb in extra:
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", cb)
                menu.append(mi)
            menu.show_all()
            more = Gtk.MenuButton(popup=menu, image=Gtk.Image.new_from_icon_name("view-more-symbolic",
                                                                                 Gtk.IconSize.BUTTON))
            more.set_tooltip_text("More")
            actions.pack_end(more, False, False, 0)
        rm = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
        if wallpaper:
            rm.set_tooltip_text("Remove this wallpaper")
            rm.connect("clicked", lambda _b: window.remove_wallpaper(key, wallpaper))
        else:
            rm.set_tooltip_text("Remove")
            rm.connect("clicked", lambda _b: window.remove(key))
        actions.pack_end(rm, False, False, 0)
        box.pack_start(actions, False, False, 0)
        self.add(box)


# ---------------------------------------------------------------- what's in use right now

ACTIVE_PARTS = [("gtk", "Controls"), ("wm", "Window borders"), ("desktop", "Desktop"), ("icons", "Icons"),
                ("cursors", "Cursors"), ("wallpapers", "Wallpaper"), ("login", "Login screen"),
                ("boot", "Boot splash")]


def locate_theme(part, name):
    """Folder of the theme in use for a part, looked up the way the desktop does."""
    if not name:
        return None
    if part in ("gtk", "wm", "desktop"):
        return desktop.find_theme_dir(name)
    for base in (installer.ICONS_DIR, installer.CURSORS_DIR, Path("/usr/share/icons")):
        if (base / name).is_dir():
            return base / name
    return None


def theme_source(path):
    """(text, manifest key or None) saying where a theme in use came from."""
    if path is None:
        return "Built in", None
    for key, entry in installer.load_manifest().items():
        if str(path) in entry.get("paths", []):
            return f"Installed with drape · {entry['title']}", key
    if str(path).startswith(str(Path.home())):
        return "In your home folder (added outside drape)", None
    return "Came with your system", None


def _fit(pb, width, height):
    scale = min(width / pb.get_width(), height / pb.get_height(), 1)
    return pb.scale_simple(max(1, int(pb.get_width() * scale)), max(1, int(pb.get_height() * scale)),
                           GdkPixbuf.InterpType.BILINEAR)


def plymouth_picture(theme_dir):
    """A still for a boot splash: its own preview if it has one, else a frame from the middle of its animation."""
    for name in ("preview.png", "screenshot.png", "logo.png"):
        if (theme_dir / name).is_file():
            return theme_dir / name
    frames = sorted(theme_dir.glob("*.png"), key=lambda p: (len(p.name), p.name))
    return frames[len(frames) // 2] if frames else None


class ActiveCard(Gtk.FlowBoxChild):
    """One part of the current look: a preview of exactly what's in use, its name and where it came from."""

    def __init__(self, window, part, label):
        super().__init__()
        self.win = window
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=8)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("view")
        self.image = Gtk.Image.new_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
        self.image.set_size_request(CARD_W, CARD_H)
        frame.add(self.image)
        box.pack_start(frame, False, False, 0)

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<small><b>{GLib.markup_escape_text(label.upper())}</b></small>")
        heading.get_style_context().add_class("dim-label")
        box.pack_start(heading, False, False, 0)
        name, detail, key, target = self._describe(part)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        title.set_markup(f"<b>{GLib.markup_escape_text(name)}</b>")
        title.set_tooltip_text(name)
        box.pack_start(title, False, False, 0)
        info = Gtk.Label(label=detail, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=34)
        info.set_tooltip_text(detail)
        info.get_style_context().add_class("dim-label")
        box.pack_start(info, False, False, 0)

        actions = Gtk.Box(spacing=6)
        change = Gtk.Button(label="Change")
        change.connect("clicked", lambda _b: window.go_to(target))
        actions.pack_start(change, False, False, 0)
        if key:
            files = Gtk.Button.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.BUTTON)
            files.set_tooltip_text("Show installed files")
            files.connect("clicked", lambda _b: window.show_files(key))
            actions.pack_end(files, False, False, 0)
        box.pack_start(actions, False, False, 0)
        self.add(box)

    def _show_pixbuf(self, pb):
        if pb is None:
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
        else:
            self.image.set_from_pixbuf(_fit(pb, CARD_W, CARD_H))
        return False

    def _preview_theme(self, part, path):
        """Draw the exact theme in use from its own files (works for themes drape didn't install)."""
        comp = {"path": str(path), "provides": [part]}
        pb = previews.preview(comp, part)
        if pb is not None:
            self._show_pixbuf(pb)
            return

        def work():
            out = previews.theme_preview_path(path, part)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(out), CARD_W, CARD_H, True) if out else None
            GLib.idle_add(self._show_pixbuf, pb)
        _renders.submit(lambda: _safe(work))

    def _describe(self, part):
        """(name, detail line, drape manifest key, page for Change) - and start the preview."""
        if part in ("gtk", "wm", "desktop", "icons", "cursors"):
            name = desktop.get(part) or ""
            path = locate_theme(part, name)
            source, key = theme_source(path)
            if path is not None:
                self._preview_theme(part, path)
            else:
                self.image.set_from_icon_name("preferences-desktop-theme", Gtk.IconSize.DIALOG)
            if part == "desktop" and path is not None and desktop.cinnamon_theme_outdated(path):
                source = "⚠ For older Cinnamon · " + source
            return name or "Default", source, key, part
        if part == "wallpapers":
            uri = desktop.get("wallpapers") or ""
            path = Path(GLib.filename_from_uri(uri)[0]) if uri.startswith("file://") else None
            if path and path.is_file():
                load_image(str(path), self.image, CARD_W, CARD_H)
                source, key = theme_source(path)
                if key is None and str(path).startswith(str(installer.WALLPAPER_DIR)):
                    source, key = theme_source(path.parent)
                return path.stem, source, key, "wallpapers"
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            return "None", "No picture set", None, "wallpapers"
        if part == "login":
            dm, greeter = system.display_manager(), system.lightdm_greeter()
            if dm == "lightdm" and greeter in system.GTK_GREETERS:
                cur = system.greeter_settings(greeter)
                bg = cur.get("background")
                if bg and Path(bg).is_file():
                    load_image(bg, self.image, CARD_W, CARD_H)
                else:
                    self.image.set_from_icon_name("system-users", Gtk.IconSize.DIALOG)
                parts = [f"{label} {cur[k]}" for k, label in (("theme-name", "Controls"), ("icon-theme-name", "Icons"),
                                                              ("cursor-theme-name", "Cursor")) if cur.get(k)]
                return f"LightDM · {greeter}", " · ".join(parts) or "Default look", None, "lock"
            if dm == "sddm":
                theme = system.current_sddm_theme()
                shot = None
                tdir = Path("/usr/share/sddm/themes") / (theme or "")
                if theme and (tdir / "metadata.desktop").is_file():
                    for line in (tdir / "metadata.desktop").read_text(errors="replace").splitlines():
                        if line.startswith("Screenshot="):
                            shot = tdir / line.split("=", 1)[1].strip()
                if shot and shot.is_file():
                    load_image(str(shot), self.image, CARD_W, CARD_H)
                else:
                    self.image.set_from_icon_name("system-users", Gtk.IconSize.DIALOG)
                return "SDDM", f"Theme: {theme}" if theme else "SDDM's built-in theme", None, "login"
            self.image.set_from_icon_name("system-users", Gtk.IconSize.DIALOG)
            return dm or "Unknown", "", None, "lock"
        if part == "boot":
            name = system.current_plymouth()
            tdir = system.PLYMOUTH_DIR / (name or "")
            pic = plymouth_picture(tdir) if name and tdir.is_dir() else None
            if pic:
                def work():
                    pb = GdkPixbuf.Pixbuf.new_from_file(str(pic))
                    # boot splashes are drawn on black; show it that way
                    bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, CARD_W, CARD_H)
                    bg.fill(0x000000FF)
                    pb = _fit(pb, CARD_W - 40, CARD_H - 40)
                    pb.composite(bg, (CARD_W - pb.get_width()) // 2, (CARD_H - pb.get_height()) // 2,
                                 pb.get_width(), pb.get_height(), (CARD_W - pb.get_width()) // 2,
                                 (CARD_H - pb.get_height()) // 2, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
                    GLib.idle_add(self._show_pixbuf, bg)
                _renders.submit(lambda: _safe(work))
            else:
                self.image.set_from_icon_name("system-run", Gtk.IconSize.DIALOG)
            source, key = theme_source(tdir if name else None)
            if key is None and name:
                source = "Came with your system" if not (tdir / root_helper.MARKER).exists() else "Installed with drape"
            return name or "None", source if system.plymouth_installed() else "Plymouth isn't installed", key, "boot"
        raise KeyError(part)


class InstalledPage(Gtk.Box):
    """Installed items in the same tabs and grid as browsing."""

    def __init__(self, window, kinds):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = window
        self.updates = {}
        self.tabs = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hhomogeneous=False)
        self.grids = {}
        for k in [pling.Kind("active", "In use", "")] + kinds + [pling.Kind("other", "Other", "")]:
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               valign=Gtk.Align.START, max_children_per_line=8,
                               margin=12, row_spacing=6, column_spacing=6)
            empty = Gtk.Label(label=f"No {k.label.lower()} installed yet.", margin=48, no_show_all=True)
            if k.key == "active":  # a fixed breakdown, not a grid of equal cards
                flow.set_homogeneous(False)
            empty.get_style_context().add_class("dim-label")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            inner.pack_start(empty, False, False, 0)
            inner.pack_start(flow, False, False, 0)
            sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            sw.add(inner)
            self.tabs.add_titled(sw, k.key, k.label)
            self.grids[k.key] = (k, sw, flow, empty)

        bar = Gtk.Box(spacing=6, margin=12, margin_bottom=0)
        self.note = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.note.get_style_context().add_class("dim-label")
        bar.pack_start(self.note, True, True, 0)
        self.updates_btn = Gtk.Button(label="Check for updates")
        self.updates_btn.connect("clicked", self.check_updates)
        bar.pack_end(self.updates_btn, False, False, 0)
        self.pack_start(bar, False, False, 0)

        # category tabs that wrap onto more lines on narrow windows (a Gtk.StackSwitcher can't shrink)
        self.chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                                 column_spacing=6, row_spacing=6, margin=12, margin_bottom=0,
                                 max_children_per_line=len(self.grids))
        self.chip = {}
        group = None
        for key in self.grids:
            rb = Gtk.RadioButton.new_with_label_from_widget(group, self.grids[key][0].label)
            rb.set_mode(False)  # looks like a toggle button
            group = group or rb
            rb.connect("toggled", lambda b, k=key: b.get_active() and self.tabs.set_visible_child_name(k))
            self.chips.add(rb)
            self.chip[key] = rb
        self.tabs.connect("notify::visible-child", lambda *_: self._sync_chip())
        self.pack_start(self.chips, False, False, 0)
        # a short explanation for tabs that need one
        self.hint = WindowBordersHelp(window)
        self.hint.set_no_show_all(True)
        self.pack_start(self.hint, False, False, 0)
        self.pack_start(self.tabs, True, True, 0)

    def _sync_chip(self):
        name = self.tabs.get_visible_child_name()
        if name == "wm":
            self.hint.show_all()
        else:
            self.hint.hide()
        rb = self.chip.get(name)
        if rb and not rb.get_active():
            rb.set_active(True)

    def load(self):
        m = installer.load_manifest()
        placed = set()
        counts = {}
        for key_name, (k, sw, flow, empty) in self.grids.items():
            for c in flow.get_children():
                c.destroy()
            if key_name == "active":
                for part, label in ACTIVE_PARTS:
                    if part in ("login", "boot", "wallpapers") or desktop.supported(part):
                        flow.add(ActiveCard(self.win, part, label))
                flow.show_all()
                counts["active"] = len(flow.get_children())
                continue
            n = 0
            for key, e in sorted(m.items(), key=lambda kv: kv[1]["title"].lower()):
                if key_name == "other":
                    if key in placed:
                        continue
                elif not any(matches(key_name, c) for c in e["components"]):
                    continue
                placed.add(key)
                if key_name == "wallpapers":
                    for c in e["components"]:
                        if c["provides"] == ["wallpapers"]:
                            flow.add(InstalledCard(self.win, key_name, key, e, wallpaper=c))
                            n += 1
                else:
                    flow.add(InstalledCard(self.win, key_name, key, e, self.updates.get(key)))
                    n += 1
            counts[key_name] = n
            self.tabs.child_set_property(sw, "title", f"{k.label} ({n})" if n else k.label)
            self.chip[key_name].set_label(f"{k.label} ({n})" if n else k.label)
            empty.set_visible(n == 0)
            flow.show_all()
        # "Other" only appears if something didn't fit a category
        self.grids["other"][1].set_visible(counts["other"] > 0)
        self.chip["other"].get_parent().set_visible(counts["other"] > 0)
        if not m:
            self.note.set_text("Nothing installed yet")
        elif self.updates:
            self.note.set_text(f"{len(self.updates)} update(s) available")
        else:
            self.note.set_text(f"{len(m)} item(s) installed")
        # land on a tab that has something in it
        current = self.tabs.get_visible_child_name()
        if not counts.get(current):
            first = next((k for k in self.grids if counts.get(k)), None)
            if first:
                self.tabs.set_visible_child_name(first)

    def check_updates(self, _btn):
        keys = [k for k in installer.load_manifest() if k.isdigit()]
        self.updates_btn.set_sensitive(False)
        self.note.set_text("Checking…")

        def work():
            m = installer.load_manifest()
            found = {}
            for k in keys:
                it = pling.get(k)
                if it.changed and it.changed != m[k].get("changed"):
                    found[k] = it
            return found

        def done(found):
            self.updates = found
            self.updates_btn.set_sensitive(True)
            self.load()
            if not found:
                self.note.set_text("Everything is up to date")

        def error(e):
            self.updates_btn.set_sensitive(True)
            self.note.set_text(str(e))

        run_async(work, done, error)


class DetailsDialog(Gtk.Dialog):
    def __init__(self, window, kind, item):
        super().__init__(title=item.name, transient_for=window, modal=True, use_header_bar=True)
        self.set_default_size(820, 640)
        self.win, self.kind, self.item = window, kind, item
        self.index = 0

        area = self.get_content_area()
        area.set_spacing(10)
        area.set_border_width(12)

        self.image = Gtk.Image.new_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
        self.image.set_size_request(780, 400)
        nav = Gtk.Box(spacing=6)
        prev = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        nxt = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        prev.connect("clicked", lambda _b: self.show_preview(-1))
        nxt.connect("clicked", lambda _b: self.show_preview(1))
        for b in (prev, nxt):
            b.set_valign(Gtk.Align.CENTER)
            b.set_sensitive(len(item.previews) > 1)
        nav.pack_start(prev, False, False, 0)
        nav.pack_start(self.image, True, True, 0)
        nav.pack_start(nxt, False, False, 0)
        area.pack_start(nav, False, False, 0)
        self.show_preview(0)

        meta = Gtk.Label(xalign=0, wrap=True)
        meta.set_markup(f"by <b>{GLib.markup_escape_text(item.author)}</b> · updated {item.changed[:10]} · "
                        f"{item.downloads:,} downloads · "
                        f"<a href=\"{GLib.markup_escape_text(item.page)}\">view on gnome-look.org</a>")
        area.pack_start(meta, False, False, 0)
        summary = Gtk.Label(label=item.summary, xalign=0, wrap=True, selectable=True)
        area.pack_start(summary, False, False, 0)

        row = Gtk.Box(spacing=8)
        self.files = Gtk.ComboBoxText()
        for f in item.files:
            self.files.append(str(f.index), f"{f.name}  ({f.size_kb / 1024:.1f} MB)")
        if item.files:
            self.files.set_active_id(str(item.best_file().index))
            row.pack_start(Gtk.Label(label="Variant:"), False, False, 0)
            row.pack_start(self.files, True, True, 0)
        area.pack_start(row, False, False, 0)
        self.files.set_visible(len(item.files) > 0)

        installed = item.id in installer.load_manifest()
        if installed:
            rm = self.add_button("Remove", 1)
            rm.get_style_context().add_class("destructive-action")
        if item.files:
            b = self.add_button("Reinstall" if installed else "Install", 2)
            if not installed:
                b2 = self.add_button("Install & apply", 3)
                b2.get_style_context().add_class("suggested-action")
        self.connect("response", self.on_response)
        self.show_all()

    def show_preview(self, step):
        if not self.item.previews:
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            return
        self.index = (self.index + step) % len(self.item.previews)
        load_image(self.item.previews[self.index], self.image, 780, 400)

    def on_response(self, _d, resp):
        file_index = int(self.files.get_active_id()) if self.item.files else None
        if resp == 1:
            self.win.remove(self.item.id)
        elif resp in (2, 3):
            self.win.install(self.item, file_index, apply_kind=self.kind if resp == 3 else None)
        self.destroy()


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="drape")
        # start at a comfortable size that fits the screen's usable area (small laptops too)
        width, height = 1180, 760
        display = Gdk.Display.get_default()
        monitor = display and (display.get_primary_monitor() or display.get_monitor(0))
        if monitor:
            area = monitor.get_workarea()
            width, height = min(width, area.width - 40), min(height, area.height - 40)
        self.set_default_size(width, height)
        self.set_app_icon()
        self.busy = {}  # item id -> {"bars": [...]}

        hb = Gtk.HeaderBar(show_close_button=True, title="drape",
                           subtitle="Themes, icons, cursors & wallpapers from gnome-look.org")
        self.set_titlebar(hb)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=18)
        self.search.connect("search-changed", lambda _e: self.reload_current())
        hb.pack_end(self.search)
        self.sort_combo = Gtk.ComboBoxText()
        # Pling's "top" favours brand-new items with a handful of votes, so popularity is the default
        for key, label in [("downloads", "Most downloaded"), ("top", "Top rated"), ("new", "Newest"),
                           ("alpha", "A–Z")]:
            self.sort_combo.append(key, label)
        self.sort_combo.set_active_id("downloads")
        self.sort_combo.connect("changed", lambda _c: self.reload_all())
        menu = Gtk.Menu()
        only = Gtk.CheckMenuItem(label="Only show themes that work on this computer",
                                 active=settings.get("only_applicable"))
        only.connect("toggled", self._toggle_applicable)
        menu.append(only)
        self.window_check_item = Gtk.CheckMenuItem(label="Check open windows on the Window borders tab automatically",
                                                   active=settings.get("window_check") == "always")
        self.window_check_item.connect("toggled", lambda it: settings.set(
            "window_check", "always" if it.get_active() else "ask"))
        menu.append(self.window_check_item)
        menu.append(Gtk.SeparatorMenuItem())
        heading = Gtk.MenuItem(label="Animate previews", sensitive=False)
        menu.append(heading)
        group = None
        for mode, label in (("auto", "Automatically (on hover if it gets heavy)"), ("always", "Always"),
                            ("hover", "Only on hover")):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or item
            item.set_active(settings.get("animations") == mode)
            item.connect("toggled", lambda it, m=mode: it.get_active() and self._set_animations(m))
            menu.append(item)
        menu.show_all()
        hb.pack_end(Gtk.MenuButton(popup=menu, image=Gtk.Image.new_from_icon_name("open-menu-symbolic",
                                                                                  Gtk.IconSize.BUTTON)))
        hb.pack_end(self.sort_combo)

        # each page asks only for its own width, so one wide page can't stop the window shrinking
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hhomogeneous=False)
        self.pages = {}
        for k in pling.KINDS:
            if k.key not in ("wallpapers", "login", "boot") and not desktop.supported(k.key):
                continue  # e.g. no "Desktop" (Cinnamon theme) page on GNOME
            p = BrowsePage(self, k.key)
            self.pages[k.key] = p
            self.stack.add_titled(p, k.key, k.label)
        self.installed = InstalledPage(self, [k for k in pling.KINDS if k.key in self.pages])
        self.stack.add_titled(self.installed, "installed", "Installed")
        from .lockpage import LockLoginPage
        self.lockpage = LockLoginPage(self, login_commands)
        self.stack.add_titled(self.lockpage, "lock", "Lock & login")
        self.stack.connect("notify::visible-child", lambda *_: self.on_page())

        side = self._sidebar()
        paned = Gtk.Box()
        paned.pack_start(side, False, False, 0)
        paned.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        paned.pack_start(self.stack, True, True, 0)

        self.infobar = Gtk.InfoBar(show_close_button=True, no_show_all=True)
        self.infobar.connect("response", lambda b, _r: b.hide())
        self.info_label = Gtk.Label(wrap=True, xalign=0)
        self.infobar.get_content_area().add(self.info_label)
        self.info_label.show()
        self.info_action = Gtk.Button(no_show_all=True, valign=Gtk.Align.CENTER)
        self.info_action_cb = None
        self.info_action.connect("clicked", lambda _b: self.info_action_cb and self.info_action_cb())
        self.infobar.get_content_area().pack_end(self.info_action, False, False, 0)
        self._sample = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(self.infobar, False, False, 0)
        root.pack_start(paned, True, True, 0)
        self.add(root)
        self.show_all()
        self.on_page()
        ANIMATIONS.on_switch = self._animations_switched
        self._closing = threading.Event()
        self.connect("destroy", lambda *_: self._closing.set())
        GLib.timeout_add_seconds(4, self._start_prefetch)

    def set_app_icon(self):
        """The drape mark: from the icon theme once installed, else straight from the repo."""
        if Gtk.IconTheme.get_default().has_icon(APP_ID):
            Gtk.Window.set_default_icon_name(APP_ID)
            self.set_icon_name(APP_ID)
            return
        svg = Path(__file__).resolve().parent.parent / "data" / f"{APP_ID}.svg"
        try:
            Gtk.Window.set_default_icon_from_file(str(svg))
            self.set_icon_from_file(str(svg))
        except GLib.Error:
            self.set_icon_name("preferences-desktop-theme")

    # pages below the "Settings" divider in the sidebar; everything above is for finding and applying themes
    SETTINGS_PAGES = ("lock",)

    def _sidebar(self):
        """Like Gtk.StackSidebar, plus a labelled divider between theme pages and settings pages."""
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        lb.get_style_context().add_class("sidebar")
        rows = {}
        for child in self.stack.get_children():
            name = self.stack.child_get_property(child, "name")
            row = Gtk.ListBoxRow()
            row.page = name
            row.add(Gtk.Label(label=self.stack.child_get_property(child, "title"), xalign=0,
                              margin=6, margin_start=10, margin_end=10))
            lb.add(row)
            rows[name] = row

        def header(row, before):
            if row.page in self.SETTINGS_PAGES and (before is None or before.page not in self.SETTINGS_PAGES):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10)
                box.pack_start(Gtk.Separator(), False, False, 0)
                label = Gtk.Label(xalign=0, margin_start=10, margin_top=4)
                label.set_markup("<small><b>SETTINGS</b></small>")
                label.get_style_context().add_class("dim-label")
                box.pack_start(label, False, False, 0)
                box.show_all()
                row.set_header(box)
            else:
                row.set_header(None)
        lb.set_header_func(header)

        syncing = {"on": False}

        def selected(_lb, row):
            if row and not syncing["on"]:
                self.stack.set_visible_child_name(row.page)
        lb.connect("row-selected", selected)

        def follow(*_):
            # keep the highlight right when a page is opened some other way (e.g. after an install link)
            row = rows.get(self.stack.get_visible_child_name())
            if row and lb.get_selected_row() is not row:
                syncing["on"] = True
                lb.select_row(row)
                syncing["on"] = False
        self.stack.connect("notify::visible-child", follow)
        follow()

        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_size_request(170, -1)
        sw.add(lb)
        return sw

    def _start_prefetch(self):
        """Quietly cache the first results and previews of every tab, so opening one is instant.
        One download at a time, and paused whenever previews on screen are still loading."""
        sort = self.sort()
        plan = []
        for kind, page in self.pages.items():
            categories, _ = page._scope()
            if categories != "":
                plan.append((kind, categories))

        def idle_wait():
            while _foreground["pending"] > 0 and not self._closing.is_set():
                self._closing.wait(0.5)
            return not self._closing.is_set()

        def run():
            for kind, categories in plan:
                for chunk in range(FIRST_CHUNKS):
                    if not idle_wait():
                        return
                    cached = pling.cached_search(kind, "", sort, chunk, CHUNK, categories, max_age=6 * 3600)
                    try:
                        items = cached[0] if cached else pling.search(kind, "", sort, chunk, CHUNK, categories)[0]
                    except pling.PlingError:
                        return  # offline: try again next launch
                    for it in items:
                        if it.previews and idle_wait():
                            try:
                                _fetch_thumb(pling.thumb_url(it.previews[0]), CARD_W, CARD_H)
                            except Exception:  # noqa: BLE001 - best effort
                                pass
        threading.Thread(target=run, daemon=True).start()
        return False

    # ------------------------------------------------------------ helpers
    def sync_menu(self):
        self.window_check_item.set_active(settings.get("window_check") == "always")

    def _set_animations(self, mode):
        settings.set("animations", mode)
        ANIMATIONS.refresh()

    def _animations_switched(self, pct):
        self.notify(f"Animated previews were using about {pct:.0f}% CPU, so they now play when you hover "
                    "over them. Change this in the ☰ menu.")

    def _toggle_applicable(self, item):
        settings.set("only_applicable", item.get_active())
        for key in ("login", "boot"):
            if key in self.pages:
                self.pages[key].loaded = False
        self.on_page()

    def query(self):
        return self.search.get_text().strip()

    def sort(self):
        return self.sort_combo.get_active_id()

    def notify(self, text, kind=Gtk.MessageType.INFO, action=None):
        """Show a message in the bar at the top; action=(label, callback) adds a button."""
        self.info_action.set_visible(action is not None)
        if action:
            self.info_action.set_label(action[0])
            self.info_action_cb = action[1]
        self.info_label.set_text(text)
        self.infobar.set_message_type(kind)
        self.infobar.show()

    def on_page(self):
        child = self.stack.get_visible_child()
        searchable = child not in (self.installed, self.lockpage)
        self.search.set_sensitive(searchable)
        self.sort_combo.set_sensitive(searchable)
        if child is self.lockpage:
            self.lockpage.load()
        elif child is self.installed:
            self.installed.load()
        elif not child.loaded:
            child.load()

    def reload_current(self):
        child = self.stack.get_visible_child()
        if child in self.pages.values():
            child.load()
        for p in self.pages.values():
            if p is not child:
                p.loaded = False

    def reload_all(self):
        for p in self.pages.values():
            p.loaded = False
        self.on_page()

    def refresh_item(self, item_id=None):
        for p in self.pages.values():
            p.refresh_cards(item_id)
        if self.stack.get_visible_child() is self.installed:
            self.installed.load()

    # ------------------------------------------------------------ actions
    def show_details(self, kind, item):
        DetailsDialog(self, kind, item)

    def show_details_by_id(self, kind, key):
        run_async(lambda: pling.get(key), lambda item: self.show_details(kind, item),
                  lambda e: error_dialog(self, "Couldn't load details", e))

    def remove_wallpaper(self, key, component):
        installer.remove_component(key, component["path"])
        self.notify(f"Removed {Path(component['path']).name}.")
        self.refresh_item()

    def install(self, item, file_index=None, apply_kind=None):
        if item.id in self.busy:
            return
        self.busy[item.id] = {"bars": []}
        self.refresh_item(item.id)

        def progress(done, total):
            def update():
                for bar in self.busy.get(item.id, {}).get("bars", []):
                    if total:
                        bar.set_fraction(done / total)
                    else:
                        bar.pulse()
            GLib.idle_add(update)

        flags = {"foreign": False, "items": False}  # what the user agreed to replace

        def work():
            fresh = pling.get(item.id)  # download links are signed and expire
            try:
                return installer.install_item(fresh, file_index, progress, flags["foreign"], flags["items"])
            except installer.ConflictError as e:
                return e  # ask the user on the main thread
            except installer.InstallError as e:
                if "wasn't installed by drape" not in str(e):
                    raise
                return e

        def retry():
            self.busy[item.id] = {"bars": []}
            self.refresh_item(item.id)
            run_async(work, done, error)

        def done(result):
            self.busy.pop(item.id, None)
            if isinstance(result, installer.ConflictError):
                self.refresh_item(item.id)
                self.resolve_conflict(result, lambda: (flags.update(items=True), retry()))
                return
            if isinstance(result, installer.InstallError):
                self.refresh_item(item.id)
                if self.confirm_overwrite(str(result)):
                    flags["foreign"] = True
                    retry()
                return
            self.installed.updates.pop(item.id, None)
            self.refresh_item(item.id)
            if any(c.get("system") for c in result["components"]) and not apply_kind:
                self.notify(f"Downloaded {item.name}. Apply it to install it for the whole system "
                            "(you'll be asked for your password).")
                return
            if apply_kind:
                comps = [c for c in result["components"] if matches(apply_kind, c)] or result["components"]
                if len(comps) == 1 or apply_kind == "wallpapers":
                    self.apply(comps[0], apply_kind)
                else:
                    self.notify(f"Installed {item.name} — it has {len(comps)} variants, pick one with Apply.")
            else:
                self.notify(f"Installed {item.name}.")

        def error(e):
            self.busy.pop(item.id, None)
            self.refresh_item(item.id)
            error_dialog(self, f"Couldn't install {item.name}", e)

        run_async(work, done, error)

    def resolve_conflict(self, err, then):
        """Another installed item has a theme with the same name: offer to uninstall it and go ahead."""
        esc = GLib.markup_escape_text
        m = installer.load_manifest()
        owners = [(k, o) for k, o in err.owners.items() if k in m]
        names = sorted({n for _k, o in owners for n in o["names"]})
        olds = " and ".join(f"<b>{esc(o['title'])}</b>" for _k, o in owners)
        text = (f"<b>{esc(err.title)}</b> installs a theme called <b>{esc(', '.join(names))}</b>, but {olds} "
                f"already has one with that name, and only one can be installed.\n\n"
                f"Replacing uninstalls {olds} (everything it installed) and installs <b>{esc(err.title)}</b>.")
        if any(in_use(c) for k, _o in owners for c in m[k]["components"]):
            text += "\n\nIt's in use right now; the new theme takes its place."
        copies = [c for k, _o in owners for c in system_copies(m[k])]
        if copies:
            text += "\n\nIt also has copies for the login screen or boot splash, so you'll be asked for your password."
        if not self.ask(f"Replace {', '.join(o['title'] for _k, o in owners)}?", text, "Replace", destructive=True):
            return
        if copies:
            self.run_root([["uninstall", kind, name] for kind, name, _p, _l in copies],
                          "Removing the old theme's system copies…", then)
        else:
            then()

    def confirm_overwrite(self, msg):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text="Replace existing theme?")
        d.format_secondary_text(msg + "\n\nReplacing it deletes the existing copy.")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Replace", Gtk.ResponseType.ACCEPT)
        ok = d.run() == Gtk.ResponseType.ACCEPT
        d.destroy()
        return ok

    def apply(self, component, kind=None):
        if component.get("system"):
            self.apply_system(component)
            return
        only = [kind] if kind and kind in component["provides"] else None
        parts = only or component["provides"]
        if "desktop" in parts and desktop.cinnamon_theme_outdated(component["path"]) and not self.ask(
                f"{component['name']} was made for an older Cinnamon",
                f"It'll work, but it was {desktop.OUTDATED_NOTE}: they'll look see-through and unstyled.",
                "Apply anyway"):
            return
        applied = desktop.apply_component(component, only)
        if applied:
            if "wm" in applied:
                self.notify(f"Now using {component['name']} for window borders. They show on apps with a "
                            "classic title bar, like Files; apps with their own title bar follow your Controls theme.",
                            action=("Show me", self.show_border_sample))
            else:
                self.notify(f"Now using {component['name']} ({', '.join(applied)}).")
        else:
            self.notify("Your desktop doesn't support applying this automatically.", Gtk.MessageType.WARNING)
        self.refresh_item()

    def choose_wallpaper(self, comps):
        d = Gtk.FileChooserDialog(title="Choose a wallpaper", transient_for=self,
                                  action=Gtk.FileChooserAction.OPEN)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Set wallpaper", Gtk.ResponseType.ACCEPT)
        d.set_current_folder(str(Path(comps[0]["path"]).parent))
        if d.run() == Gtk.ResponseType.ACCEPT:
            path = d.get_filename()
            self.apply({"provides": ["wallpapers"], "name": Path(path).name, "path": path})
        d.destroy()

    def remove(self, key):
        entry = installer.load_manifest().get(key)
        if not entry:
            return
        if any(in_use(c) for c in entry["components"]):
            d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                  buttons=Gtk.ButtonsType.NONE, text=f"Remove {entry['title']}?")
            d.format_secondary_text("It's currently in use. Your desktop will fall back to its default look "
                                    "for that part until you pick something else.")
            d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Remove", Gtk.ResponseType.ACCEPT)
            ok = d.run() == Gtk.ResponseType.ACCEPT
            d.destroy()
            if not ok:
                return
        cmds = [["uninstall", kind, name] for kind, name, _path, _label in system_copies(entry)]

        def finish():
            installer.remove(key)
            self.notify(f"Removed {entry['title']}.")
            self.refresh_item()
        if cmds:
            self.run_root(cmds, f"Removing {entry['title']}…", finish)
        else:
            finish()

    # ------------------------------------------------------------ system (root) actions
    def run_root(self, commands, title, on_success=None):
        """Run helper commands with one password prompt, showing progress."""
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.OTHER,
                              buttons=Gtk.ButtonsType.NONE, text=title)
        d.format_secondary_text("You'll be asked for your password.")
        spinner = Gtk.Spinner(active=True, margin=8)
        d.get_message_area().pack_start(spinner, False, False, 0)
        d.show_all()

        def done(result):
            d.destroy()
            if result.ok:
                if on_success:
                    on_success()
            elif not result.cancelled:
                lines = [l for l in result.output.splitlines() if l.startswith("Error:")] or \
                    result.output.splitlines()[-6:]
                error_dialog(self, title.rstrip("…") + " failed", "\n".join(lines))
            self.refresh_item()

        run_async(lambda: system.run_helper(*commands), done)

    def ask(self, title, text, yes, no="Cancel", destructive=False):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text=title)
        d.format_secondary_markup(text)
        d.add_button(no, Gtk.ResponseType.CANCEL)
        b = d.add_button(yes, Gtk.ResponseType.ACCEPT)
        b.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
        ok = d.run() == Gtk.ResponseType.ACCEPT
        d.destroy()
        return ok

    def apply_system(self, component):
        kind, name = component["system"], component["name"]
        req = system.requirement(kind)
        esc = GLib.markup_escape_text
        cmds = []
        installed, active = req.installed, req.active
        if not installed:
            if req.package:
                if not self.ask(f"Install {req.label}?",
                                f"<b>{esc(name)}</b> is for {esc(req.label)}, which isn't installed. drape can "
                                f"install it now (package <tt>{esc(req.package)}</tt>).", "Install and apply"):
                    return
                cmds.append(["apt-install", req.package])
                installed = True
            else:
                d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                      buttons=Gtk.ButtonsType.CLOSE, text=f"{name} needs {req.label}")
                d.format_secondary_markup(
                    f"That isn't available from your distribution's software sources. You can get it from "
                    f"<a href=\"{esc(req.url)}\">{esc(req.url)}</a>. The theme is downloaded; apply it again "
                    "once that's installed.")
                d.run()
                d.destroy()
                return
        cmds.append(["install", kind, component["path"], "--name", name])
        cmds.append({"plymouth": ["set-plymouth", name], "sddm": ["sddm-theme", name],
                     "webgreeter": ["web-greeter-theme", name]}[kind])
        switched = False
        if not active and req.activate:
            switched = self.ask("Switch your login screen?", esc(req.note) + "\n\nIf you don't switch, the "
                                "theme is still set up and will be used if you switch later.",
                                "Switch", "Keep current login screen")
            if switched:
                cmds.append(req.activate)

        def success():
            if kind == "plymouth":
                self.notify(f"{name} is now your boot splash. You'll see it next time you start up.")
            elif switched or active:
                self.notify(f"{name} is now your login screen" + (" (after a restart)." if switched else "."))
            else:
                self.notify(f"{name} is set up. It'll show once you switch to {req.label}.")
        title = f"Setting up {name}…" + (" (rebuilding the boot image takes a minute)" if kind == "plymouth" else "")
        self.run_root(cmds, title, success)

    def use_for_login(self, kind, component):
        """Put an installed wallpaper / Controls theme / icons / cursor on the login screen."""
        dm, greeter = system.display_manager(), system.lightdm_greeter()
        if dm != "lightdm" or greeter not in system.GTK_GREETERS:
            error_dialog(self, "Your login screen can't use this",
                         f"Your login screen is {greeter if dm == 'lightdm' else dm}, which has its own themes. "
                         "Browse them under Login screen, or switch login screen on the Lock & login page.")
            return
        cmds = login_commands(greeter, kind, component)
        self.run_root(cmds, "Updating the login screen…",
                      lambda: self.notify(f"The login screen now uses {component['name']}."))

    def show_border_sample(self):
        """A small ordinary window: the window manager draws its title bar with the window border theme."""
        if self._sample is not None:
            self._sample.present()
            return
        w = Gtk.Window(title="Window border preview", transient_for=None)
        w.set_default_size(420, 170)
        w.set_icon_name(APP_ID)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        box.pack_start(Gtk.Label(label="This window's title bar and edges come from your Window borders theme. "
                                       "Apps with a classic title bar, like Files, look like this.",
                                 wrap=True, xalign=0), True, True, 0)
        close = Gtk.Button(label="Close", halign=Gtk.Align.END)
        close.connect("clicked", lambda _b: w.destroy())
        box.pack_start(close, False, False, 0)
        w.add(box)

        def gone(*_):
            self._sample = None
        w.connect("destroy", gone)
        w.show_all()
        self._sample = w

    def go_to(self, target):
        """'Change' on an In use card: that category's installed themes if there are any, else browsing."""
        if target == "lock":
            self.stack.set_visible_child_name("lock")
            return
        grid = self.installed.grids.get(target)
        if grid and grid[2].get_children():
            self.stack.set_visible_child(self.installed)
            self.installed.tabs.set_visible_child_name(target)
        elif target in self.pages:
            self.stack.set_visible_child_name(target)

    def show_files(self, key, only=None):
        from .filesview import FilesDialog
        entry = installer.load_manifest().get(key)
        if entry:
            FilesDialog(self, entry, system_copies(entry), only).show_all()

    def pick_variant_for_login(self, kind, comps):
        d = Gtk.Dialog(title="Which variant for the login screen?", transient_for=self, modal=True,
                       use_header_bar=True)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use", Gtk.ResponseType.ACCEPT)
        combo = Gtk.ComboBoxText(margin=12)
        for i, c in enumerate(comps):
            combo.append(str(i), c["name"])
        combo.set_active(0)
        d.get_content_area().add(combo)
        d.show_all()
        ok = d.run() == Gtk.ResponseType.ACCEPT
        choice = comps[int(combo.get_active_id())]
        d.destroy()
        if ok:
            self.use_for_login(kind, choice)

    def use_for_lock(self, component):
        """GNOME has a separate lock screen wallpaper."""
        from gi.repository import Gio as _Gio
        s = _Gio.Settings.new("org.gnome.desktop.screensaver")
        s.set_string("picture-uri", Path(component["path"]).as_uri())
        self.notify(f"The lock screen now shows {Path(component['path']).name}.")

    def install_link(self, url):
        self.notify("Installing from gnome-look.org link…")

        def done(result):
            key, entry = result
            self.stack.set_visible_child(self.installed)
            self.installed.load()
            self.notify(f"Installed {entry['title']}. Pick Apply to use it.")

        def attempt(replace=False):
            def failed(e):
                if isinstance(e, installer.ConflictError):
                    self.resolve_conflict(e, lambda: attempt(replace=True))
                else:
                    error_dialog(self, "Couldn't install from link", e)
            run_async(lambda: installer.install_url(url, replace_items=replace), done, failed)
        attempt()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def _window(self):
        return self.get_active_window() or Window(self)

    def do_activate(self):
        self._window().present()

    def do_open(self, files, _n, _hint):
        win = self._window()
        win.present()
        for f in files:
            uri = f.get_uri()
            if uri.startswith(("ocs:", "ocss:")):
                win.install_link(uri)


def main():
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
