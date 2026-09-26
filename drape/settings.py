"""Small per-user preferences."""

import json
import os
from pathlib import Path

PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "drape" / "settings.json"
DEFAULTS = {"only_applicable": True}


def get(key):
    try:
        return json.loads(PATH.read_text()).get(key, DEFAULTS.get(key))
    except (OSError, ValueError):
        return DEFAULTS.get(key)


def set(key, value):  # noqa: A001 - mirrors get()
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        data = {}
    data[key] = value
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2))
