"""CAVA spectrum capture and original Cairo visualizations for live wallpapers.

One capture per enabled input serves every monitor. Audio samples stay in the
sound server/CAVA; Drape receives only bounded, unsigned spectrum amplitudes.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time

from . import wallpaper_process as processes
from .video_wallpapers import VideoError, runtime_dir
from .wallpaper_visualizers import draw  # noqa: F401 - retained public rendering entry point

BARS = 64


def available():
    return bool(shutil.which("cava") and shutil.which("pactl"))


def _pactl(*args):
    try:
        result = subprocess.run(
            ["pactl", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise VideoError(
                "Cannot read audio devices. Check that PulseAudio or PipeWire's PulseAudio service is running."
            )
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoError(f"Cannot read audio devices: {exc}") from exc


def inputs(desktop_audio, microphone):
    if (
        type(desktop_audio) is not bool
        or type(microphone) is not bool
        or not (desktop_audio or microphone)
    ):
        raise VideoError("Enable Desktop audio, Microphone, or both.")
    if not available():
        raise VideoError(
            "Install cava and pactl (pulseaudio-utils on Debian/Ubuntu) to enable audio visualizations."
        )
    selected = {}
    if desktop_audio:
        # 'auto' can select a different input depending on CAVA's build. Always
        # resolve the output monitor explicitly so desktop-only cannot use a mic.
        sink = _pactl("get-default-sink")
        selected["desktop"] = sink + ".monitor"
    if microphone:
        selected["microphone"] = _pactl("get-default-source")
    try:
        sources = json.loads(_pactl("--format=json", "list", "sources"))
        if not isinstance(sources, list):
            raise TypeError("invalid device list")
        by_name = {
            entry["name"]: entry
            for entry in sources
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }
        for kind, name in selected.items():
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", name) or name not in by_name:
                raise VideoError(
                    f"The default {kind} audio source is unavailable. Select a device in your system's Sound settings."
                )
            monitor = by_name[name].get("monitor_of_sink")
            is_monitor = name.endswith(".monitor") or monitor not in (None, "", -1, 4294967295)
            if kind == "microphone" and is_monitor:
                raise VideoError(
                    "The default input is an output monitor, not a microphone. Choose a microphone in Sound settings."
                )
    except (ValueError, TypeError, KeyError) as exc:
        raise VideoError(f"Cannot read the audio-source list: {exc}") from exc
    return selected


def config(source):
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", source):
        raise VideoError("Invalid audio source.")
    return (
        f"[general]\nbars = {BARS}\nframerate = 30\n"
        f"[input]\nmethod = pulse\nsource = {source}\n"
        "[output]\nmethod = raw\nraw_target = /dev/stdout\n"
        "data_format = binary\nbit_format = 8bit\nchannels = mono\n"
        "[smoothing]\nnoise_reduction = 77\n"
    )


class Frames:
    """Retain one latest complete frame and at most one partial frame."""

    def __init__(self):
        self.pending = bytearray()
        self.values = [0.0] * BARS
        self.received = False

    def push(self, raw):
        self.pending.extend(raw)
        complete = len(self.pending) // BARS * BARS
        if complete:
            self.values = [value / 255 for value in self.pending[complete - BARS : complete]]
            del self.pending[:complete]
            self.received = True


class Feed:
    def __init__(self, kind, source):
        self.process = None
        self.frames = Frames()
        self.log = runtime_dir() / f"cava-{kind}.log"
        self.path = runtime_dir() / f"cava-{kind}.ini"
        try:
            self.path.write_text(config(source))
            self.path.chmod(0o600)
            if self.log.exists():
                self.log.replace(self.log.with_suffix(".previous.log"))
            with self.log.open("ab") as log:
                self.process = subprocess.Popen(
                    ["cava", "-p", str(self.path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    start_new_session=True,
                )
            os.set_blocking(self.process.stdout.fileno(), False)
        except OSError:
            self.close()
            raise

    def advance(self):
        processes.limit_log(self.log)
        if self.process.poll() is not None:
            raise VideoError(f"Audio capture stopped. See {self.log} for CAVA diagnostics.")
        # Never block GTK, and never accumulate frames while the desktop is busy.
        for _ in range(8):
            try:
                raw = os.read(self.process.stdout.fileno(), 8192)
            except BlockingIOError:
                break
            if not raw:
                break
            self.frames.push(raw)

    def pause(self, value):
        processes.signal_group(self.process, signal.SIGSTOP if value else signal.SIGCONT)

    def close(self):
        processes.stop(self.process, group=True)
        if self.process is not None and self.process.stdout is not None:
            self.process.stdout.close()
        self.process = None
        self.path.unlink(missing_ok=True)


class Session:
    def __init__(self, desktop_audio, microphone):
        self.feeds = []
        self.values = [0.0] * BARS
        self.started = time.monotonic()
        self.elapsed, self.last_frame = 0.0, self.started
        self.paused = False
        try:
            for kind, source in inputs(desktop_audio, microphone).items():
                self.feeds.append(Feed(kind, source))
        except (OSError, VideoError):
            self.close()
            raise

    def advance(self):
        if self.paused:
            return
        now = time.monotonic()
        self.elapsed += min(0.25, max(0, now - self.last_frame))
        self.last_frame = now
        for feed in self.feeds:
            feed.advance()
        # Taking the strongest amplitude avoids clipping when both sources are
        # enabled, while retaining quiet sounds from either one.
        targets = [max(feed.frames.values[i] for feed in self.feeds) for i in range(BARS)]
        decayed = [max(target, old * 0.86) for target, old in zip(targets, self.values)]
        # Settle below half an 8-bit amplitude step so the last silent frame is
        # drawn and the renderer can stay idle rather than repaint tiny tails.
        self.values = [value if value >= 0.002 else 0.0 for value in decayed]

    def ready(self):
        return bool(self.feeds) and all(feed.frames.received for feed in self.feeds)

    def pause(self, value):
        for feed in self.feeds:
            feed.pause(value)
        self.paused = value
        self.last_frame = time.monotonic()

    def close(self):
        for feed in self.feeds:
            feed.close()
        self.feeds.clear()
