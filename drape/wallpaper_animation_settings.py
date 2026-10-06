"""Packaged XScreenSaver controls and Drape's independent per-animation settings."""

import json
import math
import os
import re
import shlex
from pathlib import Path

from .records import InstallError, _atomic_write, file_lock
from .video_wallpapers import VideoError

PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "drape/xscreensaver-settings.json"
)
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}")
FLAG = re.compile(r"--?[a-zA-Z][a-zA-Z0-9-]*")
RESERVED = {"root", "window", "window-id", "windowid", "display", "visual", "program"}


def _tokens(text, placeholder=False):
    """Read only a single option, never a command or a surface-target override."""
    args = shlex.split(text)
    if not args:
        return []
    if (
        not FLAG.fullmatch(args[0])
        or args[0].lstrip("-") in RESERVED
        or len(args) > 2
        or (placeholder and (len(args) != 2 or args[1] != "%"))
    ):
        raise ValueError("Unsupported animation option")
    return args


def schema(root):
    """Flatten the installed XML controls; delay remains the page's FPS control."""
    controls = {}
    for node in root.iter():
        key = node.get("id", "")
        if node.tag not in {"number", "boolean", "select", "string", "file"} or key == "delay":
            continue
        if not ID.fullmatch(key) or key in controls or len(controls) >= 80:
            continue
        control = {
            "id": key,
            "type": node.tag,
            "label": node.get("_label", node.get("label", key))[:120],
        }
        try:
            if node.tag == "number":
                low, high, default = (float(node.get(k, "nan")) for k in ("low", "high", "default"))
                if (
                    not all(math.isfinite(v) and abs(v) <= 1e9 for v in (low, high, default))
                    or low >= high
                ):
                    continue
                # Some upstream defaults use a negative sentinel for random behavior.
                control.update(
                    low=low,
                    high=high,
                    default=default,
                    step=float(node.get("step", "1")),
                    integer=all("." not in node.get(k, "") for k in ("low", "high", "default")),
                    args=_tokens(node.get("arg", ""), True),
                )
                if (
                    not control["args"]
                    or not math.isfinite(control["step"])
                    or control["step"] <= 0
                ):
                    continue
            elif node.tag == "boolean":
                yes, no = (_tokens(node.get(k, "")) for k in ("arg-set", "arg-unset"))
                if not (yes or no):
                    continue
                control.update(yes=yes, no=no, default=not bool(yes))
            elif node.tag == "select":
                options = []
                for option in node.findall("option")[:80]:
                    name = option.get("id", "")
                    if not ID.fullmatch(name):
                        raise ValueError("Invalid option")
                    options.append(
                        {
                            "id": name,
                            "label": option.get("_label", option.get("label", name))[:120],
                            "args": _tokens(option.get("arg-set", "")),
                        }
                    )
                if not options or len({o["id"] for o in options}) != len(options):
                    continue
                control.update(
                    options=options,
                    default=next((o["id"] for o in options if not o["args"]), options[0]["id"]),
                )
            else:
                control.update(args=_tokens(node.get("arg", ""), True), default="")
                if not control["args"]:
                    continue
            controls[key] = control
        except (ValueError, TypeError):
            continue
    return list(controls.values())


def validate(controls, values):
    if not isinstance(values, dict) or len(values) > 80:
        raise VideoError("Invalid animation settings.")
    known = {c["id"]: c for c in controls}
    for key, value in values.items():
        c = known.get(key)
        if c is None:
            raise VideoError("Animation settings changed; open Settings and save them again.")
        valid = False
        if c["type"] == "number":
            valid = (
                type(value) in {int, float}
                and math.isfinite(value)
                and (c["low"] <= value <= c["high"] or value == c["default"])
                and (not c["integer"] or value == int(value))
            )
        elif c["type"] == "boolean":
            valid = type(value) is bool
        elif c["type"] == "select":
            valid = isinstance(value, str) and value in {o["id"] for o in c["options"]}
        else:
            valid = (
                isinstance(value, str)
                and len(value) <= 2048
                and not any(ch in value for ch in "\x00\n\r")
            )
        if not valid:
            raise VideoError(f"Invalid value for {c['label']}.")
    return values.copy()


def arguments(controls, values):
    values = validate(controls, values)
    args = []
    for c in controls:
        if c["id"] not in values:
            continue  # Preserve the animation's native defaults unless explicitly saved.
        value = values[c["id"]]
        if c["type"] == "boolean":
            args.extend(c["yes"] if value else c["no"])
        elif c["type"] == "select":
            args.extend(next(o["args"] for o in c["options"] if o["id"] == value))
        elif c["type"] == "number":
            args.extend([c["args"][0], str(int(value)) if c["integer"] else format(value, ".12g")])
        elif value:
            args.extend([c["args"][0], value])
    return args


def _read():
    try:
        with PATH.open(encoding="utf-8") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw.encode()) > 128 * 1024:
            raise ValueError("Settings index is too large")
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or len(data) > 512
            or any(
                not ID.fullmatch(key) or not isinstance(values, dict)
                for key, values in data.items()
            )
        ):
            raise ValueError("Invalid settings index")
        return data
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise VideoError(f"Cannot read animation settings: {exc}") from exc


def saved(name, controls):
    # Package upgrades may remove controls. Keep only settings that still validate.
    values = _read().get(name, {})
    result = {}
    for c in controls:
        if c["id"] in values:
            try:
                result.update(validate([c], {c["id"]: values[c["id"]]}))
            except VideoError:
                pass
    return result


def remember(name, controls, values):
    if not isinstance(name, str) or not ID.fullmatch(name):
        raise VideoError("Invalid animation name.")
    values = validate(controls, values)
    try:
        with file_lock(PATH.with_suffix(".lock")):
            data = _read()
            if values:
                data[name] = values
            else:
                data.pop(name, None)
            raw = json.dumps(data, indent=2)
            if len(raw.encode()) > 128 * 1024 or len(data) > 512:
                raise VideoError("Animation settings index is full.")
            _atomic_write(PATH, raw)
    except (OSError, InstallError) as exc:
        raise VideoError(f"Cannot save animation settings: {exc}") from exc
