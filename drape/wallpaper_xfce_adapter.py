"""Build and supervise Xfdesktop's reversible background-only adapter.

The adapter is scoped to one xfdesktop process via its environment. No menu,
autostart, system library or Xfconf file is modified. A guardian restores the
standard desktop when the player exits, including an unexpected disconnect.
"""

import hashlib
import os
import platform
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .video_wallpapers import VideoError, runtime_dir
from .wallpaper_background import Mirror

SOURCE = Path(__file__).parent / "native/xfdesktop_background.c"
PROC = Path("/proc")


def build():
    digest = hashlib.sha256(SOURCE.read_bytes() + platform.machine().encode()).hexdigest()[:20]
    target = runtime_dir() / f"xfdesktop-background-{digest}.so"
    if target.is_file():
        return target
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        raise VideoError(
            "Xfce’s desktop adapter needs a C compiler. Install gcc or clang, then retry."
        )
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    try:
        result = subprocess.run(
            [
                compiler,
                "-shared",
                "-fPIC",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(SOURCE),
                "-o",
                str(temporary),
                "-ldl",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise VideoError("Could not build Xfce’s desktop adapter:\n" + result.stdout[-3000:])
        temporary.replace(target)
        return target
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoError(f"Could not build Xfce’s desktop adapter: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


class Guardian(Mirror):
    def __init__(self):
        module = self.module = build()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "drape.wallpaper_xfce_adapter", "--guard", str(module)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.buffer, self.prepared = b"", False
        deadline = time.monotonic() + 7
        try:
            while not self.ready():
                if time.monotonic() > deadline:
                    raise VideoError(
                        "Xfce’s desktop adapter did not become ready. The standard desktop is being restored."
                    )
                time.sleep(0.05)
        except Exception:
            self.close()
            raise

    def close(self):
        disconnected = self.process.poll() is not None
        super().close()
        if disconnected:
            # A killed guardian cannot run its finally block. Recover through a
            # separate X11 process so GTK's global X error handler stays intact.
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "drape.wallpaper_xfce_adapter",
                    "--restore",
                    str(self.module),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )


def _display_key(value):
    # Remove only the screen suffix, preserving dotted remote host names.
    host, separator, number = value.rpartition(":")
    return host + separator + number.split(".")[0]


def _display_desktops():
    """Match only this user's xfdesktop instances on this display."""
    display = _display_key(os.environ.get("DISPLAY", ""))
    found = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (
                entry.stat().st_uid != os.getuid()
                or (entry / "comm").read_text().strip() != "xfdesktop"
            ):
                continue
            environment = (entry / "environ").read_bytes().split(b"\0")
            active = next(
                (record[8:].decode() for record in environment if record.startswith(b"DISPLAY=")),
                "",
            )
            if _display_key(active) == display:
                found.append(int(entry.name))
        except (OSError, UnicodeError):
            continue
    return found


def guard(module):
    import json

    from .wallpaper_x11 import X11
    from .wallpaper_xfce_x11 import ADAPTER

    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    desktop = x11 = None
    replacing = False
    normal = dict(os.environ)
    try:
        executable = shutil.which("xfdesktop")
        if not executable:
            raise VideoError("Xfdesktop is not installed.")
        # Validate/build before asking the existing desktop to exit. Its normal
        # quit saves icon positions; Thunar windows and the panel are separate.
        x11 = X11()
        if not _display_desktops():
            raise VideoError("Xfdesktop is not running on this display.")
        replacing = True
        subprocess.run(
            [executable, "--quit"],
            env=normal,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=3,
        )
        deadline = time.monotonic() + 3
        while _display_desktops():
            if time.monotonic() > deadline:
                replacing = False  # An existing desktop remains; never kill it.
                raise VideoError("Xfdesktop did not exit. Its existing desktop was left alone.")
            time.sleep(0.05)
        adapted = dict(normal)
        adapted["LD_PRELOAD"] = str(module) + (
            ":" + normal["LD_PRELOAD"] if normal.get("LD_PRELOAD") else ""
        )
        # Keep the temporary adapter out of saved-session restart commands.
        adapted.pop("SESSION_MANAGER", None)
        with (runtime_dir() / "xfdesktop-adapter.log").open("ab") as log:
            desktop = subprocess.Popen(
                [executable, "--disable-wm-check", "--sm-client-disable"],
                env=adapted,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 5
        while x11.property(ADAPTER, 6) != desktop.pid:
            if stopped or desktop.poll() is not None or time.monotonic() > deadline:
                raise VideoError(
                    "Xfdesktop’s background adapter failed to start. Restoring the standard desktop."
                )
            time.sleep(0.05)
        print(json.dumps({"ready": True}), flush=True)
        while not stopped:
            readable, _, _ = select.select([sys.stdin.buffer], [], [], 0.2)
            if readable and not os.read(sys.stdin.fileno(), 4096):
                break
            if desktop.poll() is not None:
                raise VideoError("Xfdesktop was restarted. Live playback has stopped.")
    except Exception as exc:  # noqa: BLE001 - guardian boundary; restore in finally.
        print(json.dumps({"error": str(exc)}), flush=True)
    finally:
        if desktop is not None and desktop.poll() is None:
            desktop.terminate()
            try:
                desktop.wait(timeout=2)
            except subprocess.TimeoutExpired:
                desktop.kill()
                desktop.wait()
        if x11 is not None:
            try:
                if desktop is not None and x11.property(ADAPTER, 6) == desktop.pid:
                    x11.delete_property(ADAPTER)
                    x11.sync()
            except Exception as exc:  # noqa: BLE001 - restoration boundary.
                print(f"Could not clear the adapter marker: {exc}", file=sys.stderr)
            finally:
                x11.close()
        if replacing and not _display_desktops():
            with (runtime_dir() / "xfdesktop-adapter.log").open("ab") as log:
                subprocess.Popen(
                    [executable, "--disable-wm-check"],
                    env=normal,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )


def restore(module):
    """Recover an orphaned adapter, never quit an unrelated desktop process."""
    from .wallpaper_x11 import X11
    from .wallpaper_xfce_x11 import ADAPTER

    x11 = X11()
    try:
        pid = x11.property(ADAPTER, 6)
        if pid not in _display_desktops():
            return
        environment = (PROC / str(pid) / "environ").read_bytes().split(b"\0")
        preload = next(
            (entry[11:].decode() for entry in environment if entry.startswith(b"LD_PRELOAD=")), ""
        )
        if str(module) not in preload.split(":"):
            return
        executable = shutil.which("xfdesktop")
        subprocess.run(
            [executable, "--quit"],
            check=True,
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 2
        while pid in _display_desktops():
            if time.monotonic() > deadline:
                return
            time.sleep(0.05)
        if x11.property(ADAPTER, 6) == pid:
            x11.delete_property(ADAPTER)
            x11.sync()
        if not _display_desktops():
            with (runtime_dir() / "xfdesktop-adapter.log").open("ab") as log:
                subprocess.Popen(
                    [executable, "--disable-wm-check"],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
    finally:
        x11.close()


if __name__ == "__main__":
    if sys.argv[1] == "--restore":
        restore(Path(sys.argv[2]))
    else:
        guard(Path(sys.argv[2]))
