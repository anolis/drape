"""One cancellable, resource-bounded inspection process for the current viewport."""

import json
import os
import subprocess
import sys
import tempfile
import time
import weakref
from dataclasses import asdict
from pathlib import Path

from . import compatibility, http, pling

IDLE_SECONDS = 2
JOB_GAP = 5
JOB_TIMEOUT = 90


class IdleInspector:
    """GTK owns the timer/signals; this controller never opens theme archives."""

    def __init__(self, enabled=True, clock=time.monotonic, spawn=subprocess.Popen, index=None):
        self.enabled = enabled
        self.clock, self.spawn = clock, spawn
        self.index = index or compatibility.Index()
        self.flows = weakref.WeakKeyDictionary()
        self.active = None
        self.retired = []
        self.next_check = 0
        self.retry = {}

    def register(self, flow):
        self.flows[flow] = self.clock()

    def activity(self, flow):
        if flow in self.flows:
            self.flows[flow] = self.clock()
        if self.active and not self._visible(self.active[0], self.active[1]):
            self.cancel()

    def unregister(self, flow):
        self.flows.pop(flow, None)
        if self.active and self.active[0] is flow:
            self.cancel()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.cancel()

    @staticmethod
    def _visible(flow, card):
        return (
            flow.get_mapped()
            and flow.get_toplevel().is_active()
            and card.get_parent() is flow
            and not card.departing
            and card._scan_alive
            and flow.in_view(card)
        )

    def _key(self, item, file):
        return item.id, file.name, self.index.revision(item, file)

    def cancel(self):
        if self.active is None:
            return
        _, _, _, key, process, _ = self.active
        self.active = None
        self.retry[key] = self.clock() + JOB_GAP
        self.next_check = max(self.next_check, self.clock() + JOB_GAP)
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        self.retired.append((process, self.clock()))

    def close(self):
        self.cancel()
        # Shutdown is allowed to reap a killed child; ordinary scrolling never waits.
        for process, _ in self.retired:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
        self.retired.clear()
        self.flows.clear()

    def _reap(self, now):
        pending = []
        for process, stopped in self.retired:
            if process.poll() is not None:
                process.stdout.close()
            else:
                if now - stopped > 0.3:
                    process.kill()
                pending.append((process, stopped))
        self.retired = pending

    def tick(self):
        now = self.clock()
        self._reap(now)
        if self.active:
            flow, card, file, key, process, started = self.active
            if not self.enabled or not self._visible(flow, card):
                self.cancel()
            elif now - started > JOB_TIMEOUT:
                self.cancel()
                self.retry[key] = now + 300
            elif process.poll() is not None:
                self.active = None
                self.next_check = now + JOB_GAP
                try:
                    result = json.loads(process.stdout.read(8192))
                except (ValueError, OSError):
                    result = {"status": "failed"}
                finally:
                    process.stdout.close()
                status = result.get("status")
                if status in ("cached", "checked"):
                    if "file" in result:
                        fresh = pling.Download(**result["file"])
                        for registered in list(self.flows):
                            for other in registered.cards():
                                if other.item.id == card.item.id:
                                    other.item.changed = result["changed"]
                                    other.item.files = [
                                        fresh if f.name == file.name else f
                                        for f in other.item.files
                                    ]
                    self._refresh(card.item.id)
                elif status == "rate_limited":
                    delay = min(http.MAX_RETRY_AFTER, max(1, result.get("retry_after", 60)))
                    self.next_check = now + delay
                    card.compatibility_note.set_text("Check delayed: server rate limit")
                    card.compatibility_note.show()
                else:
                    self.retry[key] = now + 300
        if self.active or self.retired or not self.enabled or now < self.next_check:
            return
        # Reading SQLite is cheap. No download is needed for cached cards, even after relaunch.
        for flow, moved in list(self.flows.items()):
            if now - moved < IDLE_SECONDS or not flow.get_mapped():
                continue
            cards = [card for card in flow.cards() if self._visible(flow, card)]
            self.next_check = now + JOB_GAP
            checks = self.index.read_many([card.item for card in cards])
            for card in cards:
                evidence = checks.get(card.item.id, {})
                if card._checks != evidence:
                    card.refresh_evidence(evidence)
                best = card.item.best_file()
                files = sorted(card.item.files, key=lambda f: f is not best)
                for file in files:
                    key = self._key(card.item, file)
                    if file.index in evidence or self.retry.get(key, 0) > now:
                        continue
                    self._start(flow, card, file, key, now)
                    return

    def _refresh(self, item_id):
        for flow in list(self.flows):
            for card in flow.cards():
                if card.item.id == item_id and card._scan_alive and not card.departing:
                    card.refresh_evidence(self.index.read_many([card.item]).get(item_id, {}))

    def _start(self, flow, card, file, key, now):
        data = asdict(card.item)
        data.update(summary="", previews=[], files=[asdict(file)])
        payload = json.dumps({"item": data}).encode()
        # Input uses a file descriptor, so GTK cannot block on a child reading a pipe.
        if len(payload) > 49152:
            self.retry[key] = now + 300
            return
        env = os.environ.copy()
        root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        try:
            with tempfile.TemporaryFile() as source:
                source.write(payload)
                source.seek(0)
                process = self.spawn(
                    [
                        sys.executable,
                        "-m",
                        "drape.compatibility_worker",
                        "--inspect-json",
                        "--memory-mib",
                        "384",
                        "--request-interval",
                        "3",
                        "--database",
                        str(self.index.path),
                    ],
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
        except OSError:
            self.retry[key] = now + 300
            return
        self.active = flow, card, file, key, process, now
