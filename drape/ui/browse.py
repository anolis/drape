"""Catalog cards, filtering and paginated browsing."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from .gtk import GLib, Gtk, Pango
from .. import desktop, installer, peek, pling, settings, system
from .common import CARD_H, CARD_W, PART_NAMES, TAB_PART, _safe, run_async
from .images import _ui_busy_until, load_image
from .widgets import ApplyControl, Glyphs, WindowBordersHelp


_peeks = ThreadPoolExecutor(max_workers=1)  # small and gentle: one listing at a time, after pictures


CHUNK = 10          # results per request: small batches paint sooner on slow connections

FIRST_CHUNKS = 3    # batches requested up front when a tab opens


class Card(Gtk.FlowBoxChild):
    def __init__(self, window, kind, item):
        super().__init__()
        self.win, self.kind, self.item = window, kind, item
        self.compatible = True

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
        self.glyphs = Glyphs()
        overlay.add_overlay(self.glyphs)
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
                           on_done=lambda _ok: (spinner.destroy(), self._peek(alive)), alive=alive.is_set)
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
        self.misfiled = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=34, no_show_all=True)
        box.pack_start(self.misfiled, False, False, 0)
        if not item.previews:
            self._peek(None)

        self.actions = Gtk.Box(spacing=6)
        box.pack_start(self.actions, False, False, 0)
        self.add(box)
        self.refresh()

    def _peek(self, alive):
        """Find out what the download contains (cached, or a small partial read) and show glyphs."""
        f = self.item.best_file()
        if not f:
            return
        hit = peek.cached(self.item.id, f.name)
        if hit is not None:
            self._show_glyphs(*hit)
            if desktop.archive_compatible(*hit, self.kind) or all(
                    peek.cached(self.item.id, other.name) is not None for other in self.item.files):
                return

        def work():
            if alive is not None and not alive.is_set():
                return
            result = peek.contents(self.item.id, f.url, f.name)
            if not desktop.archive_compatible(*result, self.kind):
                for other in self.item.files:
                    if other != f:
                        peek.contents(self.item.id, other.url, other.name)
            GLib.idle_add(self._show_glyphs, *result)
        _peeks.submit(lambda: _safe(work))

    def _show_glyphs(self, parts, complete):
        checks = [peek.cached(self.item.id, f.name) for f in self.item.files]
        self.compatible = not checks or any(
            hit is None or desktop.archive_compatible(*hit, self.kind) for hit in checks)
        parent = self.get_parent()
        if isinstance(parent, Gtk.FlowBox):
            parent.invalidate_filter()
            page = self.win.pages.get(self.kind)
            if page:
                GLib.idle_add(page._filtered_status)
        parts = {"wm" if p in ("xfwm", "aurorae") else p for p in parts}
        expected = "plasma" if self.kind == "desktop" and desktop.current_desktop() == "kde" else TAB_PART.get(self.kind)
        if self.glyphs.show_parts(parts, expected, complete):
            not_ = GLib.markup_escape_text(PART_NAMES[expected])
            if set(parts) == {"wallpapers"}:
                text = f"⚠ Only pictures inside, not {not_}"
            else:
                names = " and ".join(PART_NAMES[p] for p in Glyphs.ORDER if p in parts)
                text = f"⚠ Contains {GLib.markup_escape_text(names)}, not {not_}"
            self.misfiled.set_markup(f"<small>{text}</small>")
            self.misfiled.show()
        return False

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
        self.flow.set_filter_func(lambda card: not settings.get("only_applicable") or card.compatible)
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

    def _filtered_status(self):
        if self.flow.in_destruction():
            return False
        message = "No compatible themes in these results."
        if settings.get("only_applicable") and self.cards() and not any(c.compatible for c in self.cards()):
            self._set_status(message)
            self._maybe_more()
        elif self.status_label.get_text() == message:
            self._set_status("")
        return False

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
            self._set_status(f"Asking {desktop.catalog_name()}…", busy=True)

        def slow():
            if gen == self.generation and not self.cards() and self.status.get_visible():
                self._set_status(f"Still waiting for {desktop.catalog_name()} - the connection seems slow. "
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
                self._set_status(f"Couldn't reach {desktop.catalog_name()}: {e}")

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
        if self.kind not in ("login", "boot"):
            cats, note = desktop.scope(self.kind, only)
            if cats is not None or not desktop.supported(self.kind):
                return cats, GLib.markup_escape_text(note)
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
