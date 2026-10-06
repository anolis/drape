"""Animation-only settings, built from installed XScreenSaver XML controls."""

from .. import wallpaper_animation_settings as settings
from ..video_wallpapers import VideoError
from .common import error_dialog
from .gtk import Gtk


def edit(window, entry):
    controls = entry.get("settings", [])
    values = settings.saved(entry["name"], controls)
    dialog = Gtk.Dialog(title=entry["label"] + " settings", transient_for=window, modal=True)
    dialog.set_default_size(520, 460)
    dialog.add_buttons(
        "Restore defaults",
        Gtk.ResponseType.APPLY,
        "Cancel",
        Gtk.ResponseType.CANCEL,
        "Apply",
        Gtk.ResponseType.ACCEPT,
    )
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
    note = Gtk.Label(
        label="These settings affect this animation in Drape. Apply updates a running wallpaper; "
        "otherwise your choices are saved for the next playback.",
        xalign=0,
        wrap=True,
    )
    body.pack_start(note, False, False, 0)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    grid = Gtk.Grid(row_spacing=12, column_spacing=16, margin=8)
    scroll.add(grid)
    body.pack_start(scroll, True, True, 0)
    dialog.get_content_area().pack_start(body, True, True, 0)
    widgets = {}
    for row, c in enumerate(controls):
        key = c["id"]
        label = Gtk.Label(label=c["label"], xalign=0, wrap=True)
        label.set_max_width_chars(26)
        grid.attach(label, 0, row, 1, 1)
        if c["type"] == "number":
            widget = Gtk.SpinButton.new_with_range(
                min(c["low"], c["default"]), max(c["high"], c["default"]), c["step"]
            )
            widget.set_digits(0 if c["integer"] else 6)
        elif c["type"] == "boolean":
            widget = Gtk.CheckButton()
        elif c["type"] == "select":
            widget = Gtk.ComboBoxText()
            for option in c["options"]:
                widget.append(option["id"], option["label"])
        else:
            widget = Gtk.Entry(max_length=2048)
            if c["type"] == "file":
                widget.set_placeholder_text("Path to a file")
        widget.set_hexpand(True)
        if c["type"] == "file":
            group = Gtk.Box(spacing=8)
            group.pack_start(widget, True, True, 0)
            browse = Gtk.Button(label="Browse…")
            browse.connect("clicked", lambda _button, field=widget: _choose_file(dialog, field))
            group.pack_start(browse, False, False, 0)
            grid.attach(group, 1, row, 1, 1)
        else:
            grid.attach(widget, 1, row, 1, 1)
        widgets[key] = widget

    def populate(current):
        for c in controls:
            value = current.get(c["id"], c["default"])
            widget = widgets[c["id"]]
            if c["type"] == "number":
                widget.set_value(value)
            elif c["type"] == "boolean":
                widget.set_active(value)
            elif c["type"] == "select":
                widget.set_active_id(value)
            else:
                widget.set_text(value)

    populate(values)
    dialog.show_all()
    accepted = False
    try:
        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.APPLY:
                populate({})
                continue
            if response != Gtk.ResponseType.ACCEPT:
                break
            values = {}
            for c in controls:
                widget = widgets[c["id"]]
                if c["type"] == "number":
                    widget.update()
                    value = widget.get_value_as_int() if c["integer"] else widget.get_value()
                elif c["type"] == "boolean":
                    value = widget.get_active()
                elif c["type"] == "select":
                    value = widget.get_active_id()
                else:
                    value = widget.get_text()
                # Omitting defaults allows the animation to retain its native behavior.
                if value != c["default"]:
                    values[c["id"]] = value
            try:
                settings.remember(entry["name"], controls, values)
                accepted = True
                break
            except VideoError as exc:
                error_dialog(dialog, "Could not save animation settings", exc)
    finally:
        dialog.destroy()
    return accepted


def _choose_file(dialog, field):
    chooser = Gtk.FileChooserDialog(
        title="Choose animation file", transient_for=dialog, action=Gtk.FileChooserAction.OPEN
    )
    chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.ACCEPT)
    try:
        if chooser.run() == Gtk.ResponseType.ACCEPT:
            field.set_text(chooser.get_filename() or "")
    finally:
        chooser.destroy()
