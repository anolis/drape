"""Local video library and bounded IPC to the live-wallpaper player."""

import fcntl
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .records import InstallError, _atomic_write, file_lock

PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "drape/videos.json"
EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".ogv"}
LIMIT = 64 * 1024


class VideoError(Exception):
    """A video cannot be saved, played or controlled."""


def supported():
    """XWayland is not enough: the wallpaper needs a verified desktop host."""
    from . import wallpaper_desktop

    return wallpaper_desktop.current() is not None


def validate_video(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise VideoError("Choose a local video file.")
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() not in EXTENSIONS or not path.is_file():
        raise VideoError("Choose an existing MP4, WebM, MKV, MOV, M4V, AVI or OGV video.")
    return str(path)


# Library references are small; the user's videos are never copied or deleted.


def library():
    try:
        with PATH.open(encoding="utf-8") as stream:
            data = json.loads(stream.read(LIMIT + 1))
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or data.get("fit") not in {"fill", "fit"}
            or not isinstance(data.get("videos"), list)
            or len(data["videos"]) > 200
            or any(not isinstance(p, str) or not Path(p).is_absolute() for p in data["videos"])
            or not isinstance(data.get("selected"), str)
        ):
            raise ValueError("invalid video library")
        return data
    except FileNotFoundError:
        return {"version": 1, "videos": [], "selected": "", "fit": "fill"}
    except (OSError, ValueError, TypeError) as exc:
        raise VideoError(f"Cannot read the video library at {PATH}: {exc}") from exc


def _edit(change):
    try:
        with file_lock(PATH.with_suffix(".lock")):
            data = library()
            change(data)
            text = json.dumps(data, indent=2)
            if len(text.encode()) > LIMIT:
                raise VideoError(
                    "The video library is full. Remove an entry before adding another."
                )
            _atomic_write(PATH, text)
            return data
    except (OSError, InstallError) as exc:
        raise VideoError(f"Could not save the video library: {exc}") from exc


def remember(path, fit="fill"):
    path = validate_video(path)
    if fit not in {"fill", "fit"}:
        raise VideoError("Unknown video layout.")

    def change(data):
        if path not in data["videos"]:
            if len(data["videos"]) >= 200:
                raise VideoError("The video library is full. Remove an entry first.")
            data["videos"].append(path)
        data.update(selected=path, fit=fit)

    return _edit(change)


def forget(path):
    def change(data):
        data["videos"] = [p for p in data["videos"] if p != path]
        if data["selected"] == path:
            data["selected"] = data["videos"][0] if data["videos"] else ""

    return _edit(change)


# Use a private runtime directory, not PIDs or global process-name termination.


def runtime_dir(create=True):
    base = os.environ.get("XDG_RUNTIME_DIR")
    path = (
        Path(base) / "drape-video"
        if base
        else Path(tempfile.gettempdir()) / f"drape-video-{os.getuid()}"
    )
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    elif not path.exists():
        return path
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise VideoError(f"The video player runtime directory is not private: {path}")
    return path


def request(action, **values):
    try:
        socket_path = runtime_dir(create=False) / "control.sock"
        if not socket_path.exists() and action in {"status", "stop", "quit"}:
            return {"state": "stopped", "path": "", "fit": "fill", "available": False}
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(12)
            connection.connect(str(socket_path))
            connection.sendall(json.dumps(dict(action=action, **values)).encode() + b"\n")
            with connection.makefile("rb") as stream:
                raw = stream.readline(LIMIT + 1)
            if len(raw) > LIMIT:
                raise VideoError("Video player response is too large.")
            reply = json.loads(raw)
            if not isinstance(reply, dict) or not isinstance(reply.get("state", ""), str):
                raise VideoError("Invalid response from the video player.")
        if reply.get("error"):
            raise VideoError(reply["error"])
        return reply
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
        if action in {"status", "stop", "quit"}:
            return {"state": "stopped", "path": "", "fit": "fill", "available": False}
        raise VideoError("The video wallpaper player is not running.") from None
    except (OSError, ValueError) as exc:
        raise VideoError(f"Cannot contact the video wallpaper player: {exc}") from exc


def _ensure_worker(source):
    from . import wallpaper_desktop

    state = request("status")
    host = wallpaper_desktop.current()
    mate_upgrade = (
        isinstance(host, wallpaper_desktop.MateX11)
        and state.get("available")
        and (state.get("mate_background") != 1 or state.get("desktop_host") != "mate")
    )
    if (
        mate_upgrade
        or state.get("stopping")
        or (state.get("available") and source != "video" and source not in state.get("sources", []))
        or (state.get("available") and source == "audio" and state.get("audio_visuals") != 1)
    ):
        # A player from an older checkout cannot render the new source. Stop it
        # through its private socket before launching the updated worker.
        if not state.get("stopping"):
            request("quit")
        deadline = time.monotonic() + 8
        # Older workers marshal status through GTK even after the main loop
        # exits. Poll the socket's removal instead of sending late requests.
        socket_path = runtime_dir(create=False) / "control.sock"
        while socket_path.exists():
            if time.monotonic() > deadline:
                raise VideoError("The previous wallpaper player is still shutting down. Try again.")
            time.sleep(0.05)
        state = {"available": False}
    if not state.get("available"):
        _wait_for_worker_exit()
        # The worker has its own singleton lock. Two Drape launches cannot create
        # competing players, and closing the browsing window leaves playback alive.
        root = Path(__file__).resolve().parent.parent
        with (runtime_dir() / "player.log").open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "drape.video_player"],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        threading.Thread(target=process.wait, daemon=True).start()
        deadline = time.monotonic() + 8
        while not request("status").get("available"):
            if process.poll() not in (None, 0) or time.monotonic() > deadline:
                raise VideoError(
                    f"Video player could not start. See {runtime_dir() / 'player.log'}."
                )
            time.sleep(0.05)


def _wait_for_worker_exit():
    """A closed socket can precede release of the worker's singleton lock."""
    path = runtime_dir(create=False) / "player.lock"
    if not path.exists():
        return
    deadline = time.monotonic() + 8
    with path.open("a+b") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise VideoError(
                        "The previous wallpaper player is still shutting down. Try again."
                    ) from None
                time.sleep(0.05)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
                return


def selection(path, fit="fill", source="video", **options):
    """Validate on both sides of IPC; only known source types may launch children."""
    from . import wallpaper_sources as sources

    if not isinstance(source, str) or source not in sources.SOURCES:
        raise VideoError("Unknown wallpaper playback source.")
    allowed = {
        "xscreensaver": {"fps", "settings"},
        "audio": {"desktop_audio", "microphone", *sources.COLOR_OPTIONS},
    }.get(source, set())
    if options.keys() - allowed:
        raise VideoError("Unknown wallpaper playback option.")
    if fit not in {"fill", "fit"}:
        raise VideoError("Unknown video layout.")
    values = sources.validate({**sources.DEFAULTS, "source": source, **options})
    if source == "video":
        path = validate_video(path)
    elif source == "xscreensaver":
        from . import wallpaper_animation_settings as animation_settings
        from . import wallpaper_xscreensaver as saver

        entry = saver.validate(path)
        values["animation"] = path
        values["settings"] = animation_settings.validate(
            entry.get("settings", []), options.get("settings", {})
        )
    else:
        if not isinstance(path, str) or path not in sources.STYLES:
            raise VideoError("Choose an audio visualization style.")
        if not (values["desktop_audio"] or values["microphone"]):
            raise VideoError("Enable Desktop audio, Microphone, or both.")
        values["style"] = path
    return path, values


def play(path, fit="fill", source="video", **options):
    from . import wallpaper_sources as sources

    if not supported():
        raise VideoError("Live wallpapers currently require Cinnamon, GNOME or MATE on X11.")
    path, values = selection(path, fit, source, **options)
    if source == "video":
        if not shutil.which("mpv"):
            raise VideoError(
                "Install mpv with your system's package manager to play video wallpapers."
            )
        remember(path, fit)
    elif source == "audio":
        from . import wallpaper_audio as audio

        if not audio.available():
            raise VideoError("Install cava and pactl to play audio visualizations.")
    # A failed save must not leave playback alive without its tray controls.
    preferences = {"source": source}
    if source == "xscreensaver":
        preferences.update(animation=path, fps=values["fps"])
    elif source == "audio":
        preferences.update(
            style=path,
            **{key: values[key] for key in ("desktop_audio", "microphone", *sources.COLOR_OPTIONS)},
        )
    sources.remember(**preferences)
    _ensure_worker(source)
    return request("play", path=path, fit=fit, source=source, options=options)


def stop():
    """Removing the video window reveals the untouched static wallpaper."""
    return request("stop")
