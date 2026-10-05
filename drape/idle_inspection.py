"""One cancellable, resource-bounded inspection process for the current viewport."""

import json
import math
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
JOB_GAP = 15
JOB_TIMEOUT = 90


class IdleInspector:
    """GTK owns the timer/signals; this controller never opens theme archives."""

    def __init__(self, enabled=True, clock=time.monotonic, spawn=subprocess.Popen, index=None):
        self.enabled = enabled
        self.clock, self.spawn = clock, spawn
        self.index = index or compatibility.Index()
        self.flows = weakref.WeakKeyDictionary()
        self.active = None
        self.local_manifest = None
        self.local_process = None
        self.local_progress = None
        self.local_counts = (0, 0)
        self.local_reused = 0
        self.local_scanned = 0
        self.local_timed_out = False
        self.cooldown = 0
        self.notice_until = 0
        self.on_status = None
        self.status = {
            "state": "idle",
            "text": "Compatibility scanner ready",
            "spinning": False,
            "fraction": None,
        }
        self.retired = []
        self.next_check = 0
        self.retry = {}
        self.attempts = {}
        self.last_attempt = {}

    def _status(self, state, text, spinning=False, fraction=None):
        status = dict(state=state, text=text, spinning=spinning, fraction=fraction)
        if status != self.status:
            self.status = status
            if self.on_status:
                self.on_status(status)

    def _read_local_progress(self):
        title = ""
        if self.local_progress:
            try:
                path = Path(self.local_progress.name) / "progress.json"
                with path.open() as stream:
                    data = json.loads(stream.read(4096))
                done, total = data["done"], data["total"]
                if isinstance(done, int) and isinstance(total, int) and 0 <= done <= total:
                    self.local_counts = done, total
                    title = data.get("title", "")
                    self.local_reused = data.get("reused", 0)
                    self.local_scanned = data.get("scanned", 0)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return title

    def _clean_local_progress(self):
        if self.local_progress:
            self.local_progress.cleanup()
            self.local_progress = None

    @staticmethod
    def _card_busy(card, busy):
        if hasattr(card, "set_scan_busy"):
            card.set_scan_busy(busy)

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
            if not self.local_process:
                self._status(
                    "disabled",
                    "Online compatibility checks disabled — cached evidence is still used",
                )

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
        _, card, _, key, process, _ = self.active
        self.active = None
        self._card_busy(card, False)
        self.attempts[card.item.id] = max(0, self.attempts.get(card.item.id, 0) - 1)
        self.notice_until = self.clock() + 2
        self._status("cancelled", "Check cancelled — theme left the active view")
        self.retry[key] = self.clock() + JOB_GAP
        self.next_check = max(self.next_check, self.clock() + JOB_GAP)
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        self.retired.append((process, self.clock()))

    def close(self):
        if self.local_process:
            process, _ = self.local_process
            if process.poll() is None:
                process.kill()
            process.wait()
            self.local_process = None
        self._clean_local_progress()
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
        # Local files take priority and do not depend on viewport idleness or network settings.
        if self.local_manifest is not None:
            manifest, self.local_manifest = self.local_manifest, None
            self.local_progress = tempfile.TemporaryDirectory(prefix="drape-scan-")
            self._status("local", "Preparing saved-theme compatibility checks…", True, 0)
            try:
                process = self.spawn(
                    [
                        sys.executable,
                        "-m",
                        "drape.compatibility_worker",
                        "--installed-local",
                        str(manifest),
                        "--memory-mib",
                        "384",
                        "--database",
                        str(self.index.path),
                        "--progress-file",
                        str(Path(self.local_progress.name) / "progress.json"),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=self._environment(),
                )
                self.local_process = process, now
            except OSError:
                self._clean_local_progress()
                self.notice_until = now + 8
                self._status(
                    "failed",
                    "Could not start saved-theme checks — existing cache is still available",
                )
        if self.local_process:
            process, started = self.local_process
            title = self._read_local_progress()
            done, total = self.local_counts
            fraction = done / total if total else 0
            if process.poll() is not None:
                self.local_process = None
                self._clean_local_progress()
                self.notice_until = now + 8
                if process.returncode == 0 and not self.local_timed_out:
                    self._status(
                        "complete",
                        f"Theme cache ready — {done} themes · {self.local_reused} reused · {self.local_scanned} inspected",
                        False,
                        1,
                    )
                else:
                    self._status(
                        "failed",
                        f"Saved-theme checks stopped — {done} of {total} cached; remaining checks pending",
                        False,
                        fraction,
                    )

                for flow in list(self.flows):
                    for card in flow.cards():
                        if card._scan_alive and not card.departing:
                            card.refresh_evidence(
                                self.index.read_many([card.item]).get(card.item.id, {})
                            )
            elif now - started > JOB_TIMEOUT:
                self.local_timed_out = True
                process.kill()
                self._status(
                    "failed",
                    f"Saved-theme checks timed out — {done} of {total} cached",
                    False,
                    fraction,
                )
                return
            else:
                text = f"Updating theme cache — {done} of {total} ready"
                if title:
                    text += f" · {title}"
                self._status("local", text, True, min(fraction, 0.99))
                return
        if self.active:
            flow, card, file, key, process, started = self.active
            if not self.enabled or not self._visible(flow, card):
                self.cancel()
            elif now - started > JOB_TIMEOUT:
                self.cancel()
                self.retry[key] = now + 300
                self._status("failed", "Compatibility check timed out — will retry later")
                self.notice_until = now + 5
            elif process.poll() is not None:
                self.active = None
                self._card_busy(card, False)
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
                    self._status("cached", f"Compatibility cached — {card.item.name}")
                    self.notice_until = now + 2
                elif status == "rate_limited":
                    self.attempts[card.item.id] = max(0, self.attempts.get(card.item.id, 0) - 1)
                    delay = min(http.MAX_RETRY_AFTER, max(1, result.get("retry_after", 60)))
                    self.next_check = now + delay
                    self.cooldown = now + delay
                    self._status(
                        "rate_limited", f"Server rate limit — next check in {math.ceil(delay)}s"
                    )
                    card.compatibility_note.set_text("Check delayed: server rate limit")
                    card.compatibility_note.show()
                else:
                    self.retry[key] = now + 300
                    self._status("failed", f"Could not inspect {card.item.name} — will retry later")
                    self.notice_until = now + 5
        if self.cooldown > now:
            self._status(
                "rate_limited",
                f"Server rate limit — next check in {math.ceil(self.cooldown - now)}s",
            )
        elif not self.enabled and not self.local_process and now >= self.notice_until:
            self._status(
                "disabled", "Online compatibility checks disabled — cached evidence is still used"
            )
        if self.active or self.retired or not self.enabled or now < self.next_check:
            return
        if now >= self.notice_until:
            self._status("idle", "No compatibility check running")
        # Reading SQLite is cheap. No download is needed for cached cards, even after relaunch.
        for flow, moved in list(self.flows.items()):
            if not flow.get_mapped():
                continue
            if not flow.get_toplevel().is_active():
                if now >= self.notice_until:
                    self._status(
                        "paused",
                        "Compatibility checks paused — activate Drape to inspect visible downloads",
                    )
                continue
            if now - moved < IDLE_SECONDS:
                if now >= self.notice_until:
                    self._status("waiting", "Compatibility checks resume when scrolling stops")
                continue
            cards = [card for card in flow.cards() if self._visible(flow, card)]
            self.next_check = now + JOB_GAP
            checks = self.index.read_many([card.item for card in cards])
            known_cards = sum(
                any(result[0] for result in checks.get(card.item.id, {}).values()) for card in cards
            )
            if cards and now >= self.notice_until:
                unknown = len(cards) - known_cards
                self._status(
                    "cached" if not unknown else "waiting",
                    f"Visible themes — {known_cards} of {len(cards)} have cached contents"
                    + (f" · {unknown} unverified" if unknown else ""),
                )
            cards.sort(key=lambda card: self.last_attempt.get(card.item.id, -1))
            for card in cards:
                evidence = checks.get(card.item.id, {})
                if card._checks != evidence:
                    card.refresh_evidence(evidence)
                # Browsing only needs useful contents, not every distro/color variant.
                # Exhaustive archive scans belong to the explicit backend worker.
                if any(result[0] for result in evidence.values()):
                    continue
                if self.attempts.get(card.item.id, 0) >= 3:
                    continue
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

    @staticmethod
    def _environment():
        env = os.environ.copy()
        root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _start(self, flow, card, file, key, now):
        data = asdict(card.item)
        data.update(summary="", previews=[], files=[asdict(file)])
        payload = json.dumps({"item": data}).encode()
        # Input uses a file descriptor, so GTK cannot block on a child reading a pipe.
        if len(payload) > 49152:
            self.retry[key] = now + 300
            return
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
                        "10",
                        "--database",
                        str(self.index.path),
                    ],
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=self._environment(),
                )
        except OSError:
            self.retry[key] = now + 300
            self.notice_until = now + 5
            self._status("failed", "Could not start compatibility check — will retry later")
            return
        self.attempts[card.item.id] = self.attempts.get(card.item.id, 0) + 1
        self.last_attempt[card.item.id] = now
        self.active = flow, card, file, key, process, now
        self._card_busy(card, True)
        self._status("scanning", f"Checking compatibility — {card.item.name} · {file.name}", True)
