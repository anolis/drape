"""drape GTK app: browse gnome-look.org by category and install/apply each piece in one click."""

import hashlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

import requests  # noqa: E402

from . import desktop, installer, pling  # noqa: E402

APP_ID = "io.github.anolis.Drape"
THUMB_DIR = Path(GLib.get_user_cache_dir()) / "drape" / "thumbs"
CARD_W, CARD_H = 260, 160
PAGE_SIZE = 30

_images = ThreadPoolExecutor(max_workers=6)


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


def load_image(url, image, width, height):
    """Fetch (with a disk cache) and show a preview in `image`, scaled to fit."""
    def work():
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        path = THUMB_DIR / hashlib.sha1(url.encode()).hexdigest()
        if not path.exists():
            r = requests.get(url, timeout=20, headers={"User-Agent": pling.USER_AGENT})
            r.raise_for_status()
            tmp = path.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
        GLib.idle_add(image.set_from_pixbuf, pb)

    def safe():
        try:
            work()
        except Exception:  # noqa: BLE001 - a missing preview is not worth an error dialog
            GLib.idle_add(image.set_from_icon_name, "image-missing", Gtk.IconSize.DIALOG)
    _images.submit(safe)


def in_use(component):
    """True if this installed component is what the desktop is currently using."""
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


class ApplyButton(Gtk.Button):
    """'Apply' for a single component, or a menu of variants when a download installed several."""

    def __init__(self, window, key, kind=None):
        super().__init__(label="Apply")
        self.win, self.key, self.kind = window, key, kind
        self.get_style_context().add_class("suggested-action")
        self.connect("clicked", self._on_clicked)

    def _components(self):
        entry = installer.load_manifest().get(self.key)
        if not entry:
            return []
        comps = entry["components"]
        if self.kind and self.kind != "wallpapers":
            comps = [c for c in comps if self.kind in c["provides"]] or comps
        return comps

    def _on_clicked(self, _btn):
        comps = self._components()
        if self.kind == "wallpapers" and len(comps) > 1:
            self.win.choose_wallpaper(comps)
        elif len(comps) == 1:
            self.win.apply(comps[0], self.kind)
        elif comps:
            self.menu = Gtk.Menu()  # keep a reference so it isn't collected while open
            for c in comps:
                mi = Gtk.MenuItem(label=c["name"] + ("  (in use)" if in_use(c) else ""))
                mi.connect("activate", lambda _m, c=c: self.win.apply(c, self.kind))
                self.menu.append(mi)
            self.menu.show_all()
            self.menu.popup_at_widget(self, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)


class Card(Gtk.FlowBoxChild):
    def __init__(self, window, kind, item):
        super().__init__()
        self.win, self.kind, self.item = window, kind, item

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("view")
        box.pack_start(frame, False, False, 0)

        self.image = Gtk.Image.new_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
        self.image.set_size_request(CARD_W, CARD_H)
        ev = Gtk.EventBox()
        ev.add(self.image)
        ev.connect("button-release-event", lambda *_: window.show_details(kind, item))
        frame.add(ev)
        if item.previews:
            load_image(item.previews[0], self.image, CARD_W, CARD_H)

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
            self.actions.pack_start(ApplyButton(self.win, self.item.id, self.kind), False, False, 0)
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
    def __init__(self, window, kind):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.kind = window, kind
        self.page = 0
        self.generation = 0
        self.total = 0

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                valign=Gtk.Align.START, max_children_per_line=8,
                                margin=12, row_spacing=6, column_spacing=6)
        self.more = Gtk.Button(label="Load more", halign=Gtk.Align.CENTER, margin=12, no_show_all=True)
        self.more.connect("clicked", lambda _b: self.load(append=True))
        self.status = Gtk.Label(margin=24, no_show_all=True, wrap=True)
        self.status.get_style_context().add_class("dim-label")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.status, False, False, 0)
        inner.pack_start(self.flow, False, False, 0)
        inner.pack_start(self.more, False, False, 0)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.add(inner)
        self.pack_start(sw, True, True, 0)
        self.loaded = False

    def cards(self):
        return self.flow.get_children()

    def _set_status(self, text):
        self.status.set_text(text or "")
        self.status.set_visible(bool(text))

    def load(self, append=False):
        self.loaded = True
        self.generation += 1
        gen = self.generation
        if not append:
            self.page = 0
            for c in self.cards():
                c.destroy()
        self._set_status("Loading…" if not append else "")
        self.more.hide()
        query, sort, page = self.win.query(), self.win.sort(), self.page

        def done(result):
            if gen != self.generation:
                return
            items, total = result
            self.total = total
            self._set_status("" if items or append else "Nothing found.")
            for it in items:
                self.flow.add(Card(self.win, self.kind, it))
            self.flow.show_all()
            self.page += 1
            self.more.set_visible(len(self.cards()) < total)

        def error(e):
            if gen == self.generation:
                self._set_status(str(e))

        run_async(lambda: pling.search(self.kind, query, sort, page, PAGE_SIZE), done, error)

    def refresh_cards(self, item_id=None):
        for c in self.cards():
            if item_id in (None, c.item.id):
                c.refresh()


class InstalledPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = window
        bar = Gtk.Box(spacing=6, margin=12)
        self.updates_btn = Gtk.Button(label="Check for updates")
        self.updates_btn.connect("clicked", self.check_updates)
        bar.pack_end(self.updates_btn, False, False, 0)
        self.note = Gtk.Label(xalign=0)
        self.note.get_style_context().add_class("dim-label")
        bar.pack_start(self.note, True, True, 0)
        self.pack_start(bar, False, False, 0)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.set_header_func(lambda row, before: row.set_header(Gtk.Separator() if before else None))
        frame = Gtk.Frame(margin=12, margin_top=0, valign=Gtk.Align.START)
        frame.add(self.list)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.add(frame)
        self.pack_start(sw, True, True, 0)
        self.updates = {}

    def load(self):
        for r in self.list.get_children():
            r.destroy()
        m = installer.load_manifest()
        self.note.set_text(f"{len(m)} item(s) installed with drape" if m else
                           "Nothing installed yet — pick a category on the left.")
        for key, e in sorted(m.items(), key=lambda kv: kv[1]["title"].lower()):
            self.list.add(self._row(key, e))
        self.list.show_all()

    def _row(self, key, e):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=10)
        top = Gtk.Box(spacing=8)
        parts = sorted({p for c in e["components"] for p in c["provides"]})
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.set_markup(f"<b>{GLib.markup_escape_text(e['title'])}</b>  "
                         f"<small>{GLib.markup_escape_text(', '.join(parts))}</small>")
        top.pack_start(title, True, True, 0)
        if key in self.updates:
            ub = Gtk.Button(label="Update")
            ub.get_style_context().add_class("suggested-action")
            ub.connect("clicked", lambda _b: self.win.install(self.updates.pop(key)))
            top.pack_end(ub, False, False, 0)
        rm = Gtk.Button(label="Remove")
        rm.connect("clicked", lambda _b: self.win.remove(key))
        top.pack_end(rm, False, False, 0)
        box.pack_start(top, False, False, 0)

        wall = [c for c in e["components"] if c["provides"] == ["wallpapers"]]
        others = [c for c in e["components"] if c["provides"] != ["wallpapers"]]
        for c in others:
            line = Gtk.Box(spacing=8, margin_start=12)
            lab = Gtk.Label(label=c["name"] + ("   ✓ in use" if in_use(c) else ""), xalign=0)
            line.pack_start(lab, True, True, 0)
            ab = Gtk.Button(label="Apply")
            ab.connect("clicked", lambda _b, c=c: self.win.apply(c))
            line.pack_end(ab, False, False, 0)
            box.pack_start(line, False, False, 0)
        if wall:
            line = Gtk.Box(spacing=8, margin_start=12)
            line.pack_start(Gtk.Label(label=f"{len(wall)} wallpaper(s) — also listed in Backgrounds settings",
                                      xalign=0), True, True, 0)
            ab = Gtk.Button(label="Set wallpaper" if len(wall) == 1 else "Choose…")
            ab.connect("clicked", lambda _b: self.win.apply(wall[0]) if len(wall) == 1
                       else self.win.choose_wallpaper(wall))
            line.pack_end(ab, False, False, 0)
            box.pack_start(line, False, False, 0)
        row.add(box)
        return row

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
            self.note.set_text(f"{len(found)} update(s) available" if found else "Everything is up to date.")

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
            self.files.set_active(0)
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
        self.set_default_size(1180, 760)
        self.set_icon_name("preferences-desktop-theme")
        self.busy = {}  # item id -> {"bars": [...]}

        hb = Gtk.HeaderBar(show_close_button=True, title="drape",
                           subtitle="Themes, icons, cursors & wallpapers from gnome-look.org")
        self.set_titlebar(hb)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=28)
        self.search.connect("search-changed", lambda _e: self.reload_current())
        hb.pack_end(self.search)
        self.sort_combo = Gtk.ComboBoxText()
        # Pling's "top" favours brand-new items with a handful of votes, so popularity is the default
        for key, label in [("downloads", "Most downloaded"), ("top", "Top rated"), ("new", "Newest"),
                           ("alpha", "A–Z")]:
            self.sort_combo.append(key, label)
        self.sort_combo.set_active_id("downloads")
        self.sort_combo.connect("changed", lambda _c: self.reload_all())
        hb.pack_end(self.sort_combo)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages = {}
        for k in pling.KINDS:
            if k.key != "wallpapers" and not desktop.supported(k.key):
                continue  # e.g. no "Desktop" (Cinnamon theme) page on GNOME
            p = BrowsePage(self, k.key)
            self.pages[k.key] = p
            self.stack.add_titled(p, k.key, k.label)
        self.installed = InstalledPage(self)
        self.stack.add_titled(self.installed, "installed", "Installed")
        self.stack.connect("notify::visible-child", lambda *_: self.on_page())

        side = Gtk.StackSidebar(stack=self.stack)
        side.set_size_request(170, -1)
        paned = Gtk.Box()
        paned.pack_start(side, False, False, 0)
        paned.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        paned.pack_start(self.stack, True, True, 0)

        self.infobar = Gtk.InfoBar(show_close_button=True, no_show_all=True)
        self.infobar.connect("response", lambda b, _r: b.hide())
        self.info_label = Gtk.Label(wrap=True, xalign=0)
        self.infobar.get_content_area().add(self.info_label)
        self.info_label.show()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(self.infobar, False, False, 0)
        root.pack_start(paned, True, True, 0)
        self.add(root)
        self.show_all()
        self.on_page()

    # ------------------------------------------------------------ helpers
    def query(self):
        return self.search.get_text().strip()

    def sort(self):
        return self.sort_combo.get_active_id()

    def notify(self, text, kind=Gtk.MessageType.INFO):
        self.info_label.set_text(text)
        self.infobar.set_message_type(kind)
        self.infobar.show()

    def on_page(self):
        child = self.stack.get_visible_child()
        searchable = child is not self.installed
        self.search.set_sensitive(searchable)
        self.sort_combo.set_sensitive(searchable)
        if child is self.installed:
            self.installed.load()
        elif not child.loaded:
            child.load()

    def reload_current(self):
        child = self.stack.get_visible_child()
        if child is not self.installed:
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

        def work():
            fresh = pling.get(item.id)  # download links are signed and expire
            try:
                return installer.install_item(fresh, file_index, progress)
            except installer.InstallError as e:
                if "wasn't installed by drape" not in str(e):
                    raise
                return e  # ask the user on the main thread

        def done(result):
            self.busy.pop(item.id, None)
            if isinstance(result, installer.InstallError):
                if self.confirm_overwrite(str(result)):
                    self.busy[item.id] = {"bars": []}
                    run_async(lambda: installer.install_item(pling.get(item.id), file_index, progress, True),
                              done, error)
                self.refresh_item(item.id)
                return
            self.refresh_item(item.id)
            if apply_kind:
                comps = [c for c in result["components"] if apply_kind in c["provides"]] or result["components"]
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

    def confirm_overwrite(self, msg):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text="Replace existing theme?")
        d.format_secondary_text(msg + "\n\nReplacing it deletes the existing copy.")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Replace", Gtk.ResponseType.ACCEPT)
        ok = d.run() == Gtk.ResponseType.ACCEPT
        d.destroy()
        return ok

    def apply(self, component, kind=None):
        only = [kind] if kind and kind in component["provides"] else None
        applied = desktop.apply_component(component, only)
        if applied:
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
        installer.remove(key)
        self.notify(f"Removed {entry['title']}.")
        self.refresh_item()

    def install_link(self, url):
        self.notify("Installing from gnome-look.org link…")

        def done(result):
            key, entry = result
            self.stack.set_visible_child(self.installed)
            self.installed.load()
            self.notify(f"Installed {entry['title']}. Pick Apply to use it.")

        run_async(lambda: installer.install_url(url), done,
                  lambda e: error_dialog(self, "Couldn't install from link", e))


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
