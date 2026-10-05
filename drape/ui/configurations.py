"""Named personal theme combinations with explicit save and apply selection."""

import json

from .. import configurations, desktop
from ..configuration_store import ConfigurationError, ConfigurationStore
from .card_transitions import CardFlow, FadingCard
from .common import PART_NAMES, error_dialog, run_async
from .gtk import GLib, Gtk, Pango


def _label(text):
    return Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=65)


def application_notes(parts):
    notes = ["Choose the saved components to apply. Unavailable components cannot be selected."]
    if "lookandfeel" in parts:
        notes.append(
            "The KDE global theme is applied first, without resetting the desktop layout. "
            "It may also change appearance settings that are not checked here. "
            "Other checked components are applied afterward."
        )
    if "libadwaita" in parts:
        notes.append(
            f"Native GNOME styling replaces {desktop.libadwaita.CONFIG_HOME / 'gtk-4.0/gtk.css'} "
            "with a theme import, using the current light/dark preference. The original CSS "
            "is backed up; restore it from Native GNOME setup. " + desktop.libadwaita.RESTART_NOTE
        )
    if "kvantum" in parts:
        profiles = ", ".join(str(p) for p in desktop.qt.login_profiles(desktop.qt.HOME))
        notes.append(
            "Kvantum selects the saved Qt theme and enables the session engine in "
            f"{desktop.qt.CONFIG_HOME / 'environment.d/90-drape-qt.conf'} and login profiles "
            f"({profiles}). Previous settings are backed up; restore them from Kvantum setup. "
            + desktop.qt.RESTART_NOTE
        )
    if "cursors" in parts:
        notes.append(
            "Cursor selection also updates Xcursor defaults and cursor login settings. "
            + desktop.cursors.RESTART_NOTE
        )
    return "\n\n".join(notes)


class ConfigurationsPage(Gtk.ScrolledWindow):
    def __init__(self, window, store=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = window
        self.store = store or ConfigurationStore()
        self.alive, self.busy = True, False
        self.connect("destroy", lambda *_: setattr(self, "alive", False))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=24)
        self.add(body)
        heading = Gtk.Label(xalign=0)
        heading.set_markup("<big><b>My configurations</b></big>")
        body.pack_start(heading, False, False, 0)
        body.pack_start(
            _label(
                "Save your current theme components as a named configuration. Mix themes from "
                "different packs, then bring that combination back whenever you want. "
                "Configurations use the theme files already on this machine."
            ),
            False,
            False,
            0,
        )
        actions = Gtk.Box(spacing=8)
        self.save_button = Gtk.Button(label="Save current appearance…")
        self.save_button.get_style_context().add_class("suggested-action")
        self.save_button.connect("clicked", self._save_current)
        actions.pack_start(self.save_button, False, False, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda *_: self.load())
        actions.pack_start(refresh, False, False, 0)
        body.pack_start(actions, False, False, 0)
        self.status = _label("")
        body.pack_start(self.status, False, False, 0)
        self.flow = CardFlow(
            selection_mode=Gtk.SelectionMode.NONE,
            min_children_per_line=1,
            max_children_per_line=3,
            row_spacing=12,
            column_spacing=12,
            homogeneous=True,
        )
        body.pack_start(self.flow, False, False, 0)

    def load(self):
        try:
            entries = self.store.load()
        except ConfigurationError as exc:
            self.status.set_text(str(exc))
            self.save_button.set_sensitive(False)
            self.flow.clear()
            self.show_all()
            return
        self.save_button.set_sensitive(not self.busy)
        self.status.set_text(
            "Reading current appearance…"
            if self.busy
            else f"{len(entries)} saved configurations"
            if entries
            else "No configurations saved yet. Set up a look you like, then save it here."
        )
        self.flow.reconcile(
            [
                (
                    key,
                    json.dumps(entry, sort_keys=True),
                    lambda key=key, entry=entry: self._card(key, entry),
                )
                for key, entry in sorted(
                    entries.items(), key=lambda pair: pair[1]["name"].casefold()
                )
            ]
        )
        self._set_busy(self.busy)
        self.show_all()

    def _set_busy(self, busy):
        self.busy = busy
        self.save_button.set_sensitive(not busy)
        for card in self.flow.cards():
            for button in card.action_buttons:
                button.set_sensitive(not busy)

    def _card(self, key, entry):
        card = FadingCard()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=32)
        title.set_markup(f"<b>{GLib.markup_escape_text(entry['name'])}</b>")
        title.set_tooltip_text(entry["name"])
        box.pack_start(title, False, False, 0)
        context = _label(f"Saved on {entry['desktop']} · {entry['wm']}")
        context.get_style_context().add_class("dim-label")
        box.pack_start(context, False, False, 0)
        summary = _label(
            "\n".join(
                f"{PART_NAMES.get(c['part'], c['part'])}: {configurations.component_name(c)}"
                for c in entry["components"]
            )
        )
        summary.set_max_width_chars(32)
        box.pack_start(summary, True, True, 0)
        actions = Gtk.Box(spacing=6)
        apply_button = Gtk.Button(label="Apply…", sensitive=not self.busy)
        apply_button.connect("clicked", lambda button: self._apply(button, key))
        actions.pack_start(apply_button, False, False, 0)
        rename = Gtk.Button(label="Rename…")
        rename.connect("clicked", lambda *_: self._rename(key))
        actions.pack_start(rename, False, False, 0)
        delete = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
        delete.set_tooltip_text("Delete this saved configuration")
        delete.connect("clicked", lambda *_: self._delete(key))
        actions.pack_end(delete, False, False, 0)
        box.pack_start(actions, False, False, 0)
        card.add(box)
        card.action_buttons = (apply_button, rename, delete)
        return card

    @staticmethod
    def _dialog(window, title, accept):
        dialog = Gtk.Dialog(title=title, transient_for=window, modal=True)
        dialog.set_default_size(620, 480)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, accept, Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(body)
        dialog.get_content_area().pack_start(scroller, True, True, 0)
        return dialog, body

    def _save_current(self, _button):
        if self.busy:
            return
        self._set_busy(True)
        self.status.set_text("Reading current appearance…")

        def done(result):
            if not self.alive:
                return
            self.busy = False
            self.load()
            snapshot, warnings = result
            self._save_snapshot(snapshot, warnings)

        run_async(configurations.capture, done, self._failed)

    def _save_snapshot(self, snapshot, warnings):
        if not snapshot["components"]:
            error_dialog(
                self.win,
                "Nothing to save",
                "No selected theme components could be read for this desktop.",
            )
            return
        dialog, body = self._dialog(self.win, "Save current appearance", "Save configuration")
        name = Gtk.Entry(placeholder_text="Configuration name", activates_default=True)
        body.pack_start(name, False, False, 0)
        body.pack_start(
            _label("Choose the currently selected components to remember."), False, False, 0
        )
        selectors = []
        for component in snapshot["components"]:
            check = Gtk.CheckButton(
                label=f"{PART_NAMES.get(component['part'], component['part'])}: "
                + configurations.component_name(component),
                active=True,
            )
            body.pack_start(check, False, False, 0)
            selectors.append((component, check))
        if warnings:
            body.pack_start(
                _label("Some settings could not be read:\n" + "\n".join(warnings)), False, False, 0
            )
        body.pack_start(
            _label(
                "This saves theme selections, not a copy of the theme files. Login, boot and panel layouts are managed separately."
            ),
            False,
            False,
            0,
        )
        error = _label("")
        body.pack_start(error, False, False, 0)

        def validate(*_):
            dialog.set_response_sensitive(
                Gtk.ResponseType.ACCEPT,
                bool(name.get_text().strip()) and any(check.get_active() for _, check in selectors),
            )

        name.connect("changed", validate)
        for _, check in selectors:
            check.connect("toggled", validate)
        validate()
        dialog.show_all()
        name.grab_focus()
        while dialog.run() == Gtk.ResponseType.ACCEPT:
            selected = [c for c, check in selectors if check.get_active()]
            try:
                self.store.save(name.get_text(), dict(snapshot, components=selected))
            except ConfigurationError as exc:
                error.set_text(str(exc))
                continue
            self.win.notify(f"Saved configuration {name.get_text().strip()}.")
            break
        dialog.destroy()
        self.load()

    def _apply(self, button, key):
        if self.busy:
            return
        try:
            entry = self.store.load()[key]
        except (ConfigurationError, KeyError) as exc:
            error_dialog(self.win, "Cannot read configuration", exc)
            return
        self._set_busy(True)
        self.status.set_text("Checking saved components…")

        def reviewed(rows):
            if not self.alive:
                return
            selected = self._choose_application(entry, rows)
            if not selected:
                self.busy = False
                self.load()
                return
            self.status.set_text(f"Applying {entry['name']}…")

            def done(result):
                if not self.alive:
                    return
                self.busy = False
                applied, failed = result
                self.load()
                self.win.refresh_item()
                if applied:
                    text = f"Applied {entry['name']} ({', '.join(PART_NAMES.get(p, p) for p in applied)})."
                    for part, note in (
                        ("kvantum", desktop.qt.RESTART_NOTE),
                        ("libadwaita", desktop.libadwaita.RESTART_NOTE),
                        ("cursors", desktop.cursors.RESTART_NOTE),
                    ):
                        if part in applied:
                            text += " " + note
                    self.win.notify(text)
                if failed:
                    error_dialog(
                        self.win,
                        "Some configuration components could not be applied",
                        "\n".join(
                            f"{PART_NAMES.get(part, part)}: {message}" for part, message in failed
                        ),
                    )

            run_async(lambda: configurations.apply(entry, selected), done, self._failed)

        run_async(lambda: configurations.review(entry), reviewed, self._failed)

    def _choose_application(self, entry, rows):
        dialog, body = self._dialog(self.win, f"Apply {entry['name']}", "Apply selected")
        body.pack_start(
            _label("Review the changes for this desktop, then choose which components to apply."),
            False,
            False,
            0,
        )
        selectors = []
        for row in rows:
            component, reason, current = row["component"], row["reason"], row["current"]
            part = component["part"]
            current_name = (
                configurations.component_name({"part": part, "value": current})
                if isinstance(current, str)
                else "Unknown"
            )
            check = Gtk.CheckButton(
                label=f"{PART_NAMES.get(part, part)}: {current_name} → {configurations.component_name(component)}",
                active=not reason,
                sensitive=not reason,
            )
            body.pack_start(check, False, False, 0)
            if reason:
                body.pack_start(_label(reason), False, False, 0)
            selectors.append((part, check))
        note = _label("")
        body.pack_start(note, False, False, 0)

        def changed(*_):
            selected = [
                part for part, check in selectors if check.get_active() and check.get_sensitive()
            ]
            dialog.set_response_sensitive(Gtk.ResponseType.ACCEPT, bool(selected))
            note.set_text(application_notes(selected))

        for _, check in selectors:
            check.connect("toggled", changed)
        changed()
        dialog.show_all()
        accepted = dialog.run() == Gtk.ResponseType.ACCEPT
        selected = (
            [part for part, check in selectors if check.get_active() and check.get_sensitive()]
            if accepted
            else []
        )
        dialog.destroy()
        return selected

    def _failed(self, exc):
        if self.alive:
            self.busy = False
            self.load()
            error_dialog(self.win, "Configuration action failed", exc)

    def _rename(self, key):
        try:
            entry = self.store.load()[key]
            dialog, body = self._dialog(self.win, "Rename configuration", "Rename")
            name = Gtk.Entry(text=entry["name"], activates_default=True)
            body.pack_start(name, False, False, 0)
            error = _label("")
            body.pack_start(error, False, False, 0)
            dialog.show_all()
            name.grab_focus()
            while dialog.run() == Gtk.ResponseType.ACCEPT:
                try:
                    self.store.rename(key, name.get_text())
                except ConfigurationError as exc:
                    error.set_text(str(exc))
                    continue
                break
            dialog.destroy()
            self.load()
        except (ConfigurationError, KeyError) as exc:
            error_dialog(self.win, "Cannot rename configuration", exc)

    def _delete(self, key):
        try:
            entry = self.store.load()[key]
            dialog = Gtk.MessageDialog(
                transient_for=self.win,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text=f"Delete configuration {entry['name']}?",
            )
            dialog.format_secondary_text(
                "This removes the saved configuration. Theme files and your current appearance are kept."
            )
            accepted = dialog.run() == Gtk.ResponseType.YES
            dialog.destroy()
            if accepted:
                self.store.delete(key)
                self.load()
        except (ConfigurationError, KeyError) as exc:
            error_dialog(self.win, "Cannot delete configuration", exc)
