"""Reversible GTK 4 user styles for native GNOME applications.

Libadwaita does not select arbitrary themes through gtk-theme. Import the theme's
GTK 4 stylesheet through GTK's user CSS instead, keeping a first-use backup.
"""

import ctypes.util
import functools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from gi.repository import Gio

from .records import file_lock
from .session import atomic_text

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
RESTART_NOTE = "Restart native GNOME apps such as Files and Settings to load the GTK 4 style."
VALIDATE = """
import gi, sys
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
errors = []
provider = Gtk.CssProvider()
provider.connect('parsing-error', lambda _, section, error:
    errors.append(str(error)) if error.domain == 'gtk-css-parser-error-quark' else None)
provider.load_from_path(sys.argv[1])
if errors:
    print('\\n'.join(errors[:5]))
    sys.exit(1)
"""


@functools.lru_cache(maxsize=1)
def available():
    return bool(ctypes.util.find_library("gtk-4"))


def theme_dir(name):
    if not name or Path(name).name != name or name in (".", ".."):
        return None
    data = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
    for root in (
        HOME / ".themes",
        data / "themes",
        Path("/usr/share/themes"),
        Path("/usr/local/share/themes"),
    ):
        folder = root / name / "gtk-4.0"
        if (folder / "gtk.css").is_file():
            return folder
    return None


def _backup():
    return CONFIG_HOME / "drape/libadwaita-restore.json"


def configured():
    return _backup().is_file()


def get():
    try:
        state = json.loads(_backup().read_text())
        if all(_snapshot(Path(path)) == written for path, written in state["written"].items()):
            return state["theme"]
    except (OSError, ValueError, KeyError):
        pass
    return ""


def dark_preferred():
    source = Gio.SettingsSchemaSource.get_default()
    schema = source and source.lookup("org.gnome.desktop.interface", True)
    return bool(
        schema
        and schema.has_key("color-scheme")
        and Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme")
        == "prefer-dark"
    )


def _snapshot(path):
    if path.is_symlink():
        return {"link": os.readlink(path)}
    if path.exists():
        return {"text": path.read_text(), "mode": path.stat().st_mode & 0o777}
    return None


def _write(path, snapshot):
    if snapshot is None:
        path.unlink(missing_ok=True)
    elif "link" in snapshot:
        with tempfile.TemporaryDirectory(prefix=".drape-", dir=path.parent) as temporary:
            temporary = Path(temporary) / "link"
            temporary.symlink_to(snapshot["link"])
            os.replace(temporary, path)
    else:
        atomic_text(path, snapshot["text"])
        os.chmod(path, snapshot["mode"])


def apply(name):
    folder = theme_dir(name)
    if folder is None:
        raise ValueError(
            "This theme has no gtk-4.0/gtk.css. A GTK 3 theme cannot style native GNOME apps."
        )
    source = folder / "gtk.css"
    dark = dark_preferred()
    if dark and (folder / "gtk-dark.css").is_file():
        source = folder / "gtk-dark.css"
    # Gtk 3 and Gtk 4 cannot coexist in one Python process. Validate separately
    # before writing user CSS, including relative stylesheet imports.
    result = subprocess.run(
        [sys.executable, "-c", VALIDATE, str(source)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            "The GTK 4 stylesheet could not be loaded safely:\n"
            + (result.stdout + result.stderr)[-2000:]
        )
    with file_lock(CONFIG_HOME / "drape/session.lock"):
        backup = _backup()
        prior_backup = backup.read_text() if backup.exists() else None
        # GTK loads only the user gtk.css; a user gtk-dark.css would not select
        # the dark variant. Choose the source using GNOME's preference instead.
        paths = [CONFIG_HOME / "gtk-4.0/gtk.css"]
        before = {str(path): _snapshot(path) for path in paths}
        state = json.loads(prior_backup) if prior_backup else {"original": before}
        if prior_backup and before != state["written"]:
            raise ValueError(
                "Your GTK 4 stylesheet changed outside Drape. Restore or resolve it before applying another theme."
            )
        state.update(
            theme=name,
            dark=dark,
            written={
                str(path): {
                    "text": f'/* GTK 4 style selected by Drape */\n@import url("{source.resolve().as_uri()}");\n',
                    "mode": 0o600,
                }
                for path in paths
            },
        )
        try:
            atomic_text(backup, json.dumps(state, indent=2))
            for path, snapshot in state["written"].items():
                _write(Path(path), snapshot)
        except BaseException:
            for path, snapshot in before.items():
                _write(Path(path), snapshot)
            if prior_backup is None:
                backup.unlink(missing_ok=True)
            else:
                atomic_text(backup, prior_backup)
            raise
    return True


def restore():
    with file_lock(CONFIG_HOME / "drape/session.lock"):
        if not configured():
            return
        state = json.loads(_backup().read_text())
        before = {path: _snapshot(Path(path)) for path in state["original"]}
        for path, snapshot in before.items():
            if snapshot not in (state["written"][path], state["original"][path]):
                raise ValueError(
                    f"{path} changed outside Drape. Restore it manually to preserve your edits."
                )
        try:
            for path, snapshot in state["original"].items():
                _write(Path(path), snapshot)
            _backup().unlink()
        except BaseException:
            for path, snapshot in before.items():
                _write(Path(path), snapshot)
            raise
