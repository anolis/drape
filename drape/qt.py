"""Kvantum themes and Qt widget-style setup, independent of the desktop shell."""

import configparser
import functools
import io
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .records import file_lock

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
THEMES_DIR = CONFIG_HOME / "Kvantum"
LIB_DIRS = (Path("/usr/lib"), Path("/usr/lib64"), Path("/usr/local/lib"))
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


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".drape-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


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
    profile = HOME / ".profile"
    config = THEMES_DIR / "kvantum.kvconfig"
    with file_lock(CONFIG_HOME / "drape" / "qt.lock"):
        before = {
            path: path.read_text() if path.exists() else None
            for path in (environment, profile, config)
        }
        try:
            if theme is not None:
                parser = _parser(config)
                if not parser.has_section("General"):
                    parser.add_section("General")
                parser.set("General", "theme", theme)
                text = io.StringIO()
                parser.write(text, space_around_delimiters=False)
                _atomic_text(config, text.getvalue())
            _atomic_text(
                environment, "# Qt widget style selected by Drape\nQT_STYLE_OVERRIDE=kvantum\n"
            )
            start, end = "# BEGIN DRAPE QT STYLE", "# END DRAPE QT STYLE"
            existing = before[profile] or ""
            existing = re.sub(
                r"(?m)^# BEGIN DRAPE QT STYLE\n.*?^# END DRAPE QT STYLE\n?",
                "",
                existing,
                flags=re.DOTALL,
            )
            _atomic_text(
                profile,
                existing.rstrip("\n") + f"\n\n{start}\nexport QT_STYLE_OVERRIDE=kvantum\n{end}\n",
            )
        except BaseException:
            for path, content in before.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_text(path, content)
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
    """Remove Drape's session override, preserving the selected theme and other setup."""
    profile = HOME / ".profile"
    environment = CONFIG_HOME / "environment.d" / "90-drape-qt.conf"
    with file_lock(CONFIG_HOME / "drape" / "qt.lock"):
        if profile.exists():
            original = profile.read_text()
            text = re.sub(
                r"(?m)^# BEGIN DRAPE QT STYLE\n.*?^# END DRAPE QT STYLE\n?",
                "",
                original,
                flags=re.DOTALL,
            )
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
