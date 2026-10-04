"""Kvantum themes and Qt widget-style setup, independent of the desktop shell."""

import configparser
import functools
import io
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from . import qt_restore
from .records import file_lock
from .session import atomic_text as _atomic_text
from .session import login_profiles, profile_block

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
THEMES_DIR = CONFIG_HOME / "Kvantum"
LIB_DIRS = (Path("/usr/lib"), Path("/usr/lib64"), Path("/usr/local/lib"))
PROC = Path("/proc")
RESTART_NOTE = (
    "First setup: log out and back in to enable Qt themes in desktop-launched apps. "
    "When switching themes later, restart the Qt apps."
)


# Detect plugins, rather than assuming that the manager supports both Qt versions.


@functools.lru_cache(maxsize=1)
def engines():
    found = set()
    for major in (5, 6):
        for root in LIB_DIRS:
            for pattern in (
                f"qt{major}/plugins/styles/libkvantum.so",
                f"*/qt{major}/plugins/styles/libkvantum.so",
            ):
                if any(path.is_file() for path in root.glob(pattern)):
                    found.add(major)
    return frozenset(found)


def refresh():
    engines.cache_clear()


def supported():
    return bool(engines())


def theme_names(folder):
    """Require the paired artwork/configuration; arbitrary SVG files are not themes."""
    folder = Path(folder)
    return sorted(
        path.stem
        for path in folder.glob("*.kvconfig")
        if path.is_file()
        and (folder / (path.stem + ".svg")).is_file()
        and re.fullmatch(r"[\w.+-]+", path.stem)
        and path.stem not in {"Default", "Kvantum"}
    )


def locate(name):
    if not name or not re.fullmatch(r"[\w.+-]+", name):
        return None
    candidates = [
        THEMES_DIR / name,
        HOME / ".themes" / name / "Kvantum",
        DATA_HOME / "themes" / name / "Kvantum",
    ]
    candidates += [Path(base) / "Kvantum" / name for base in ("/usr/share", "/usr/local/share")]
    candidates += [
        Path(base) / "themes" / name / "Kvantum" for base in ("/usr/share", "/usr/local/share")
    ]
    return next((path for path in candidates if name in theme_names(path)), None)


def _parser(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def get():
    try:
        return _parser(THEMES_DIR / "kvantum.kvconfig").get("General", "theme", fallback="")
    except (OSError, configparser.Error, UnicodeError):
        return ""


def configured():
    """Report persisted setup, not the environment of this already-running process."""
    try:
        environment = CONFIG_HOME / "environment.d/90-drape-qt.conf"
        return "QT_STYLE_OVERRIDE=kvantum" in environment.read_text() and all(
            "# BEGIN DRAPE QT STYLE\nexport QT_STYLE_OVERRIDE=kvantum\n# END DRAPE QT STYLE"
            in path.read_text()
            for path in login_profiles(HOME)
        )
    except OSError:
        return False


def session_active():
    """Check desktop launchers, not Drape's own (possibly updated) environment.

    None means the desktop environment could not be inspected. Updating systemd
    cannot change the environment inherited by an already-running desktop shell.
    """
    launchers = {
        "cinnamon",
        "gnome-shell",
        "plasmashell",
        "xfce4-session",
        "mate-session",
        "lxqt-session",
    }
    values = []
    try:
        processes = list(PROC.iterdir())
    except OSError:
        return None
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            argv = (process / "cmdline").read_bytes().split(b"\0")
            if not argv or Path(os.fsdecode(argv[0])).name not in launchers:
                continue
            values.append(
                b"QT_STYLE_OVERRIDE=kvantum" in (process / "environ").read_bytes().split(b"\0")
            )
        except OSError:
            continue
    return all(values) if values else None


def restore_available():
    return (CONFIG_HOME / "drape/qt-restore.json").exists()


def restore_description():
    try:
        state = json.loads((CONFIG_HOME / "drape/qt-restore.json").read_text())
        if not state["legacy"]:
            return "Restore the Qt style override and selected theme saved before Drape enabled Kvantum. Unrelated settings, downloaded themes and installed engine packages are kept. Log out and back in afterward."
    except (OSError, ValueError, KeyError):
        pass
    return "Remove Drape's Qt login override. This setup predates restoration backups, so its original style and theme cannot be recovered automatically. Other settings, downloaded themes and installed engine packages are kept. Log out and back in afterward."


def enable(theme=None):
    """Persist the engine for both systemd sessions and display-manager login shells.

    Existing Qt processes keep their style. The profile export is needed on desktops
    whose application launchers do not inherit systemd's environment.d settings.
    """
    if not supported():
        return False
    if theme is not None and locate(theme) is None:
        raise ValueError("This Kvantum theme is missing its configuration or artwork.")
    environment = CONFIG_HOME / "environment.d" / "90-drape-qt.conf"
    profiles = login_profiles(HOME)
    config = THEMES_DIR / "kvantum.kvconfig"
    backup = CONFIG_HOME / "drape/qt-restore.json"
    with file_lock(CONFIG_HOME / "drape" / "session.lock"):
        before = {
            path: path.read_text() if path.exists() else None
            for path in (environment, *profiles, config)
        }
        previous_backup = backup.read_text() if backup.exists() else None
        legacy = any("# BEGIN DRAPE QT STYLE" in (before[path] or "") for path in profiles)
        state = (
            json.loads(previous_backup)
            if previous_backup
            else {
                "legacy": legacy,
                "environment": None if legacy else os.environ.get("QT_STYLE_OVERRIDE"),
                "files": {},
            }
        )
        planned = dict(before)
        if theme is not None:
            parser = _parser(config)
            if not parser.has_section("General"):
                parser.add_section("General")
            parser.set("General", "theme", theme)
            text = io.StringIO()
            parser.write(text, space_around_delimiters=False)
            planned[config] = text.getvalue()
        planned[environment] = "# Qt widget style selected by Drape\nQT_STYLE_OVERRIDE=kvantum\n"
        for profile in profiles:
            planned[profile] = profile_block(
                before[profile] or "", "DRAPE QT STYLE", "export QT_STYLE_OVERRIDE=kvantum"
            )
        try:
            # Save the first baseline before touching anything. Later theme switches
            # update only the last-written values, never the original settings.
            for path, original in before.items():
                kind = (
                    "theme"
                    if path == config
                    else "environment"
                    if path == environment
                    else "profile"
                )
                state["files"].setdefault(
                    str(path),
                    {
                        "before": qt_restore.legacy_original(original, kind)
                        if legacy
                        else original,
                        "written": planned[path],
                        "kind": kind,
                    },
                )
                state["files"][str(path)]["written"] = planned[path]
            # Record the intended writes first so a restart can still undo setup
            # interrupted between file replacements.
            _atomic_text(backup, json.dumps(state, indent=2))
            for path, content in planned.items():
                if content is not None and content != before[path]:
                    _atomic_text(path, content)
        except BaseException:
            for path, content in before.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_text(path, content)
            if previous_backup is None:
                backup.unlink(missing_ok=True)
            else:
                _atomic_text(backup, previous_backup)
            raise
    os.environ["QT_STYLE_OVERRIDE"] = "kvantum"
    _activation_style("kvantum")
    return True


def _activation_style(style):
    # Newly D-Bus/systemd-activated apps can adopt the style without a new login.
    command = shutil.which("dbus-update-activation-environment")
    if command:
        try:
            subprocess.run(
                [command, "--systemd", "QT_STYLE_OVERRIDE=" + style],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def disable():
    """Restore the first Qt setup baseline; keep downloaded themes installed."""
    profiles = login_profiles(HOME)
    environment = CONFIG_HOME / "environment.d" / "90-drape-qt.conf"
    backup = CONFIG_HOME / "drape/qt-restore.json"
    with file_lock(CONFIG_HOME / "drape" / "session.lock"):
        if backup.exists():
            state = json.loads(backup.read_text())
            originals = {
                Path(path): Path(path).read_text() if Path(path).exists() else None
                for path in state["files"]
            }
            # Validate every file before restoring any of them.
            changes = {
                path: qt_restore.restore_text(path, text, state["files"][str(path)])
                for path, text in originals.items()
            }
            try:
                for path, text in changes.items():
                    if text is None:
                        path.unlink(missing_ok=True)
                    elif text != originals[path]:
                        _atomic_text(path, text)
                backup.unlink()
            except BaseException:
                for path, text in originals.items():
                    if text is None:
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_text(path, text)
                raise
            style = state["environment"]
            if style is None:
                os.environ.pop("QT_STYLE_OVERRIDE", None)
            else:
                os.environ["QT_STYLE_OVERRIDE"] = style
            _activation_style(style or "")
            return
        for profile in profiles:
            if profile.exists():
                original = profile.read_text()
                text = profile_block(original, "DRAPE QT STYLE")
                if text != original:
                    _atomic_text(profile, text)
        if (
            environment.exists()
            and environment.read_text()
            == "# Qt widget style selected by Drape\nQT_STYLE_OVERRIDE=kvantum\n"
        ):
            environment.unlink()
    os.environ.pop("QT_STYLE_OVERRIDE", None)
    _activation_style("")


# Optional engine installation uses distribution repositories, with no added sources.


def install_command(major):
    if major not in (5, 6):
        raise ValueError("Qt version must be 5 or 6")
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return None
    families = {release.get("ID"), *release.get("ID_LIKE", "").split()}
    if "arch" in families and shutil.which("pacman"):
        return [
            "pacman",
            "-S",
            "--needed",
            "--noconfirm",
            "kvantum-qt5" if major == 5 else "kvantum",
        ]
    if families & {"debian", "ubuntu"} and shutil.which("apt-get"):
        return ["apt-get", "install", "-y", f"qt{major}-style-kvantum"]
    return None


def check_install(command):
    """Reject unavailable packages and dependency plans that remove existing packages."""
    probe = (
        ["apt-get", "-s", *command[1:]]
        if command[0] == "apt-get"
        else ["pacman", "-Sp", "--print-format", "%n", command[-1]]
    )
    result = subprocess.run(
        probe,
        capture_output=True,
        text=True,
        env=dict(os.environ, LC_ALL="C"),
        timeout=30,
        check=False,
    )
    if result.returncode or re.search(r"(?m)^Remv ", result.stdout):
        raise ValueError(
            "This Qt engine cannot be installed safely from your configured repositories.\n"
            + (result.stdout + result.stderr)[-3000:]
        )
