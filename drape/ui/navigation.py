"""Engine-based navigation and explanations of each theme's reach."""

from .. import desktop
from .gtk import GLib, Gtk

# Keep stable page IDs: saved scroll positions, install links and filtering use
# these IDs even when labels and ordering change.
GROUPS = (
    ("COLLECTIONS", ("packs", "configurations", "installed")),
    ("GTK APPLICATIONS", ("gtk",)),
    ("GNOME / LIBADWAITA", ("libadwaita", "libadwaitasettings")),
    ("QT / KVANTUM APPLICATIONS", ("kvantum", "qtsettings")),
    ("DESKTOP SHELL", ("desktop", "lookandfeel", "colors", "xfcepanel")),
    ("WINDOW DECORATIONS", ("wm", "windowmanager")),
    ("SHARED ASSETS", ("icons", "cursors", "wallpapers")),
    ("STARTUP & LOGIN", ("login", "boot", "lock")),
)

DESCRIPTIONS = {
    "libadwaita": "Native GNOME apps such as Files and Settings. Applies the theme's GTK 4 stylesheet through user CSS, with a backup and Restore action in Native GNOME setup. GTK 4 files are required, but may not be designed for your libadwaita version. Restart the apps after applying.",
    "gtk": "GTK application widgets such as buttons, menus and app-drawn title bars. Themes must support the app's GTK version. For native GNOME Files and Settings, use GNOME / libadwaita with a GTK 4 stylesheet. This does not style Qt apps or custom-rendered apps such as Kitty.",
    "kvantum": "Qt widget applications using the Kvantum engine, such as OpenSnitch with its System appearance selected. This does not style GTK/libadwaita apps, Kitty, or window-manager decorations. Apps with their own styles and sandboxed runtimes may need separate setup.",
    "desktop": "Desktop panels, menus and shell widgets. Application widgets and window decorations use separate engines.",
    "wm": "Window frames and title bars drawn by the supported window manager. Apps that draw their own decorations use their own toolkit or settings instead.",
    "lookandfeel": "KDE Plasma global themes bundle several appearance settings. Review the component choices before applying.",
    "colors": "KDE application and desktop color palettes. A color scheme does not replace widget or window-decoration styles.",
    "icons": "Application, folder and action icons. Apps can bundle their own icons rather than using the system icon theme.",
    "cursors": "Mouse pointer images. Apps can retain cached pointers until restarted or use their own cursors.",
    "wallpapers": "Desktop background images. Application and desktop-shell themes are separate.",
    "login": "The sign-in screen drawn by your login manager. This is separate from themes used after login.",
    "boot": "The startup screen drawn by Plymouth. This is separate from your desktop and application themes.",
    "packs": "Bundles of appearance components that may use several engines. Choose which components to apply; a pack does not automatically style every application.",
    "configurations": "Save your current theme selections as a named personal configuration. Review and reapply compatible components using the theme files on this machine.",
}


def page_label(name, default):
    if name == "desktop":
        return {"cinnamon": "Cinnamon styles", "kde": "Plasma styles"}.get(
            desktop.current_desktop(), "Desktop styles"
        )
    return {
        "gtk": "GTK themes",
        "libadwaita": "GTK 4 themes",
        "libadwaitasettings": "Native GNOME setup",
        "kvantum": "Kvantum themes",
        "qtsettings": "Kvantum setup",
    }.get(name, default)


def build_sidebar(window):
    """Headers follow visible rows, so filtering never leaves an empty group."""
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
    window._sidebar_list = lb
    lb.set_filter_func(lambda row: window.page_visible(row.page))
    lb.get_style_context().add_class("sidebar")
    children = {
        window.stack.child_get_property(child, "name"): child
        for child in window.stack.get_children()
    }
    rows = {}
    for group, pages in GROUPS:
        for name in pages:
            if name not in children:
                continue
            row = Gtk.ListBoxRow()
            row.page, row.group = name, group
            default = window.stack.child_get_property(children[name], "title")
            label = Gtk.Label(
                label=page_label(name, default),
                xalign=0,
                margin=6,
                margin_start=10,
                margin_end=10,
            )
            row.add(label)
            if name in DESCRIPTIONS:
                row.set_tooltip_text(DESCRIPTIONS[name])
            lb.add(row)
            rows[name] = row

    def header(row, before):
        if before is not None and before.group == row.group:
            row.set_header(None)
            return
        label = Gtk.Label(xalign=0, margin_start=10, margin_top=12, margin_bottom=4)
        label.set_markup(f"<small><b>{GLib.markup_escape_text(row.group)}</b></small>")
        label.get_style_context().add_class("dim-label")
        row.set_header(label)
        label.show()

    lb.set_header_func(header)
    syncing = {"on": False}

    def selected(_lb, row):
        if row and not syncing["on"]:
            window.stack.set_visible_child_name(row.page)

    lb.connect("row-selected", selected)

    def follow(*_):
        row = rows.get(window.stack.get_visible_child_name())
        if row and lb.get_selected_row() is not row:
            syncing["on"] = True
            lb.select_row(row)
            syncing["on"] = False

    window.stack.connect("notify::visible-child", follow)
    follow()
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroller.set_size_request(220, -1)
    scroller.add(lb)
    return scroller
