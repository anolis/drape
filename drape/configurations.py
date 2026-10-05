"""Capture and reapply selected theme engines without downloading or copying themes."""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gi.repository import GLib

from . import desktop
from .configuration_store import PARTS, ConfigurationError, validate_snapshot


def component_name(component):
    value = component["value"]
    if component["part"] == "wallpapers":
        return Path(unquote(urlsplit(value).path)).name or value
    return value or "Default"


def _local_wallpaper(value):
    uri = urlsplit(value)
    if uri.scheme == "file" and uri.netloc in ("", "localhost"):
        return Path(unquote(uri.path))
    if not uri.scheme and Path(value).is_absolute():
        return Path(value)
    return None


def locate(part, value):
    """Use desktop search paths, including system themes installed outside Drape."""
    if part == "wallpapers":
        return _local_wallpaper(value)
    if part == "kvantum":
        return desktop.qt.locate(value)
    if part == "libadwaita":
        folder = desktop.libadwaita.theme_dir(value)
        return folder.parent if folder else None
    if part in desktop.kde.DIRECTORIES:
        return desktop.kde.locate(part, value)
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    system = [
        Path(root)
        for root in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        if root
    ]
    if part in ("icons", "cursors"):
        roots = [Path.home() / ".icons", data / "icons", *(root / "icons" for root in system)]
    else:
        roots = [Path.home() / ".themes", data / "themes", *(root / "themes" for root in system)]
    return next((root / value for root in roots if (root / value).is_dir()), None)


def capture():
    """Read selected settings once; never inspect archives or generate previews."""
    components, warnings = [], []
    desktop.running_wm(refresh=True)
    for part in PARTS:
        if desktop.theme_part(part) != part or not desktop.supported(part):
            continue  # Store the actual engine, rather than duplicate UI aliases.
        try:
            value = desktop.get(part)
        except (OSError, ValueError, GLib.Error) as exc:
            warnings.append(f"{part}: {exc}")
            continue
        if isinstance(value, str) and (value or part == "desktop"):
            components.append({"part": part, "value": value})
    return {
        "desktop": desktop.current_desktop() or "unknown",
        "wm": desktop.running_wm() or "unknown",
        "components": components,
    }, warnings


def unavailable(component):
    """Explain missing/unsupported selections; never substitute a similarly named theme."""
    part, value = component["part"], component["value"]
    if not desktop.supported(part) or desktop.theme_part(part) != part:
        return "Unsupported by the current desktop or window manager"
    # GTK ships these compiled styles; Cinnamon's empty name selects its default.
    if (part == "gtk" and value in {"Adwaita", "Adwaita-dark", "HighContrast"}) or (
        part == "desktop" and not value
    ):
        return ""
    path = locate(part, value)
    if path is None or not path.exists():
        return "Theme files are missing" if part != "wallpapers" else "Wallpaper file is missing"
    if part == "wallpapers":
        return "" if path.is_file() else "Wallpaper is not a local image file"
    component = _appearance_component(part, value, path)
    if part not in desktop.compatible_parts(component):
        return "Theme component is incompatible with the current desktop"
    return ""


def _appearance_component(part, value, path):
    """Keep an installed component's explicit Cinnamon choice when saving/restoring looks."""
    component = {
        "name": value,
        "path": str(path),
        "provides": ["gtk" if part == "libadwaita" else part],
    }
    if part == "desktop":
        from . import installer

        try:
            records = installer.load_manifest()
        except installer.InstallError as error:
            raise ConfigurationError(str(error)) from error
        if any(
            stored.get("allow_incomplete_cinnamon") and stored["path"] == str(path)
            for entry in records.values()
            for stored in entry["components"]
        ):
            component["allow_incomplete_cinnamon"] = True
    return component


def review(snapshot):
    validate_snapshot(snapshot)
    desktop.running_wm(refresh=True)
    rows = []
    for component in snapshot["components"]:
        reason = unavailable(component)
        current = None
        if not reason:
            try:
                current = desktop.get(component["part"])
            except (OSError, ValueError, GLib.Error):
                pass
        rows.append({"component": component, "reason": reason, "current": current})
    return rows


def apply(snapshot, selected):
    """Revalidate after confirmation and report partial failures without hiding them."""
    validate_snapshot(snapshot)
    selected = set(selected)
    applied, failed = [], []
    desktop.running_wm(refresh=True)
    components = {c["part"]: c for c in snapshot["components"]}
    # A global theme may change several engines. Apply it first, then restore the
    # saved explicit selections so the final appearance matches the configuration.
    order = ("lookandfeel", *(part for part in PARTS if part != "lookandfeel"))
    for part in order:
        if part not in selected or part not in components:
            continue
        component = components[part]
        try:
            reason = unavailable(component)
            if reason:
                raise ConfigurationError(reason)
            value = component["value"]
            if (part == "gtk" and value in {"Adwaita", "Adwaita-dark", "HighContrast"}) or (
                part == "desktop" and not value
            ):
                done = desktop.set_(part, value)
            else:
                path = locate(part, value)
                done = desktop.apply_component(
                    _appearance_component(part, value, path),
                    [part],
                )
            if not done:
                raise ConfigurationError("The desktop could not apply this component")
            applied.append(part)
        except (desktop.ApplyError, ConfigurationError, OSError, ValueError, GLib.Error) as exc:
            failed.append((part, str(exc)))
    return applied, failed
