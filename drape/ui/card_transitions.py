"""Frame-clock fades for cards, including deferred removal and compatibility filtering."""

from .gtk import GLib, Gtk


DURATION_US = 180_000


class FadingCard(Gtk.FlowBoxChild):
    def __init__(self):
        super().__init__()
        self.departing = False
        self._tick = None
        self._finish = None
        self.set_opacity(0)
        self.connect("map", self._mapped)
        self.connect("unmap", self._unmapped)
        self.connect("destroy", self._cancel)

    def _cancel(self, *_):
        if self._tick is not None:
            self.remove_tick_callback(self._tick)
            self._tick = None
        self._finish = None

    def _mapped(self, *_):
        if not self.departing:
            self.fade(1)

    def _unmapped(self, *_):
        finish = self._finish
        self._cancel()
        if finish:
            finish()
        elif not self.departing:
            self.set_opacity(0)

    def fade(self, target, finish=None):
        self._cancel()
        if not self.get_mapped() or not self.get_settings().get_property("gtk-enable-animations"):
            self.set_opacity(target)
            if finish:
                finish()
            return
        start = self.get_opacity()
        begun = self.get_frame_clock().get_frame_time()
        self._finish = finish

        def frame(_widget, clock):
            elapsed = min(1, max(0, (clock.get_frame_time() - begun) / DURATION_US))
            eased = elapsed * elapsed * (3 - 2 * elapsed)
            self.set_opacity(start + (target - start) * eased)
            if elapsed < 1:
                return True
            self._tick = None
            self._finish = None
            if finish:
                finish()
            return False

        self._tick = self.add_tick_callback(frame)

    def dismiss(self, defer=True):
        if self.departing:
            if not defer:
                self._cancel()
                self.destroy()
            return
        self.departing = True
        self.set_sensitive(False)
        parent = self.get_parent()
        if defer and isinstance(parent, CardFlow):

            def finish():
                if not parent.in_view(self):
                    self.set_opacity(1)
                    parent.defer_removal(self, lambda: self.fade(0, finish))
                else:
                    self.destroy()

            parent.defer_removal(self, lambda: self.fade(0, finish))
        else:
            self.fade(0, self.destroy)


class CardFlow(Gtk.FlowBox):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._predicate = lambda _card: True
        self._deferred = {}
        self._viewport = None
        self._viewport_handlers = []
        self._check_source = None
        self.set_filter_func(lambda card: getattr(card, "_included", True))
        self.connect("map", self._watch_viewport)
        self.connect("size-allocate", self._queue_check)
        self.connect("destroy", self._stop_watching)

    # Mapped GTK children can still be outside the scrolled viewport.

    def _watch_viewport(self, *_):
        viewport = self.get_ancestor(Gtk.ScrolledWindow)
        if viewport is not self._viewport:
            self._stop_watching()
            self._viewport = viewport
            if viewport:
                for adjustment in (viewport.get_vadjustment(), viewport.get_hadjustment()):
                    for signal in ("changed", "value-changed"):
                        self._viewport_handlers.append(
                            (adjustment, adjustment.connect(signal, self._queue_check))
                        )
        self._queue_check()

    def _stop_watching(self, *_):
        for adjustment, handler in self._viewport_handlers:
            adjustment.disconnect(handler)
        self._viewport_handlers.clear()
        self._viewport = None
        if self._check_source is not None:
            GLib.source_remove(self._check_source)
            self._check_source = None
        if _ and _[0] is self:
            self._deferred.clear()

    def in_view(self, card):
        viewport = self.get_ancestor(Gtk.ScrolledWindow)
        if viewport is None:
            return True  # Standalone grids retain their existing removal behavior.
        if not card.get_mapped() or not viewport.get_mapped():
            return False
        position = card.translate_coordinates(viewport, 0, 0)
        if position is None:
            return False
        x, y = position
        allocation = card.get_allocation()
        return (
            x < viewport.get_allocated_width()
            and x + allocation.width > 0
            and y < viewport.get_allocated_height()
            and y + allocation.height > 0
        )

    def defer_removal(self, card, remove):
        """Keep an off-screen allocation intact until scrolling brings it back into view."""
        self._deferred.pop(card, None)
        if self.in_view(card):
            remove()
        else:
            self._deferred[card] = remove

    def _queue_check(self, *_):
        if self._deferred and self._check_source is None:
            self._check_source = GLib.idle_add(self._check_deferred)

    def _check_deferred(self):
        self._check_source = None
        for card, remove in list(self._deferred.items()):
            if card.get_parent() is not self:
                self._deferred.pop(card, None)
            elif self.in_view(card):
                self._deferred.pop(card, None)
                remove()
        return False

    def _hide_card(self, card):
        card.fade(0, lambda: self._finish_hide(card))

    def _finish_hide(self, card):
        if card._wanted:
            return
        if not self.in_view(card):
            # Scrolling can take a card out of view during its fade, too.
            card.set_opacity(1)
            self.defer_removal(card, lambda: self._hide_card(card))
            return
        card._included = False
        self.invalidate_filter()

    def cards(self):
        return [card for card in self.get_children() if not card.departing]

    def set_card_filter(self, predicate):
        self._predicate = predicate
        self.refilter()

    def add(self, card):
        card._included = bool(self._predicate(card))
        card._wanted = card._included
        super().add(card)

    def refilter(self):
        for card in self.cards():
            wanted = bool(self._predicate(card))
            if wanted == card._wanted:
                continue
            card._wanted = wanted
            self._deferred.pop(card, None)  # A reversed filter cancels pending removal.
            card.set_sensitive(wanted)
            if wanted:
                card._included = True
                if not card.get_mapped():
                    card.set_opacity(0)
                self.invalidate_filter()
                if card.get_mapped():
                    card.fade(1)
            else:
                self.defer_removal(card, lambda card=card: self._hide_card(card))

    def clear(self):
        # Explicit navigation/reload discards the old grid, including deferred departures.
        self._deferred.clear()
        for card in self.get_children():
            card.dismiss(defer=False)

    def reconcile(self, entries):
        """Keep unchanged cards; fade only additions, removals and changed content."""
        existing = {card._identity: card for card in self.cards()}
        wanted = set()
        for position, (identity, snapshot, create) in enumerate(entries):
            wanted.add(identity)
            old = existing.get(identity)
            if old is not None and old._snapshot == snapshot:
                old._position = position
                continue
            if old is not None:
                old.dismiss()
            card = create()
            card._identity, card._snapshot = identity, snapshot
            card._position = position
            self.add(card)
        for identity, card in existing.items():
            if identity not in wanted:
                card.dismiss()
        self.set_sort_func(lambda a, b: a._position - b._position)
        self.invalidate_sort()
        self.show_all()
