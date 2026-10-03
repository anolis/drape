"""Frame-clock fades for cards, including deferred removal and compatibility filtering."""

from .gtk import Gtk


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

    def dismiss(self):
        if self.departing:
            return
        self.departing = True
        self.set_sensitive(False)
        self.fade(0, self.destroy)


class CardFlow(Gtk.FlowBox):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._predicate = lambda _card: True
        self.set_filter_func(lambda card: getattr(card, "_included", True))

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
            card.set_sensitive(wanted)
            if wanted:
                card._included = True
                if not card.get_mapped():
                    card.set_opacity(0)
                self.invalidate_filter()
                if card.get_mapped():
                    card.fade(1)
            else:

                def hidden(card=card):
                    card._included = False
                    self.invalidate_filter()

                card.fade(0, hidden)

    def clear(self):
        for card in self.cards():
            card.dismiss()

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
