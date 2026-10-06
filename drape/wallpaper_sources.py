"""Saved playback-source choices, independent of the local video library."""

import json
import os
from pathlib import Path

from .records import InstallError, _atomic_write, file_lock
from .video_wallpapers import VideoError

PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "drape/wallpaper-sources.json"
)
SOURCES = {"video", "xscreensaver", "audio"}
STYLES = {
    "bars": "Spectrum bars",
    "curve": "Flowing curve",
    "rings": "Radial rings",
    "blocks": "Spectrum blocks",
    "mirror": "Mirrored spectrum",
    "wave_ring": "Circular wave",
    "ribbons": "Layered ribbons",
    "particles": "Orbiting lights",
    "spiral": "Spectrum spiral",
}
COLOR_MODES = {
    "single": "Single color",
    "gradient": "Two-color gradient",
    "rainbow": "Rainbow",
    "aurora": "Aurora",
    "sunset": "Sunset",
    "fire": "Fire & gold",
    "ocean": "Ocean",
}
COLOR_SPEEDS = {"slow": "Slow", "normal": "Normal", "fast": "Fast"}
COLOR_OPTIONS = ("color", "color2", "color_mode", "cycle_colors", "color_speed")
DEFAULTS = {
    "version": 1,
    "source": "video",
    "animation": "",
    "fps": 30,
    "style": "curve",
    "desktop_audio": True,
    "microphone": False,
    "color": "#65d6ce",
    "color2": "#b477ff",
    "color_mode": "single",
    "cycle_colors": False,
    "color_speed": "normal",
}


def validate(data):
    import re

    if not isinstance(data, dict):
        raise VideoError("Invalid live-wallpaper preferences.")
    # Existing version-1 files predate palette settings. Fill only the new keys;
    # missing original fields still indicate a damaged preferences file.
    data = {**{key: DEFAULTS[key] for key in COLOR_OPTIONS if key != "color"}, **data}
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("source"), str)
        or data.get("source") not in SOURCES
        or not isinstance(data.get("animation"), str)
        or len(data["animation"]) > 80
        or type(data.get("fps")) is not int
        or data["fps"] not in {15, 30, 60}
        or not isinstance(data.get("style"), str)
        or data.get("style") not in STYLES
        or type(data.get("desktop_audio")) is not bool
        or type(data.get("microphone")) is not bool
        or not isinstance(data.get("color"), str)
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", data["color"])
        or not isinstance(data.get("color2"), str)
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", data["color2"])
        or not isinstance(data.get("color_mode"), str)
        or data["color_mode"] not in COLOR_MODES
        or type(data.get("cycle_colors")) is not bool
        or not isinstance(data.get("color_speed"), str)
        or data["color_speed"] not in COLOR_SPEEDS
    ):
        raise VideoError("Invalid live-wallpaper preferences.")
    return {key: data[key] for key in DEFAULTS}


def preferences():
    try:
        with PATH.open(encoding="utf-8") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise VideoError("Live-wallpaper preferences are too large.")
        return validate(json.loads(raw))
    except FileNotFoundError:
        return DEFAULTS.copy()
    except (OSError, ValueError, TypeError) as exc:
        raise VideoError(f"Cannot read live-wallpaper preferences: {exc}") from exc


def remember(**values):
    try:
        with file_lock(PATH.with_suffix(".lock")):
            data = preferences()
            data.update(values)
            data = validate(data)
            _atomic_write(PATH, json.dumps(data, indent=2))
            return data
    except (OSError, InstallError) as exc:
        raise VideoError(f"Cannot save live-wallpaper preferences: {exc}") from exc
