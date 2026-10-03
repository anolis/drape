"""Render a sample window in the GTK theme named by $GTK_THEME and save it as a PNG.

Run as `python3 -m drape.render_gtk out.png` - GTK only reads the theme at startup, so each
variant needs its own process.
"""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .previews import H, W  # noqa: E402


# Representative GTK controls


def sample():
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    root.get_style_context().add_class("background")

    hb = Gtk.HeaderBar(title="Documents", subtitle="~/Documents", show_close_button=True)
    hb.pack_start(Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON))
    hb.pack_end(
        Gtk.MenuButton(
            image=Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        )
    )
    root.pack_start(hb, False, False, 0)

    body = Gtk.Box(spacing=0)
    side = Gtk.ListBox()
    side.get_style_context().add_class("sidebar")
    for i, name in enumerate(["Home", "Documents", "Music", "Pictures"]):
        row = Gtk.ListBoxRow()
        row.add(Gtk.Label(label=name, xalign=0, margin=6, margin_start=12, margin_end=24))
        side.add(row)
        if i == 1:
            side.select_row(row)
    body.pack_start(side, False, False, 0)
    body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

    main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
    nb = Gtk.Notebook()
    for t in ("General", "Appearance", "About"):
        nb.append_page(Gtk.Box(), Gtk.Label(label=t))
    nb.set_size_request(-1, 30)
    main.pack_start(nb, False, False, 0)

    row1 = Gtk.Box(spacing=8)
    entry = Gtk.Entry(text="Search files…")
    row1.pack_start(entry, True, True, 0)
    combo = Gtk.ComboBoxText()
    combo.append_text("Recent")
    combo.set_active(0)
    row1.pack_start(combo, False, False, 0)
    main.pack_start(row1, False, False, 0)

    row2 = Gtk.Box(spacing=12)
    check = Gtk.CheckButton(label="Hidden")
    check.set_active(True)
    row2.pack_start(check, False, False, 0)
    row2.pack_start(Gtk.RadioButton(label="Grid"), False, False, 0)
    sw = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
    row2.pack_end(sw, False, False, 0)
    main.pack_start(row2, False, False, 0)

    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(60)
    scale.set_draw_value(False)
    main.pack_start(scale, False, False, 0)
    bar = Gtk.ProgressBar(fraction=0.45)
    main.pack_start(bar, False, False, 0)

    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    buttons.pack_start(Gtk.Button(label="Cancel"), False, False, 0)
    ok = Gtk.Button(label="Save")
    ok.get_style_context().add_class("suggested-action")
    buttons.pack_start(ok, False, False, 0)
    main.pack_end(buttons, False, False, 0)

    body.pack_start(main, True, True, 0)
    root.pack_start(body, True, True, 0)
    return root


# Off-screen rendering entry point


def main():
    out = sys.argv[1]
    win = Gtk.OffscreenWindow()
    win.set_default_size(W, H)
    win.add(sample())
    win.show_all()
    for _ in range(20):
        while Gtk.events_pending():
            Gtk.main_iteration()
    pb = win.get_pixbuf()
    if pb is None:
        sys.exit(1)
    if pb.get_width() != W or pb.get_height() != H:
        pb = pb.scale_simple(W, H, 2)
    pb.savev(out, "png", [], [])
    GLib.idle_add(Gtk.main_quit)


if __name__ == "__main__":
    main()
