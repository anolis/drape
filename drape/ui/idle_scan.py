"""Connect viewport activity and window lifecycle to the isolated inspector."""

from .. import settings
from ..idle_inspection import IdleInspector
from .gtk import GLib


class ViewportInspector(IdleInspector):
    def __init__(self, window):
        super().__init__(enabled=settings.get("inspect_visible"))
        self.source = GLib.timeout_add(150, self._poll)
        window.connect("destroy", self._destroy)

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
