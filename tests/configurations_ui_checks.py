"""Real GTK modal and lifecycle checks, called inside the isolated UI smoke test."""

from pathlib import Path
from unittest import mock

from drape import configurations
from drape.configuration_store import ConfigurationStore
from drape.ui import configurations as config_ui
from drape.ui import window as window_ui
from drape.ui.gtk import GLib, Gtk
from drape.ui.window import Window


def descendants(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from descendants(child)


def answer(title, callback):
    """Respond after the real modal has appeared, including nested GTK loops."""

    def ready():
        for dialog in Gtk.Window.list_toplevels():
            if (
                isinstance(dialog, Gtk.Dialog)
                and dialog.get_visible()
                and (dialog.get_title() or "") == title
            ):
                callback(dialog)
                return False
        return True

    return GLib.timeout_add(20, ready)


def check(window, temp, pump):
    store = ConfigurationStore(Path(temp) / "configurations.json")
    page = config_ui.ConfigurationsPage(window, store)
    window.add(page)
    window.show_all()
    page.load()
    assert "No configurations" in page.status.get_text()
    snapshot = {
        "desktop": "gnome",
        "wm": "Mutter",
        "components": [
            {"part": "gtk", "value": "Graphite-Dark"},
            {"part": "icons", "value": "Papirus"},
        ],
    }

    def save(dialog):
        widgets = list(descendants(dialog.get_content_area()))
        entry = next(w for w in widgets if isinstance(w, Gtk.Entry))
        assert not dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).get_sensitive()
        entry.set_text("My evening <look>")
        assert dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).get_sensitive()
        dialog.response(Gtk.ResponseType.ACCEPT)

    answer("Save current appearance", save)
    with mock.patch.object(configurations, "capture", return_value=(snapshot, [])):
        page.save_button.clicked()
        pump(0.3)
    saved = store.load()
    assert len(saved) == 1
    key, entry = next(iter(saved.items()))
    assert entry["components"] == snapshot["components"]
    assert page.save_button.get_sensitive() and not page.busy
    card = page.flow.cards()[0]
    assert card._identity == key
    title = next(w for w in descendants(card) if isinstance(w, Gtk.Label))
    assert title.get_text() == "My evening <look>"  # User names remain literal, not markup.

    rows = [{"component": c, "current": "Other", "reason": ""} for c in entry["components"]]

    def choose(dialog):
        checks = [w for w in descendants(dialog) if isinstance(w, Gtk.CheckButton)]
        assert "Other → Graphite-Dark" in checks[0].get_label()
        checks[0].set_active(False)
        dialog.response(Gtk.ResponseType.ACCEPT)

    answer("Apply My evening <look>", choose)
    with (
        mock.patch.object(configurations, "review", return_value=rows),
        mock.patch.object(configurations, "apply", return_value=(["icons"], [])) as apply,
    ):
        card.action_buttons[0].clicked()
        pump(0.3)
    assert apply.call_args.args[1] == ["icons"]
    assert card.action_buttons[0].get_sensitive() and page.save_button.get_sensitive()

    # Cancellation cannot alter appearance; missing themes cannot be selected.
    rows[0]["reason"] = "Theme files are missing"

    def cancel(dialog):
        checks = [w for w in descendants(dialog) if isinstance(w, Gtk.CheckButton)]
        assert not checks[0].get_sensitive() and not checks[0].get_active()
        dialog.response(Gtk.ResponseType.CANCEL)

    answer("Apply My evening <look>", cancel)
    with (
        mock.patch.object(configurations, "review", return_value=rows),
        mock.patch.object(configurations, "apply") as apply,
    ):
        card.action_buttons[0].clicked()
        pump(0.3)
        apply.assert_not_called()

    def rename(dialog):
        next(w for w in descendants(dialog) if isinstance(w, Gtk.Entry)).set_text("Renamed look")
        dialog.response(Gtk.ResponseType.ACCEPT)

    answer("Rename configuration", rename)
    page._rename(key)
    pump(0.2)
    assert store.load()[key]["name"] == "Renamed look"
    restored = config_ui.ConfigurationsPage(window, ConfigurationStore(store.path))
    restored.load()
    assert len(restored.flow.cards()) == 1
    restored.destroy()

    # Delete has separate consent and leaves theme application untouched.
    def decline(dialog):
        dialog.response(Gtk.ResponseType.NO)

    answer("", decline)
    page._delete(key)
    assert key in store.load()

    def accept(dialog):
        dialog.response(Gtk.ResponseType.YES)

    answer("", accept)
    page._delete(key)
    assert store.load() == {}
    window.remove(page)
    page.destroy()
    # Exercise the real main-window navigation and toolbar state without catalog
    # requests, rather than testing the page only as a standalone widget.
    with (
        mock.patch.object(window_ui.BrowsePage, "load"),
        mock.patch.object(window_ui, "AppUpdates"),
        mock.patch.object(window_ui.Gdk.Display, "get_default", return_value=None),
        mock.patch.object(Window, "_start_prefetch", return_value=False),
        mock.patch.object(Window, "_check_theme_session", return_value=False),
    ):
        main = Window(None)
        main.idle_inspector.local_manifest = None
        main.stack.set_visible_child_name("configurations")
        assert main.stack.get_visible_child() is main.configurations
        assert not main.search.get_sensitive() and not main.sort_combo.get_sensitive()
        assert main._sidebar_list.get_selected_row().page == "configurations"
        main.destroy()

    print(
        "Configuration dialogs passed: save, reload, apply selection, missing files, cancel, rename and delete consent."
    )
