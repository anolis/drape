"""Explicit consent for incomplete Cinnamon styling, without blocking GTK workers."""

import threading

from .. import desktop, installer
from .gtk import GLib, Gtk


def confirm(window, warnings, action="Install anyway"):
    dialog = Gtk.MessageDialog(
        transient_for=window,
        modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE,
        text="This Cinnamon style may look incomplete",
    )
    dialog.format_secondary_text(
        "\n".join(f"{warning['name']}: {warning['reason']}" for warning in warnings)
        + "\n\nMissing dialog styles can leave some prompts using default or incomplete styling. "
        "You can still use the rest of the theme and switch back from Installed or Cinnamon Themes."
    )
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, action, Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    accepted = dialog.run() == Gtk.ResponseType.ACCEPT
    dialog.destroy()
    return accepted


def confirm_install(window, warnings):
    """Ask on GTK's main loop while keeping the already downloaded/extracted archive."""
    ready = threading.Event()
    result = {"accepted": False, "error": None}

    def ask():
        try:
            if not window._closing.is_set():
                result["accepted"] = confirm(window, warnings)
        except Exception as error:  # noqa: BLE001 - relay UI failures to the installation worker
            result["error"] = error
        finally:
            ready.set()
        return False

    GLib.idle_add(ask)
    while not ready.wait(0.1):
        if window._closing.is_set():
            return False
    if result["error"]:
        raise result["error"]
    return result["accepted"]


def approve_application(window, component):
    reason = desktop.cinnamon_style_warning(component)
    if not reason or component.get("allow_incomplete_cinnamon"):
        return True
    if not confirm(window, [{"name": component["name"], "reason": reason}], "Apply anyway"):
        return False
    installer.accept_cinnamon_warning(component)
    return True
