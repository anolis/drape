"""Compatibility checks deferred by HTTP 429, released per host once its cooldown ends."""

import time
from collections import OrderedDict

from .gtk import GLib


class _Host:
    def __init__(self):
        self.jobs = OrderedDict()  # key -> job, oldest first
        self.ready_at = 0.0  # monotonic time the host's cooldown ends
        self.timer = None
        self.running = False


class RetryQueue:
    """Rate-limited checks wait here instead of each arming its own timer.

    Each host has one queue and at most one timer. When its cooldown ends a single job runs, and
    the next starts only after it finishes (plus SPACING), so a host that is still overloaded sees
    one request rather than a burst. A job that is rate-limited again re-queues itself, which
    pushes the host's cooldown back for everything still waiting.

    Call from the main thread only; jobs run through `submit` (a worker pool)."""

    SPACING = 1.0  # seconds between one released job finishing and the next starting

    def __init__(self, submit):
        self._submit = submit
        self._hosts = {}

    def add(self, host, key, job, delay):
        """Run `job` once `host` has cooled down for `delay` seconds. A waiting job with the same
        key is replaced and keeps its place in line."""
        state = self._hosts.setdefault(host, _Host())
        state.jobs[key] = job
        state.ready_at = max(state.ready_at, time.monotonic() + delay)
        self._arm(host, state)

    def cancel(self, predicate):
        """Drop waiting jobs whose key matches; one already running is left to finish."""
        for host, state in list(self._hosts.items()):
            for key in [key for key in state.jobs if predicate(key)]:
                del state.jobs[key]
            self._forget_if_idle(host, state)

    def pending(self, host=None):
        if host is not None:
            return list(self._hosts[host].jobs) if host in self._hosts else []
        return [key for state in self._hosts.values() for key in state.jobs]

    def _arm(self, host, state):
        if state.timer is not None or state.running or not state.jobs:
            return
        wait = max(0.0, state.ready_at - time.monotonic())
        state.timer = GLib.timeout_add(max(1, round(wait * 1000)), self._release, host)

    def _release(self, host):
        state = self._hosts.get(host)
        if state is None:
            return False
        state.timer = None
        if not state.jobs:
            self._forget_if_idle(host, state)
            return False
        if state.ready_at > time.monotonic():  # pushed back while the timer was waiting
            self._arm(host, state)
            return False
        _, job = state.jobs.popitem(last=False)
        state.running = True

        def run():
            try:
                job()
            finally:
                GLib.idle_add(self._finished, host)

        self._submit(run)
        return False

    def _finished(self, host):
        state = self._hosts.get(host)
        if state is not None:
            state.running = False
            state.ready_at = max(state.ready_at, time.monotonic() + self.SPACING)
            self._arm(host, state)
            self._forget_if_idle(host, state)
        return False

    def _forget_if_idle(self, host, state):
        if not state.jobs and not state.running:
            if state.timer is not None:
                GLib.source_remove(state.timer)
            self._hosts.pop(host, None)
