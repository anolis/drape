"""'Show installed files': where an installed item's files are, how big they are, and what's inside."""

import os
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import installer, pling  # noqa: E402

PART_LABELS = {k.key: k.label for k in pling.KINDS} | {"xfwm": "Xfce borders"}
MAX_LISTED = 500


def _label(path, entry):
    comp = next((c for c in entry["components"] if c["path"] == str(path)), None)
    if comp is None:
        if Path(path).parent == installer.WALLPAPER_DIR:
            return "Wallpaper folder (listed in Backgrounds settings)", "folder-pictures"
        return "Installed files", "folder"
    provides = comp["provides"]
    if comp.get("system"):
        return "Downloaded copy (what drape installs the system copy from)", "folder-download"
    if provides == ["wallpapers"]:
        return "Wallpaper", "image-x-generic"
    if "icons" in provides:
        return "Icon theme" + (" (includes cursors)" if "cursors" in provides else ""), "folder"
    if provides == ["cursors"]:
        return "Cursor theme", "input-mouse"
    return "Theme: " + ", ".join(PART_LABELS.get(p, p) for p in provides), "preferences-desktop-theme"


def scan(path):
    """(total bytes, file count, sorted relative file names) without following links."""
    p = Path(path)
    if not p.exists() and not p.is_symlink():
        return None
    if p.is_file() or p.is_symlink():
        return p.lstat().st_size, 1, [p.name]
    size, names = 0, []
    for root, dirs, files in os.walk(p):
        dirs.sort()
        for f in sorted(files):
            fp = Path(root) / f
            try:
                size += fp.lstat().st_size
            except OSError:
                continue
            names.append(str(fp.relative_to(p)))
    return size, len(names), names


def human(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def show_in_file_manager(path, parent):
    """Open the folder; for a file, ask the file manager to highlight it."""
    p = Path(path)
    if p.is_file():
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([p.as_uri()], "")), None, Gio.DBusCallFlags.NONE, 3000, None)
            return
        except GLib.Error:
            p = p.parent
    Gtk.show_uri_on_window(parent, p.as_uri(), Gdk.CURRENT_TIME)


class FilesDialog(Gtk.Dialog):
    def __init__(self, window, entry, system_copies, only=None):
        super().__init__(title=f"Files installed by {entry['title']}", transient_for=window, use_header_bar=True)
        self.set_default_size(760, 560)
        self.parent_window = window

        if only:  # a single wallpaper card: that image and its pack folder
            locations = [(Path(only["path"]), *_label(only["path"], entry))]
            folder = Path(only["path"]).parent
            if str(folder) in entry["paths"]:
                locations.append((folder, *_label(folder, entry)))
        else:
            locations = [(Path(p), *_label(p, entry)) for p in entry["paths"]]
            if len(entry["components"]) > 1:  # say which variant each one is
                locations = [(p, f"{p.name} · {label}" if not label.startswith("Wallpaper") else label, icon)
                             for p, label, icon in locations]
            # a wallpaper pack lists every image and the folder; the folder is enough
            folders = {p for p, *_ in locations if p.is_dir()}
            locations = [loc for loc in locations if loc[0].parent not in folders or loc[0].is_dir()]
        locations += [(path, label, "system-run") for _k, _n, path, label in system_copies]

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        for path, label, icon in locations:
            box.pack_start(self._location(path, label, icon), False, False, 0)

        note = Gtk.Label(xalign=0, wrap=True)
        note.set_markup(
            f"drape keeps track of these in <tt>{GLib.markup_escape_text(str(installer.MANIFEST))}</tt>. "
            "Removing the theme deletes all of them" +
            (" - system copies after asking for your password." if system_copies else "."))
        note.get_style_context().add_class("dim-label")
        box.pack_start(note, False, False, 0)

        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.add(box)
        self.get_content_area().pack_start(sw, True, True, 0)

    def _location(self, path, label, icon):
        frame = Gtk.Frame()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        top = Gtk.Box(spacing=12)
        top.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DND), False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>")
        text.pack_start(title, False, False, 0)
        home = str(Path.home())
        shown = "~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path)
        where = Gtk.Label(label=shown, xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        where.set_tooltip_text(str(path))
        text.pack_start(where, False, False, 0)
        stats = Gtk.Label(label="Counting…", xalign=0)
        stats.get_style_context().add_class("dim-label")
        text.pack_start(stats, False, False, 0)
        top.pack_start(text, True, True, 0)

        open_btn = Gtk.Button.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.BUTTON)
        open_btn.set_tooltip_text("Show in file manager")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", lambda _b: show_in_file_manager(path, self.parent_window))
        copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.BUTTON)
        copy_btn.set_tooltip_text("Copy location")
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.connect("clicked", lambda _b: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(path), -1))
        top.pack_end(copy_btn, False, False, 0)
        top.pack_end(open_btn, False, False, 0)
        outer.pack_start(top, False, False, 0)

        expander = Gtk.Expander(label="Files")
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, left_margin=8, top_margin=6,
                            bottom_margin=6)
        lsw = Gtk.ScrolledWindow(min_content_height=160, max_content_height=260, propagate_natural_height=True)
        lsw.add(view)
        expander.add(lsw)
        outer.pack_start(expander, False, False, 0)
        frame.add(outer)

        def done(result):
            if result is None:
                stats.set_markup("<b>Missing</b> - it was moved or deleted outside drape")
                open_btn.set_sensitive(False)
                expander.set_sensitive(False)
                return
            size, count, names = result
            stats.set_text(f"{human(size)} · {count:,} file{'s' if count != 1 else ''}")
            listed = names[:MAX_LISTED]
            more = f"\n… and {count - MAX_LISTED:,} more" if count > MAX_LISTED else ""
            view.get_buffer().set_text("\n".join(listed) + more)
            expander.set_label(f"Files ({count:,})")

        def work():
            try:
                result = scan(path)
            except OSError:
                result = None
            GLib.idle_add(done, result)
        threading.Thread(target=work, daemon=True).start()
        return frame
