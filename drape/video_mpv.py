"""Isolated mpv command construction and its local JSON control protocol."""

import json
import socket

from .video_wallpapers import LIMIT, VideoError


def command(path, xid, ipc_path, fit):
    return [
        "mpv",
        "--no-config",
        "--load-scripts=no",
        "--access-references=no",
        "--demuxer=lavf",
        "--demuxer-lavf-o=protocol_whitelist=file",
        "--no-audio",
        "--sid=no",
        "--loop-file=inf",
        "--no-osc",
        "--osd-level=0",
        "--input-default-bindings=no",
        "--input-vo-keyboard=no",
        "--input-cursor=no",
        "--input-terminal=no",
        "--stop-screensaver=no",
        "--x11-bypass-compositor=never",
        "--hwdec=auto-safe",
        "--vo=gpu,xv,x11",
        "--panscan=" + ("1" if fit == "fill" else "0"),
        f"--wid={xid}",
        f"--input-ipc-server={ipc_path}",
        "--really-quiet",
        "--",
        path,
    ]


def send(path, args):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(0.3)
        connection.connect(str(path))
        connection.sendall(json.dumps({"command": args, "request_id": 1}).encode() + b"\n")
        with connection.makefile("rb") as stream:
            # mpv can send asynchronous events before the response.
            for _ in range(30):
                raw = stream.readline(LIMIT + 1)
                if len(raw) > LIMIT or not raw:
                    raise VideoError("Invalid response from mpv.")
                response = json.loads(raw)
                if response.get("request_id") == 1:
                    if response.get("error") != "success":
                        raise VideoError(response.get("error", "mpv command failed"))
                    return response.get("data")
    raise VideoError("mpv did not answer the command.")
