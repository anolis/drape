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

    def _update_status(self, status):
        for view in list(self.status_views):
            view.update(status)

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
