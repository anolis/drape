"""Connect viewport activity and window lifecycle to the isolated inspector."""

from weakref import WeakSet

from .. import installer, settings
from ..idle_inspection import IdleInspector
from .gtk import GLib
from .scanner_status import ScannerStatus


class ViewportInspector(IdleInspector):
    def __init__(self, window):
        super().__init__(enabled=settings.get("inspect_visible"))
        self.status_views = WeakSet()
        self.status_source = None
        self.busy_cards = WeakSet()
        self.on_status = self._update_status
        self.local_manifest = installer.MANIFEST
        self.source = GLib.timeout_add(150, self._poll)
        window.connect("destroy", self._destroy)

    def create_status_bar(self):
        view = ScannerStatus()
        self.status_views.add(view)
        view.connect("destroy", lambda *_: self.status_views.discard(view))
        view.update(self.status)
        return view

    def _update_status(self, _status):
        # Adjustment changes may arrive during GTK layout. Repaint only at idle.
        if self.status_source is None:
            self.status_source = GLib.idle_add(self._paint_status)

    def _paint_status(self):
        self.status_source = None
        for view in list(self.status_views):
            view.update(self.status)
        return False

    def _card_busy(self, card, busy):
        card._scan_busy_target = busy
        if card in self.busy_cards:
            return
        self.busy_cards.add(card)

        def update():
            self.busy_cards.discard(card)
            if card._scan_alive and not card.in_destruction():
                super(ViewportInspector, self)._card_busy(card, card._scan_busy_target)
            return False

        GLib.idle_add(update)

    def _poll(self):
        self.tick()
        return True

    def register_view(self, flow, scroller):
        self.register(flow)
        handlers = []
        for adjustment in (scroller.get_vadjustment(), scroller.get_hadjustment()):
            for signal in ("value-changed", "changed"):
                handlers.append(
                    (adjustment, adjustment.connect(signal, lambda *_: self.activity(flow)))
                )
        flow.connect("map", lambda *_: self.activity(flow))
        flow.connect("unmap", lambda *_: self.activity(flow))

        def destroy(*_):
            self.unregister(flow)
            for adjustment, handler in handlers:
                adjustment.disconnect(handler)

        flow.connect("destroy", destroy)

    def _destroy(self, *_):
        GLib.source_remove(self.source)
        self.close()
        if self.status_source is not None:
            GLib.source_remove(self.status_source)
            self.status_source = None
