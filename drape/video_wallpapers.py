"""Local video library and bounded IPC to the Cinnamon wallpaper player."""

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

from . import desktop
from .records import InstallError, _atomic_write, file_lock

PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "drape/videos.json"
EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".ogv"}
LIMIT = 64 * 1024


class VideoError(Exception):
    """A video cannot be saved, played or controlled."""


def supported():
    """XWayland is not enough: the wallpaper needs Cinnamon's X11 desktop."""
    return (
        desktop.current_desktop() == "cinnamon"
        and os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"
        and not os.environ.get("WAYLAND_DISPLAY")
        and bool(os.environ.get("DISPLAY"))
    )


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
    except (FileNotFoundError, ConnectionRefusedError):
        if action in {"status", "stop", "quit"}:
            return {"state": "stopped", "path": "", "fit": "fill", "available": False}
        raise VideoError("The video wallpaper player is not running.") from None
    except (OSError, ValueError) as exc:
        raise VideoError(f"Cannot contact the video wallpaper player: {exc}") from exc


def play(path, fit="fill"):
    if not supported():
        raise VideoError("Video wallpapers currently require Cinnamon on X11.")
    if not shutil.which("mpv"):
        raise VideoError("Install mpv with your system's package manager to play video wallpapers.")
    path = validate_video(path)
    if fit not in {"fill", "fit"}:
        raise VideoError("Unknown video layout.")
    # A failed save must not leave playback alive without its tray controls.
    remember(path, fit)
    if not request("status").get("available"):
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
            if process.poll() is not None or time.monotonic() > deadline:
                raise VideoError(
                    f"Video player could not start. See {runtime_dir() / 'player.log'}."
                )
            time.sleep(0.05)
    result = request("play", path=path, fit=fit)
    return result


def stop():
    """Removing the video window reveals the untouched static wallpaper."""
    return request("stop")
