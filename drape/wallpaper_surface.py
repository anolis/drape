"""A hosted wallpaper surface; desktop ordering and rendering remain separate."""

import signal
import subprocess
import time

import cairo

from . import (
    video_mpv,
    wallpaper_audio,
    wallpaper_desktop,
    wallpaper_process,
    wallpaper_sources,
    wallpaper_xscreensaver,
)
from . import video_wallpapers as videos
from .ui.gtk import Gtk


class Surface:
    def __init__(
        self, monitor, path, fit, number, profile="gpu", source="video", options=None, audio=None
    ):
        options = options or wallpaper_sources.DEFAULTS
        self.source = source
        self.started = time.monotonic()
        self.ipc = videos.runtime_dir() / f"mpv-{number}.sock"
        self.log = (
            videos.runtime_dir()
            / f"{('xscreensaver' if source == 'xscreensaver' else 'mpv')}-{number}.log"
        )
        self.ipc.unlink(missing_ok=True)
        self.process = None
        host = wallpaper_desktop.current()
        if host is None:
            raise videos.VideoError("No supported desktop host is available.")
        self.window = host.window(monitor)
        area = Gtk.DrawingArea()
        self.area = area
        if source == "audio":
            area.connect(
                "draw",
                lambda widget, cr: wallpaper_audio.draw(
                    cr,
                    widget.get_allocated_width(),
                    widget.get_allocated_height(),
                    audio.values,
                    path,
                    options["color"],
                    color2=options["color2"],
                    color_mode=options["color_mode"],
                    cycle_colors=options["cycle_colors"],
                    color_speed=options["color_speed"],
                    elapsed=audio.elapsed,
                ),
            )
        self.window.add(area)
        self.window.show_all()
        # Keep desktop clicks and drags available to Nemo, including when an mpv
        # child covers the entire surface. The panel and icons stay above us.
        self.window.get_window().input_shape_combine_region(cairo.Region(), 0, 0)
        self.window.get_window().lower()
        if source == "audio":
            return  # All monitors share one capture session, owned by Player.
        try:
            if self.log.exists():
                self.log.replace(self.log.with_suffix(".previous.log"))
            # Append lets the periodic cap truncate the file without leaving
            # sparse holes at the child's previous write position.
            with self.log.open("ab") as log:
                args = (
                    wallpaper_xscreensaver.command(
                        wallpaper_xscreensaver.validate(path),
                        area.get_window().get_xid(),
                        options["fps"],
                        options.get("settings", {}),
                    )
                    if source == "xscreensaver"
                    else video_mpv.command(
                        path, area.get_window().get_xid(), self.ipc, fit, profile
                    )
                )
                self.process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=source == "xscreensaver",
                )
        except (OSError, videos.VideoError):
            self.close()
            raise

    def limit_log(self):
        """Keep warnings bounded even if a driver emits errors every frame."""
        wallpaper_process.limit_log(self.log)

    def pause_animation(self, value):
        wallpaper_process.signal_group(self.process, signal.SIGSTOP if value else signal.SIGCONT)

    def close(self):
        wallpaper_process.stop(self.process, group=self.source == "xscreensaver")
        self.process = None
        self.window.destroy()
        self.ipc.unlink(missing_ok=True)
