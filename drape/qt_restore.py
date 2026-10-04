"""Undo Qt setup without overwriting unrelated edits made after activation."""

import configparser
import io
import re

from .session import profile_block


def _block(text):
    match = re.search(
        r"(?m)^# BEGIN DRAPE QT STYLE\n.*?^# END DRAPE QT STYLE\n?",
        text or "",
        flags=re.DOTALL,
    )
    return match.group() if match else ""


def _theme(text):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(text or "")
    return parser, parser.get("General", "theme", fallback=None)


def restore_text(path, current, entry):
    """Three-way restore of the owned block/key; refuse conflicting user edits."""
    before, written = entry["before"], entry["written"]
    if before == written:
        return current
    if current == before:
        return current
    if current == written:
        return before
    if entry["kind"] == "profile":
        if _block(current) != _block(written):
            raise ValueError(
                f"The Qt block in {path} changed. Restore it manually to avoid losing edits."
            )
        return (current or "").replace(_block(written), _block(before), 1)
    if entry["kind"] == "theme":
        parser, selected = _theme(current)
        _, applied = _theme(written)
        _, original = _theme(before)
        if selected != applied:
            raise ValueError(f"The Qt theme in {path} changed outside Drape. Restore it manually.")
        if original is None:
            parser.remove_option("General", "theme")
        else:
            if not parser.has_section("General"):
                parser.add_section("General")
            parser.set("General", "theme", original)
        output = io.StringIO()
        parser.write(output, space_around_delimiters=False)
        return output.getvalue()
    raise ValueError(f"{path} changed outside Drape. Restore it manually to avoid losing edits.")


def legacy_original(text, kind):
    """Old installations lack a baseline; remove only identifiable Drape setup."""
    if kind == "profile":
        return profile_block(text or "", "DRAPE QT STYLE") or None
    if (
        kind == "environment"
        and text == "# Qt widget style selected by Drape\nQT_STYLE_OVERRIDE=kvantum\n"
    ):
        return None
    return text
