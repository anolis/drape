"""Durable, per-user named appearance configurations, separate from installs."""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .records import InstallError, _atomic_write, file_lock

PATH = (
    Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "drape/configurations.json"
)
PARTS = (
    "kvantum",
    "lookandfeel",
    "plasma",
    "colors",
    "gtk",
    "libadwaita",
    "desktop",
    "wm",
    "xfwm",
    "aurorae",
    "icons",
    "cursors",
    "wallpapers",
)


class ConfigurationError(Exception):
    """The configuration could not be read, saved or applied."""


def valid_name(name):
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ConfigurationError("Choose a configuration name of 1–100 characters.")
    return name


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ConfigurationError("Invalid configuration snapshot.")
    if any(not isinstance(snapshot.get(key), str) for key in ("desktop", "wm")):
        raise ConfigurationError("Invalid desktop information in configuration.")
    components = snapshot.get("components")
    if not isinstance(components, list) or not 1 <= len(components) <= len(PARTS):
        raise ConfigurationError("Select at least one theme component to save.")
    seen = set()
    for component in components:
        if not isinstance(component, dict):
            raise ConfigurationError("Invalid saved theme component.")
        part, value = component.get("part"), component.get("value")
        if part not in PARTS or part in seen or not isinstance(value, str) or len(value) > 8192:
            raise ConfigurationError("Invalid or duplicate saved theme component.")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ConfigurationError("Invalid saved theme value.")
        if part != "wallpapers" and (
            "/" in value
            or value in (".", "..")
            or value.startswith("-")
            or (not value and part != "desktop")
        ):
            raise ConfigurationError("Invalid saved theme name.")
        seen.add(part)
    return snapshot


class ConfigurationStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else PATH
        self.backup = self.path.with_suffix(".json.bak")

    def load(self):
        """Corrupt or missing-with-backup records are never silently replaced."""
        try:
            with self.path.open(encoding="utf-8") as stream:
                text = stream.read(4 * 1024 * 1024 + 1)
            if len(text) > 4 * 1024 * 1024:
                raise ValueError("configuration file is too large")
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("unsupported configuration format")
            entries = data.get("configurations")
            if not isinstance(entries, dict):
                raise ConfigurationError("invalid configuration list")
            for key, entry in entries.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    raise ConfigurationError("invalid configuration record")
                valid_name(entry.get("name"))
                validate_snapshot(entry)
                if any(not isinstance(entry.get(k), str) for k in ("created", "updated")):
                    raise ValueError("invalid configuration date")
            return entries
        except FileNotFoundError as exc:
            if not self.backup.exists():
                return {}
            raise ConfigurationError(
                f"Configurations are missing. Restore the backup at {self.backup} first."
            ) from exc
        except (OSError, ValueError, ConfigurationError) as exc:
            raise ConfigurationError(
                f"Cannot read configurations at {self.path}: {exc}. "
                "Your saved configurations have not been overwritten."
            ) from exc

    def _edit(self, mutate):
        try:
            with file_lock(self.path.with_suffix(".lock")):
                entries = self.load()
                result = mutate(entries)
                # Keep the previous valid version before atomically replacing records.
                if self.path.exists():
                    _atomic_write(self.backup, self.path.read_text(encoding="utf-8"))
                _atomic_write(
                    self.path,
                    json.dumps({"version": 1, "configurations": entries}, indent=2),
                )
                return result
        except (OSError, InstallError) as exc:
            raise ConfigurationError(f"Could not save configurations: {exc}") from exc

    @staticmethod
    def _unique(entries, name, except_key=None):
        if any(
            entry["name"].casefold() == name.casefold()
            for key, entry in entries.items()
            if key != except_key
        ):
            raise ConfigurationError("A configuration with this name already exists.")

    def save(self, name, snapshot):
        name = valid_name(name)
        # Copy through JSON so subsequent UI edits cannot mutate the saved snapshot.
        snapshot = json.loads(json.dumps(validate_snapshot(snapshot)))
        key = uuid.uuid4().hex

        def add(entries):
            self._unique(entries, name)
            now = datetime.now(UTC).isoformat()
            entries[key] = dict(snapshot, name=name, created=now, updated=now)
            return key

        return self._edit(add)

    def rename(self, key, name):
        name = valid_name(name)

        def change(entries):
            if key not in entries:
                raise ConfigurationError("This configuration was removed. Reload the list.")
            self._unique(entries, name, key)
            entries[key]["name"] = name
            entries[key]["updated"] = datetime.now(UTC).isoformat()

        self._edit(change)

    def delete(self, key):
        self._edit(lambda entries: entries.pop(key, None))
