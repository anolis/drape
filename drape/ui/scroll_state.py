"""Restore section positions after asynchronous content has enough height."""

from .. import settings
import math
from .gtk import GLib


class ScrollState:
    def __init__(self, scroller, key):
        self.scroller, self.key = scroller, key
        self.pending = None
        self.timer = None
        self.last_value = None
        self.adjustment = scroller.get_vadjustment()
        self.adjustment.connect("changed", self.restore)
        self.adjustment.connect("value-changed", self.changed)
        scroller.connect("map", self.restore)
        scroller.connect("scroll-event", self.user_scroll)
        scroller.get_vscrollbar().connect("button-press-event", self.user_scroll)
        scroller.connect("destroy", self.close)
        self.reset(key)

    def reset(self, key):
        if self.timer:
            self.save()
        self.key = key
        self.last_value = None
        value = settings.get("scroll_positions") or {}
        position = value.get(key, 0) if isinstance(value, dict) else 0
        self.pending = (
            max(0, float(position))
            if isinstance(position, (int, float)) and math.isfinite(position)
            else 0
        )

    def finish(self):
        """If a catalog shrank, keep the closest reachable position instead of waiting forever."""
        self.restore()
        if self.pending is not None and self.scroller.get_mapped():
            self.pending = None
            self.changed()

    def restore(self, *_):
        if self.pending is None or not self.scroller.get_mapped():
            return
        limit = max(0, self.adjustment.get_upper() - self.adjustment.get_page_size())
        target = self.pending
        self.adjustment.set_value(min(target, limit))
        if limit >= target:
            self.pending = None

    def changed(self, *_):
        if self.pending is not None or not self.scroller.get_mapped():
            return
        self.last_value = self.adjustment.get_value()
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(400, self.save)

    def user_scroll(self, *_):
        self.pending = None
        GLib.idle_add(self.changed)
        return False

    def save(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.pending is None:
            positions = settings.get("scroll_positions") or {}
            positions = positions if isinstance(positions, dict) else {}
            positions[self.key] = (
                self.last_value if self.last_value is not None else self.adjustment.get_value()
            )
            try:
                settings.set("scroll_positions", positions)
            except OSError:
                pass
        return False

    def close(self, *_):
        self.save()

    def hold(self):
        """Content reconciliation can briefly clamp an adjustment before new rows arrive."""
        if self.pending is None and self.scroller.get_mapped():
            if self.timer:
                self.save()
            self.pending = self.adjustment.get_value()


def track_section(widget, section):
    """Attach persistence to settings-page scrollers, including lazily built pages."""
    from .gtk import Gtk

    scrollers = []

    def walk(child):
        if isinstance(child, Gtk.ScrolledWindow):
            scrollers.append(child)
        elif isinstance(child, Gtk.Container):
            for nested in child.get_children():
                walk(nested)

    walk(widget)
    for index, scroller in enumerate(scrollers):
        if not hasattr(scroller, "_scroll_state"):
            scroller._scroll_state = ScrollState(scroller, f"section:{section}:{index}")
