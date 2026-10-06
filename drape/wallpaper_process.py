"""Bound diagnostics and terminate only child processes owned by the wallpaper worker."""

import os
import signal
import subprocess


def limit_log(path):
    try:
        if path.stat().st_size > 256 * 1024:
            with path.open("r+b") as stream:
                stream.seek(-64 * 1024, os.SEEK_END)
                tail = stream.read(64 * 1024)
                stream.seek(0)
                stream.write(tail)
                stream.truncate()
    except OSError:
        pass  # Diagnostics must not interrupt healthy playback.


def signal_group(process, value):
    try:
        os.killpg(process.pid, value)
    except ProcessLookupError:
        pass


def stop(process, group=False):
    if process is None:
        return
    if group:
        # Stopped animations need CONT to handle TERM; include helper children.
        signal_group(process, signal.SIGCONT)
        signal_group(process, signal.SIGTERM)
    elif process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if group:
            signal_group(process, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=2)
    if group:
        signal_group(process, signal.SIGKILL)
