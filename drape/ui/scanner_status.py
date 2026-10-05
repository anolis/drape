"""Compact, shared compatibility activity display for the main window and profiles."""

from .gtk import Gtk, Pango


class ScannerStatus(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=8, margin=8)
        self.spinner = Gtk.Spinner(no_show_all=True, valign=Gtk.Align.CENTER)
        self.pack_start(self.spinner, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.label.get_style_context().add_class("dim-label")
        text.pack_start(self.label, False, False, 0)
        self.progress = Gtk.ProgressBar(no_show_all=True)
        text.pack_start(self.progress, False, False, 0)
        self.pack_start(text, True, True, 0)
        self.show_all()

    def update(self, status):
        self.label.set_text(status["text"])
        self.set_tooltip_text(status["text"])
        if status["spinning"]:
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()
        fraction = status["fraction"]
        self.progress.set_visible(fraction is not None)
        if fraction is not None:
            self.progress.set_fraction(fraction)
