"""Resolve bundled GTK styles into files usable by GTK 4 user CSS.

The theme loader normally registers gtk.gresource. User CSS loads earlier and
libadwaita may select another built-in theme, so imports cannot rely on that
process-global resource registration. Copy the static assets and use file URIs.
"""

import hashlib
import re
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from gi.repository import Gio, GLib

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
RESOURCE_URI = re.compile(r"resource://(/[^\s\"')]+)")
URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.DOTALL)


def prepare(source, config_home):
    source = Path(source)
    bundle = source.parent / "gtk.gresource"
    if not bundle.is_file():
        return source
    if bundle.stat().st_size > MAX_BYTES:
        raise ValueError("The GTK resource bundle exceeds the supported size.")
    content = source.read_text()
    digest = hashlib.sha256(
        bundle.read_bytes() + content.encode() + str(source).encode()
    ).hexdigest()
    root = config_home / "drape/gtk4-resources" / digest
    entry = root / ".drape-entry.css"
    if entry.is_file():
        return entry
    try:
        resource = Gio.Resource.load(str(bundle))
    except GLib.Error as error:
        raise ValueError(f"The GTK resource bundle could not be read: {error}") from error
    files = {}
    total = 0

    def walk(prefix):
        nonlocal total
        if len(PurePosixPath(prefix).parts) > 64:
            raise ValueError("The GTK resource bundle is nested too deeply.")
        for name in resource.enumerate_children(prefix, Gio.ResourceLookupFlags.NONE):
            path = prefix + name
            if ".." in PurePosixPath(path).parts or "\\" in path:
                raise ValueError("The GTK resource bundle contains an unsafe path.")
            if name.endswith("/"):
                walk(path)
                continue
            _, size, _ = resource.get_info(path, Gio.ResourceLookupFlags.NONE)
            total += size
            if total > MAX_BYTES or len(files) >= MAX_FILES:
                raise ValueError("The GTK resource bundle exceeds the supported extraction limits.")
            files[path] = resource.lookup_data(path, Gio.ResourceLookupFlags.NONE).get_data()

    try:
        walk("/")
    except GLib.Error as error:
        raise ValueError(f"The GTK resource bundle could not be read: {error}") from error

    def rewrite(text):
        def replace(match):
            path = unquote(match[1])
            return (root / path.lstrip("/")).as_uri() if path in files else match[0]

        return RESOURCE_URI.sub(replace, text)

    # Keep relative references in the theme's wrapper pointing at its own files.
    def absolute_url(match):
        value = match[2].strip()
        if not value or value.startswith("#") or urlsplit(value).scheme:
            return match[0]
        return f'url("{(source.parent / unquote(value)).resolve().as_uri()}")'

    wrapper = URL.sub(absolute_url, rewrite(content))
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".drape-resource-", dir=root.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        for path, data in files.items():
            target = staging / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.endswith(".css"):
                data = rewrite(data.decode("utf-8")).encode()
            target.write_bytes(data)
        (staging / entry.name).write_text(wrapper)
        try:
            staging.rename(root)
        except OSError:
            # Another apply process can prepare the same content concurrently.
            if not entry.is_file():
                raise
    return entry
