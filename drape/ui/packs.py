"""Choose one compatible variant per appearance part of a downloaded bundle."""

from .gtk import Gtk
import configparser
from pathlib import Path
from .. import desktop, installer
from .common import PART_NAMES, error_dialog, in_use


ORDER = [
    "kvantum",
    "lookandfeel",
    "plasma",
    "colors",
    "gtk",
    "libadwaita",
    "desktop",
    "wm",
    "xfwm",
    "aurorae",
    "icons",
    "cursors",
    "wallpapers",
]


# Group compatible variants by appearance component


def choices(entry):
    groups = {}
    for component in entry["components"]:
        for part in desktop.compatible_parts(component):
            if part in desktop.PACK_PARTS:
                groups.setdefault(part, []).append(component)
    return groups


def component_notes(entry, groups):
    """Metatheme references suggest companion themes; they do not install those files."""
    notes = [f"{len(groups)} compatible components for this desktop."]
    if "libadwaita" in groups:
        notes.append(
            "GNOME / libadwaita replaces your user GTK 4 stylesheet (gtk.css) with an import of the selected theme, using GNOME's light/dark preference. The original file is backed up; restore it from Native GNOME setup. Restart native GNOME apps afterward."
        )
    included = {part for component in entry["components"] for part in component["provides"]}
    references = set()
    for component in entry["components"]:
        metadata = Path(component["path"]) / "index.theme"
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(metadata)
            section = parser["X-GNOME-Metatheme"]
            for part, key in (("icons", "IconTheme"), ("cursors", "CursorTheme")):
                name = section.get(key)
                if name and part not in included:
                    references.add(f"{PART_NAMES[part]}: {name} (separate download)")
        except (OSError, UnicodeError, configparser.Error, KeyError):
            continue
    notes.extend(sorted(references))
    return "\n".join(notes)


# Explicit per-component selection and application


def apply_pack(window, key, only_kind=None):
    try:
        entry = installer.load_manifest().get(key)
    except installer.InstallError as exc:
        error_dialog(window, "Cannot load installed themes", exc)
        return
    groups = choices(entry) if entry else {}
    if only_kind:
        part = desktop.theme_part(only_kind)
        groups = {part: groups[part]} if part in groups else {}
    if not groups:
        window.notify("This pack has no compatible combination for the current desktop.")
        return
    dialog = Gtk.Dialog(title=f"Apply {entry['title']}", transient_for=window, modal=True)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply selected", Gtk.ResponseType.ACCEPT)
    area = dialog.get_content_area()
    grid = Gtk.Grid(column_spacing=16, row_spacing=10, margin=16)
    area.pack_start(grid, True, True, 0)
    selectors = {}
    for row, part in enumerate(p for p in ORDER if p in groups):
        components = groups[part]
        combo = Gtk.ComboBoxText()
        combo.append("skip", "Keep current")
        for index, component in enumerate(components):
            combo.append(str(index), component["name"])
        chosen = entry.get("chosen", {}).get(part)
        preferred = next(
            (i for i, c in enumerate(components) if c["name"] == chosen),
            next((i for i, c in enumerate(components) if in_use(c)), 0),
        )
        combo.set_active_id(str(preferred))
        grid.attach(Gtk.Label(label=PART_NAMES.get(part, part), xalign=0), 0, row, 1, 1)
        grid.attach(combo, 1, row, 1, 1)
        selectors[part] = combo
    note = Gtk.Label(label=component_notes(entry, groups), xalign=0, wrap=True, margin=16)
    note.get_style_context().add_class("dim-label")
    area.pack_start(note, False, False, 0)
    dialog.show_all()
    accepted = dialog.run() == Gtk.ResponseType.ACCEPT
    selected = (
        [
            (part, groups[part][int(combo.get_active_id())])
            for part, combo in selectors.items()
            if combo.get_active_id() != "skip"
        ]
        if accepted
        else []
    )
    dialog.destroy()
    applied = []
    for part, component in selected:
        try:
            done = desktop.apply_component(component, [part])
            if done:
                installer.set_chosen(key, part, component["name"])
        except (desktop.ApplyError, installer.InstallError) as exc:
            error_dialog(window, "Could not apply theme pack", exc)
            break
        if done:
            applied.extend(done)
    if applied:
        note = " " + desktop.qt.RESTART_NOTE if "kvantum" in applied else ""
        if "libadwaita" in applied:
            note += " " + desktop.libadwaita.RESTART_NOTE
        if "cursors" in applied:
            note += " " + desktop.cursors.RESTART_NOTE
        window.notify(
            f"Applied {entry['title']} ({', '.join(PART_NAMES.get(p, p) for p in applied)})." + note
        )
        window.refresh_item(key)
