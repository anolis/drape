"""Catalog item details and download variant selection."""



from .gtk import GLib, Gtk
from .. import installer
from .images import load_image


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
                        f"<a href=\"{GLib.markup_escape_text(item.page)}\">view on theme catalog</a>")
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
