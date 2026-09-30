"""Installation stages and bounded, thread-safe updates for catalog progress bars."""

import threading

from .gtk import GLib, Pango


class InstallProgress:
    """Download percentages describe only the download; later stages pulse until done."""

    def __init__(self, closing=None):
        self._lock = threading.Lock()
        self._text = "Preparing download…"
        self._fraction = None
        self._bars = []
        self._closing = closing
        # A timer animates stages without measurable progress, and coalesces rapid
        # download callbacks instead of putting one idle callback per chunk on GTK.
        self._source = GLib.timeout_add(100, self._tick)

    def status(self, text):
        with self._lock:
            self._text, self._fraction = text, None

    def download(self, done, total):
        with self._lock:
            self._fraction = min(done / total, 1.0) if total else None
            self._text = f"Downloading {self._fraction:.0%}" if total else "Downloading…"

    def attach(self, bar):
        self._bars.append(bar)
        bar.connect("destroy", lambda *_: self._bars.remove(bar))
        bar.set_show_text(True)
        bar.set_ellipsize(Pango.EllipsizeMode.END)
        self._render(bar)

    def _render(self, bar):
        with self._lock:
            text, fraction = self._text, self._fraction
        bar.set_text(text)
        bar.set_tooltip_text(text)
        if fraction is None:
            bar.pulse()
        else:
            bar.set_fraction(fraction)

    def _tick(self):
        if self._closing is not None and self._closing.is_set():
            self._source = None
            return False
        for bar in self._bars:
            self._render(bar)
        return True

    def close(self):
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
