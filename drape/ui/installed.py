"""Installed packs, selection, updates and the current desktop look."""

from pathlib import Path

from .gtk import GLib, Gtk, Pango
from .. import desktop, installer, pling, settings
from .active import ACTIVE_PARTS, ActiveCard
from .common import CARD_H, CARD_W, in_use, matches, run_async
from .images import load_image
from .widgets import ApplyControl, Glyphs, WindowBordersHelp


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
        self.selection_id = (key, wallpaper["path"] if wallpaper else None)
        self.select_check = Gtk.CheckButton(label="Select wallpaper" if wallpaper else "Select pack")
        self.select_check.set_active(self.selection_id in window.installed.selected)
        self.select_check.connect("toggled", lambda b: window.installed.select_card(self.selection_id, b.get_active()))
        box.pack_start(self.select_check, False, False, 0)
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
        overlay = Gtk.Overlay()
        overlay.add(ev)
        glyphs = Glyphs()
        overlay.add_overlay(glyphs)
        frame.add(overlay)
        entry_image(key, entry, image, CARD_W, CARD_H, wallpaper)
        glyphs.show_parts({"login" if p in ("sddm", "webgreeter") else p
                           for c in (entry["components"] if not wallpaper else [wallpaper])
                           for p in c["provides"] if p != "xfwm"})
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


class InstalledPage(Gtk.Box):
    """Installed items in the same tabs and grid as browsing."""

    def __init__(self, window, kinds):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = window
        self.updates = {}
        self.selected = set()
        self.tabs = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hhomogeneous=False)
        self.grids = {}
        for k in [pling.Kind("active", "In use", "")] + kinds + [pling.Kind("other", "Other", "")]:
            k = pling.Kind(k.key, desktop.category_label(k.key, k.label), k.categories)
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

        selection_bar = Gtk.Box(spacing=8, margin=12, margin_bottom=0)
        self.select_all_btn = Gtk.Button(label="Select all in category")
        self.select_all_btn.connect("clicked", self.select_all)
        selection_bar.pack_start(self.select_all_btn, False, False, 0)
        self.clear_btn = Gtk.Button(label="Clear selection")
        self.clear_btn.connect("clicked", lambda *_: self.clear_selection())
        selection_bar.pack_start(self.clear_btn, False, False, 0)
        self.delete_btn = Gtk.Button(label="Delete selected (0)")
        self.delete_btn.get_style_context().add_class("destructive-action")
        self.delete_btn.connect("clicked", lambda *_: self.win.remove_selected(set(self.selected)))
        selection_bar.pack_end(self.delete_btn, False, False, 0)
        self.pack_start(selection_bar, False, False, 0)
        self._selection_changed()

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
        self._sync_category_visibility()

    def _sync_category_visibility(self):
        for kind, (_label, panel, *_rest) in self.grids.items():
            if kind in ("active", "other"):
                continue
            visible = desktop.category_visible(kind)
            for widget in (panel, self.chip[kind].get_parent()):
                widget.set_no_show_all(not visible)
                widget.set_visible(visible)

    def select_card(self, identity, active):
        if active:
            self.selected.add(identity)
        else:
            self.selected.discard(identity)
        # A pack may appear in several categories; keep those checkboxes in sync.
        for _kind, _sw, flow, _empty in self.grids.values():
            for card in flow.get_children():
                if isinstance(card, InstalledCard) and card.selection_id == identity:
                    if card.select_check.get_active() != active:
                        card.select_check.set_active(active)
        self._selection_changed()

    def _selection_changed(self):
        count = len(self.selected)
        self.delete_btn.set_label(f"Delete selected ({count})")
        self.delete_btn.set_sensitive(bool(count))
        self.clear_btn.set_sensitive(bool(count))
        self.select_all_btn.set_sensitive(self.tabs.get_visible_child_name() != "active")

    def select_all(self, *_):
        grid = self.grids.get(self.tabs.get_visible_child_name())
        if grid:
            for card in grid[2].get_children():
                if isinstance(card, InstalledCard):
                    card.select_check.set_active(True)

    def clear_selection(self):
        self.selected.clear()
        for _kind, _sw, flow, _empty in self.grids.values():
            for card in flow.get_children():
                if isinstance(card, InstalledCard):
                    card.select_check.set_active(False)
        self._selection_changed()

    def _sync_chip(self):
        name = self.tabs.get_visible_child_name()
        self._selection_changed()
        if name == "wm":
            self.hint.show_all()
        else:
            self.hint.hide()
        rb = self.chip.get(name)
        if rb and not rb.get_active():
            rb.set_active(True)

    def load(self):
        self._sync_category_visibility()
        m = installer.load_manifest()
        self.selected = {(key, path) for key, path in self.selected if key in m and
                         (path is None or any(c["path"] == path for c in m[key]["components"]))}
        self._selection_changed()
        placed = set()
        counts = {}
        for key_name, (k, sw, flow, empty) in self.grids.items():
            for c in flow.get_children():
                c.destroy()
            if key_name not in ("active", "other") and not desktop.category_visible(key_name):
                counts[key_name] = 0
                continue
            if key_name == "active":
                for part, label in ACTIVE_PARTS:
                    if part in ("login", "boot", "wallpapers") or desktop.supported(part):
                        flow.add(ActiveCard(self.win, part, desktop.category_label(part, label)))
                flow.show_all()
                counts["active"] = len(flow.get_children())
                continue
            n = 0
            for key, e in sorted(m.items(), key=lambda kv: kv[1]["title"].lower()):
                if settings.get("only_applicable") and not any(
                        c.get("system") or desktop.compatible_parts(c) for c in e["components"]):
                    continue
                if key_name == "other":
                    if key in placed:
                        continue
                elif not any(matches(key_name, c) for c in e["components"]):
                    continue
                elif settings.get("only_applicable") and key_name not in ("login", "boot"):
                    target = desktop.theme_part(key_name)
                    if not any(target in desktop.compatible_parts(c) for c in e["components"]):
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
