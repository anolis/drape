"""Real GTK notification checks, called inside the isolated UI smoke test."""

import threading
from unittest import mock

from drape import updater
from drape.ui import updates
from drape.ui.gtk import Gtk


def check():
    window = Gtk.Window()
    window._closing = threading.Event()
    window.notify = mock.Mock()
    header = Gtk.HeaderBar(title="Drape", show_close_button=True)
    window.set_titlebar(header)
    controller = updates.AppUpdates(window)
    window.show_all()
    callbacks = []
    with mock.patch.object(updates, "run_async", side_effect=lambda *args: callbacks.append(args)):
        controller.check()
    callbacks.pop()[1](updater.Update("old", "new", 2, "New changes"))
    header.show_all()
    assert controller.button.get_visible()
    window.notify("Applied an icon theme")
    assert controller.button.get_visible()
    controller.checking = False
    with mock.patch.object(updates, "run_async", side_effect=lambda *args: callbacks.append(args)):
        controller.check()
    callbacks.pop()[1](None)
    header.show_all()
    assert not controller.button.get_visible()
    window.destroy()
    print(
        "Update notice passed: persistent header action survives theme notifications and clears when current."
    )
