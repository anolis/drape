"""Reusable theme controls, glyphs and window-border explanation."""

from pathlib import Path

from .gtk import GLib, GdkPixbuf, Gtk
from .. import desktop, installer, previews, settings
from .common import PART_NAMES, _safe, in_use, matches, run_async
from .images import _renders


# Window borders only reach apps that let the window manager draw their title bar
WM_NOTE = ("Window borders only show on apps with a classic title bar, like Files (Nemo). Apps that draw "
           "their own title bar, like drape and most GNOME apps, follow your Controls theme instead.")

class WindowBordersHelp(Gtk.Box):
    """Explains which apps window borders reach, with a sample window to see them on. Checking the open
    windows only happens when the user asks, and the answer isn't kept."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12, margin_bottom=0)
        self.win = window
        # note and buttons share one row to save vertical space
        row = Gtk.Box(spacing=12)
        note = Gtk.Label(label=WM_NOTE, xalign=0, wrap=True, valign=Gtk.Align.CENTER)
        note.get_style_context().add_class("dim-label")
        row.pack_start(note, True, True, 0)
        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        sample = Gtk.Button(label="Show a sample window")
        sample.set_tooltip_text("Opens a small window with a classic title bar, so you can see the borders")
        sample.connect("clicked", lambda _b: window.show_border_sample())
        buttons.pack_start(sample, False, False, 0)
        self.check = Gtk.Button(label="Check my open windows")
        self.check.set_tooltip_text("Shows which of the apps you have open right now use these borders")
        self.check.connect("clicked", lambda _b: self.run_check())
        buttons.pack_start(self.check, False, False, 0)
        row.pack_end(buttons, False, False, 0)
        self.pack_start(row, False, False, 0)
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


GLYPH_CSS = b"""
.drape-glyphs { background-color: rgba(0, 0, 0, 0.55); border-radius: 6px; padding: 3px 5px; }
.drape-glyphs image { color: #ffffff; }
.drape-glyphs.misfiled { background-color: rgba(192, 28, 40, 0.85); }
"""


class Glyphs(Gtk.Box):
    """Small symbols on a card saying what its download actually contains."""

    ORDER = ["desktop", "plasma", "lookandfeel", "colors", "gtk", "wm", "aurorae", "icons", "cursors", "wallpapers", "login", "plymouth"]

    def __init__(self):
        super().__init__(spacing=4, halign=Gtk.Align.START, valign=Gtk.Align.START, margin=6, no_show_all=True)
        self.get_style_context().add_class("drape-glyphs")

    def show_parts(self, parts, expected=None, complete=True):
        for c in self.get_children():
            c.destroy()
        parts = [p for p in self.ORDER if p in parts]
        if not parts:
            self.hide()
            return False
        for p in parts:
            glyph = {"plasma": "desktop", "lookandfeel": "desktop", "colors": "gtk", "aurorae": "wm"}.get(p, p)
            img = Gtk.Image.new_from_icon_name(f"drape-part-{glyph}-symbolic", Gtk.IconSize.MENU)
            img.show()
            self.pack_start(img, False, False, 0)
        names = ", ".join(PART_NAMES[p] for p in parts)
        misfiled = bool(expected) and expected not in parts and complete
        ctx = self.get_style_context()
        (ctx.add_class if misfiled else ctx.remove_class)("misfiled")
        if misfiled and parts == ["wallpapers"]:
            names = "only pictures"
        self.set_tooltip_text(f"Filed under {PART_NAMES[expected]}, but contains {names}" if misfiled
                              else f"Contains: {names}")
        self.show()
        return misfiled


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
        preview_kind = desktop.theme_part(self.kind) or self.kind
        pb = previews.preview(c, preview_kind)
        if pb is not None:
            self.cache[path] = pb
            return
        self.cache[path] = None

        def work():
            out = previews.theme_preview_path(Path(path), preview_kind)
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


class ApplyControl(Gtk.Box):
    """[Apply | ▾]: Apply uses the variant in use or last picked; ▾ lists variants with previews."""

    def __init__(self, window, key, kind):
        super().__init__()
        self.win, self.key, self.kind = window, key, kind
        self.get_style_context().add_class("linked")
        apply = Gtk.Button(label="Apply pack" if kind == "packs" else "Apply")
        apply.get_style_context().add_class("suggested-action")
        apply.connect("clicked", self._apply)
        self.pack_start(apply, False, False, 0)
        comps = self._components()
        apply.set_sensitive(bool(comps))
        if not comps:
            apply.set_tooltip_text("No supported components for this desktop and window manager")
        if len(comps) > 1 and kind != "packs":
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
            comps = [c for c in comps if matches(self.kind, c)]
        return [c for c in comps if c.get("system") or
                any(p in desktop.compatible_parts(c) for p in (
                    [desktop.theme_part(self.kind)] if self.kind and self.kind != "packs" else c["provides"]))]

    def _target(self, comps):
        using = [c for c in comps if in_use(c)]
        if using:
            return using[0]
        chosen = installer.load_manifest().get(self.key, {}).get("chosen", {}).get(self.kind)
        return next((c for c in comps if c["name"] == chosen), comps[0])

    def _apply(self, _btn):
        if self.kind == "packs":
            self.win.apply_pack(self.key)
            return
        comps = self._components()
        if self.kind == "wallpapers" and len(comps) > 1:
            self.win.choose_wallpaper(comps)
        elif comps:
            self.win.apply(self._target(comps), self.kind)

    def _pick(self, btn):
        comps = self._components()
        if not comps:
            return
        if self.kind == "wallpapers":
            self.win.choose_wallpaper(comps)
            return
        VariantPicker(self.win, self.key, self.kind, comps, btn).popup()
