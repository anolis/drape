"""Cards and previews for the current desktop look."""

from pathlib import Path

from .card_transitions import FadingCard
from .gtk import GLib, GdkPixbuf, Gtk, Pango
from .. import desktop, helper as root_helper, installer, previews, system
from .common import CARD_H, CARD_W, _safe
from .images import _renders, load_image


ACTIVE_PARTS = [("gtk", "Controls"), ("wm", "Window borders"), ("desktop", "Desktop"), ("icons", "Icons"),
                ("lookandfeel", "Global theme"), ("colors", "Color scheme"),
                ("cursors", "Cursors"), ("wallpapers", "Wallpaper"), ("login", "Login screen"),
                ("boot", "Boot splash")]


def locate_theme(part, name):
    """Folder of the theme in use for a part, looked up the way the desktop does."""
    if not name:
        return None
    if desktop.current_desktop() == "kde" and desktop.theme_part(part) in desktop.kde.DIRECTORIES:
        return desktop.kde.locate(desktop.theme_part(part), name)
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


class ActiveCard(FadingCard):
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
        preview_kind = desktop.theme_part(part) or part
        pb = previews.preview(comp, preview_kind)
        if pb is not None:
            self._show_pixbuf(pb)
            return

        def work():
            out = previews.theme_preview_path(path, preview_kind)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(out), CARD_W, CARD_H, True) if out else None
            GLib.idle_add(self._show_pixbuf, pb)
        _renders.submit(lambda: _safe(work))

    def _describe(self, part):
        """(name, detail line, drape manifest key, page for Change) - and start the preview."""
        if part in ("gtk", "wm", "desktop", "icons", "cursors", "lookandfeel", "colors"):
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
