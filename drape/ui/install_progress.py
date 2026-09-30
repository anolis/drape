"""Installation stages and bounded, thread-safe updates for catalog progress bars."""

import threading

from .gtk import GLib, Pango


class InstallProgress:
    """Weighted installation progress; stage weights represent work, not elapsed time."""

    def __init__(self, closing=None):
        self._lock = threading.Lock()
        self._text = "Preparing download…"
        self._fraction = 0.0
        self._bars = []
        self._closing = closing
        # Coalesce rapid download callbacks instead of putting one idle callback
        # per chunk on GTK. Unmeasured stages advance at their boundaries.
        self._source = GLib.timeout_add(100, self._tick)

    def status(self, text, fraction=None):
        with self._lock:
            self._text = text
            if fraction is not None:
                self._fraction = max(self._fraction, min(fraction, 0.99))

    def download(self, done, total):
        with self._lock:
            if total:
                self._fraction = max(self._fraction, 0.40 * min(done / total, 1.0))
            self._text = "Downloading…"

    def complete(self):
        with self._lock:
            self._text, self._fraction = "Installed", 1.0
        for bar in self._bars:
            self._render(bar)

    def attach(self, bar):
        self._bars.append(bar)
        bar.connect("destroy", lambda *_: self._bars.remove(bar))
        bar.set_show_text(True)
        bar.set_ellipsize(Pango.EllipsizeMode.END)
        self._render(bar)

    def _render(self, bar):
        with self._lock:
            text, fraction = self._text, self._fraction
        bar.set_text(f"{fraction:.0%} · {text}")
        bar.set_tooltip_text(text)
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
