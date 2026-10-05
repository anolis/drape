"""Native GNOME style setup with explicit CSS changes and restoration."""

from .. import desktop, libadwaita
from .common import error_dialog, run_async
from .gtk import Gtk


def confirm_application(window, name):
    dialog = Gtk.MessageDialog(
        transient_for=window,
        modal=True,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.YES_NO,
        text=f"Apply {name} to native GNOME apps?",
    )
    root = libadwaita.CONFIG_HOME / "gtk-4.0"
    dialog.format_secondary_text(
        f"This replaces {root / 'gtk.css'} with an import of this theme's GTK 4 style. Your original file or symlink is backed up and can be restored from Native GNOME setup.\n\n"
        "GTK 4 themes may not cover every libadwaita widget or version. Restart Files, Settings and other native GNOME apps afterward. GNOME/X11 desktop-drawn title bars need logout/login to refresh. Application menu entries are not changed."
    )
    accepted = dialog.run() == Gtk.ResponseType.YES
    dialog.destroy()
    return accepted


class LibadwaitaSettingsPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = window
        self.alive = True
        self.connect("destroy", lambda *_: setattr(self, "alive", False))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        self.add(self.body)

    def load(self):
        for child in self.body.get_children():
            child.destroy()
        for text in (
            "Native GNOME / libadwaita appearance",
            "Files, Settings and other native GNOME apps use libadwaita. They need GTK 4 styles through user CSS rather than the GTK 3 theme setting. The override affects GTK 4 apps using this user's configuration. Sandboxed apps may need separate access, and themes may not cover every libadwaita widget or version.",
            "Native GNOME style: " + (libadwaita.get() or "No verified Drape override"),
            "Selected GTK application theme: " + (desktop.get("gtk") or "Unknown"),
            "Uses GNOME's light/dark preference when applying. Reapply after changing that preference.",
        ):
            self.body.pack_start(Gtk.Label(label=text, xalign=0, wrap=True), False, False, 0)
        name = desktop.get("gtk")
        apply = Gtk.Button(
            label="Apply current theme's GTK 4 style",
            sensitive=desktop.supported("libadwaita") and bool(libadwaita.theme_dir(name)),
        )
        apply.connect("clicked", lambda button: self._apply(button, name))
        self.body.pack_start(apply, False, False, 0)
        if not libadwaita.theme_dir(name):
            self.body.pack_start(
                Gtk.Label(
                    label="The current theme has no GTK 4 stylesheet. Choose one from GTK 4 themes or Installed → GNOME / libadwaita.",
                    xalign=0,
                    wrap=True,
                ),
                False,
                False,
                0,
            )
        browse = Gtk.Button(label="Browse GTK 4 themes", sensitive=desktop.supported("libadwaita"))
        browse.connect("clicked", lambda *_: self.win.go_to("libadwaita"))
        self.body.pack_start(browse, False, False, 0)
        restore = Gtk.Button(
            label="Restore previous native GNOME appearance", sensitive=libadwaita.configured()
        )
        restore.connect("clicked", self._restore)
        self.body.pack_start(restore, False, False, 0)
        self.body.pack_start(
            Gtk.Label(label=libadwaita.RESTART_NOTE, xalign=0, wrap=True), False, False, 0
        )
        self.show_all()

    def _apply(self, button, name):
        if not confirm_application(self.win, name):
            return
        button.set_sensitive(False)
        button.set_label("Checking GTK 4 style…")

        def done(_result):
            if self.alive:
                self.load()
                self.win.refresh_item()
                self.win.notify(libadwaita.RESTART_NOTE)

        def failed(error):
            if self.alive:
                self.load()
                error_dialog(self.win, "Could not apply native GNOME style", error)

        run_async(lambda: libadwaita.apply(name), done, failed)

    def _restore(self, _button):
        try:
            libadwaita.restore()
        except (OSError, ValueError) as error:
            error_dialog(self.win, "Could not restore native GNOME style", error)
            return
        self.load()
        self.win.refresh_item()
        self.win.notify("Original GTK 4 style files restored. " + libadwaita.RESTART_NOTE)
