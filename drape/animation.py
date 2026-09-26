"""Animated previews that watch their own CPU cost.

In "auto" mode previews play on their own while drape measures how much CPU the animations take.
If they average more than THRESHOLD percent of one core, drape switches to playing a preview only
while the mouse is over its card, for the rest of the session.
"""

import collections
import os
import time
import weakref

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

THRESHOLD = 10.0   # percent of one core, like `top`
WINDOW = 5         # seconds averaged before deciding
MODES = ("auto", "always", "hover")


def main_thread_cpu():
    """CPU seconds used by the main (GTK) thread, where animation frames are drawn. Downloads and
    decoding happen on other threads and don't count against the animations."""
    try:
        with open(f"/proc/self/task/{os.getpid()}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return time.process_time()


class Governor:
    def __init__(self, get_mode, busy=lambda: False, on_switch=None, sampler=main_thread_cpu,
                 clock=time.monotonic):
        self.get_mode = get_mode      # returns "auto", "always" or "hover"
        self.busy = busy              # True while the UI is doing other work (loading a page)
        self.on_switch = on_switch    # called once when auto mode falls back to hover
        self.sampler, self.clock = sampler, clock
        self.players = weakref.WeakSet()
        self.switched = False
        self.samples = collections.deque(maxlen=WINDOW)
        self.timer = None
        self.last = None

    def hover_only(self):
        mode = self.get_mode()
        return mode == "hover" or (mode == "auto" and self.switched)

    def register(self, player):
        self.players.add(player)
        if self.get_mode() == "auto" and not self.switched and self.timer is None:
            self.last = (self.sampler(), self.clock())
            self.timer = GLib.timeout_add_seconds(1, self._tick)

    def refresh(self):
        """Settings changed: re-apply to every preview."""
        for p in list(self.players):
            p.update()
        if self.get_mode() == "auto" and not self.switched and self.timer is None and self.players:
            self.last = (self.sampler(), self.clock())
            self.timer = GLib.timeout_add_seconds(1, self._tick)

    def measure(self):
        """One sample; returns True while sampling should continue. Separate from GLib for tests."""
        cpu, now = self.sampler(), self.clock()
        prev_cpu, prev_now = self.last
        self.last = (cpu, now)
        playing = any(p.running for p in list(self.players))
        if not playing:
            if not self.players:
                return False
            return True
        if self.busy() or now <= prev_now:
            return True  # loading a page costs CPU too; only judge the animations on their own
        self.samples.append((cpu - prev_cpu) / (now - prev_now) * 100)
        if len(self.samples) == WINDOW and sum(self.samples) / WINDOW > THRESHOLD:
            self.switched = True
            for p in list(self.players):
                p.update()
            if self.on_switch:
                self.on_switch(sum(self.samples) / WINDOW)
            return False
        return True

    def _tick(self):
        keep = self.get_mode() == "auto" and not self.switched and self.measure()
        if not keep:
            self.timer = None
        return keep


CARD_FPS = 15  # thumbnails don't need 25-50 fps; halves the cost of fast GIFs


def schedule(delays, max_fps=None):
    """(tick_ms, [frame index for each tick]) that plays frames with these delays at a steady rate of
    at most max_fps: each tick shows whichever frame would be on screen at that moment."""
    step = max(min(delays), 1000 / max_fps if max_fps else 0, 10)
    total = sum(delays)
    ticks, frame, frame_end = [], 0, delays[0]
    t = 0.0
    while t < total - 1e-6:
        while t >= frame_end - 1e-6 and frame < len(delays) - 1:
            frame += 1
            frame_end += delays[frame]
        ticks.append(frame)
        t += step
    return step, ticks


def build_animation(frames, max_fps=None):
    """A GdkPixbufSimpleAnim from [(Pixbuf, delay_ms)]. GtkImage plays these itself, redrawing
    only the picture (cheaper than swapping pixbufs) and stopping when the image is hidden."""
    step, ticks = schedule([d for _pb, d in frames], max_fps)
    w, h = frames[0][0].get_width(), frames[0][0].get_height()
    anim = GdkPixbuf.PixbufSimpleAnim.new(w, h, 1000 / step)
    anim.set_loop(True)
    for i in ticks:
        anim.add_frame(frames[i][0])
    return anim


class Player:
    """Plays an animated preview in a Gtk.Image. In hover mode it only plays while the mouse is over
    the card; previews outside a card (like the details view) always play, at full frame rate."""

    def __init__(self, image, frames, governor):
        self.image, self.gov = image, governor
        self.first = frames[0][0]
        self.box = image.get_ancestor(Gtk.EventBox)
        self.anim = build_animation(frames, CARD_FPS if self.box is not None else None)
        self.playing, self.hovered = False, False
        self.handlers = []
        if self.box is not None:
            self.handlers += [(self.box, self.box.connect("enter-notify-event", self._enter)),
                              (self.box, self.box.connect("leave-notify-event", self._leave))]
        image.set_from_pixbuf(self.first)
        governor.register(self)
        self.update()

    @property
    def running(self):
        return self.playing and self.image.get_mapped()

    def _enter(self, *_):
        self.hovered = True
        self.update()
        return False

    def _leave(self, _w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:  # moving onto the card's own spinner isn't leaving
            self.hovered = False
            self.update()
        return False

    def update(self):
        play = self.box is None or self.hovered or not self.gov.hover_only()
        if play and not self.playing:
            self.image.set_from_animation(self.anim)
        elif not play and self.playing:
            self.image.set_from_pixbuf(self.first)  # rest on the first frame
        self.playing = play

    def cancel(self):
        for obj, h in self.handlers:
            if obj.handler_is_connected(h):
                obj.disconnect(h)
        self.playing = False
        self.gov.players.discard(self)
