"""What's inside a gnome-look download, without downloading all of it.

Uploaders choose their own category, and some choose wrong (a wallpaper pack under Desktop themes,
icons under Controls...). Folder names alone say what a theme contains, and they can be read cheaply:
a zip keeps its table of contents at the end (fetched with HTTP range requests), and a tar lists
entries as it goes (the first part of the stream is enough). Results are cached on disk.
"""

import bz2
import io
import json
import lzma
import os
import posixpath
import re
import tarfile
import threading
import time
import zipfile
import zlib
from pathlib import Path

import requests

from .pling import USER_AGENT
from . import http

CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "drape" / "peek-v4.json"
TAR_BUDGETS = (
    64 * 1024,
    256 * 1024,
)  # bytes read from the start of a tar download; more only if needed
BLOCK = 128 * 1024  # zip reads are fetched and cached in blocks this size
IMAGE_RE = re.compile(r"\.(jpe?g|png|webp|jxl|avif|bmp)$", re.I)
_lock = threading.Lock()
# The transport layer shares rate-limit cooldowns with catalog requests and installs.
RateLimited = http.RateLimited


# what a file host answers once a signed download link has expired
EXPIRED_STATUSES = {401, 403, 410}


class InspectionFailed(Exception):
    """No archive evidence was obtained; callers must not record a verdict."""


class LinkExpired(InspectionFailed):
    """The signed download link was refused; fresh links may still work."""


def fresh_item(item):
    """Refresh expiring download links, preserving a catalog cooldown for the caller."""
    from . import pling

    try:
        return pling.get(item.id)
    except pling.PlingError as exc:
        if isinstance(exc.__cause__, RateLimited):
            raise exc.__cause__ from None
        raise InspectionFailed("Could not refresh download links.") from exc


def _file_named(item, name):
    file = next((f for f in item.files if f.name == name), None)
    if file is None:
        raise InspectionFailed(f"{name} is no longer offered for download.")
    return file


def contents_refreshing(item, file, use_cache=True):
    """`contents` for one download of `item`, re-fetching the item's links once if they have
    expired. Returns (item, result), where item may be the refreshed one; a later call with it
    uses the fresh link for any of its files."""
    file = _file_named(item, file.name)
    try:
        return item, contents(item.id, file.url, file.name, use_cache)
    except LinkExpired:
        item = fresh_item(item)
        file = _file_named(item, file.name)
        try:
            return item, contents(item.id, file.url, file.name, use_cache)
        except RateLimited as exc:
            exc.item = item
            raise


# ---------------------------------------------------------------- what a list of paths contains


def classify_names(names):
    """Parts a theme archive contains, judged from its paths only."""
    parts = set()
    lower = [n.lower().strip("/") for n in names]
    dirs = set()
    for n in lower:
        bits = n.split("/")
        for i in range(1, len(bits)):
            dirs.add("/".join(bits[:i]))
    has = lambda pattern: any(re.search(pattern, n) for n in lower)  # noqa: E731
    if has(r"\.plymouth$"):
        parts.add("plymouth")
    if has(r"(^|/)metadata\.desktop$") and has(r"(^|/)main\.qml$"):
        parts.update(("login", "sddm"))
    if (
        has(r"(^|/)index\.html$")
        and has(r"(^|/)(index\.theme|index\.yml|theme\.json)$")
        and not has(r"/gtk-3\.0/")
    ):
        parts.update(("login", "webgreeter"))
    if has(r"(^|/)gtk-[234]\.0(/|$)"):
        parts.add("gtk")
        parts.update(f"gtk-{v}.0" for v in (2, 3, 4) if has(rf"(^|/)gtk-{v}\.0(/|$)"))
    if has(r"(^|/)metacity-1(/|$)"):
        parts.add("wm")
    if has(r"(^|/)xfwm4(/|$)"):
        parts.add("xfwm")
    if has(r"(^|/)decoration\.svgz?$") or has(r"(^|/)aurorae/themes/[^/]+/"):
        parts.add("aurorae")
    if has(r"(^|/)contents/defaults$") or has(r"(^|/)plasma/look-and-feel/[^/]+/"):
        parts.add("lookandfeel")
    if has(r"(^|/)(widgets|dialogs)/[^/]+\.svgz?$") and has(r"(^|/)metadata\.(json|desktop)$"):
        parts.add("plasma")
    if has(r"\.colors$"):
        parts.add("colors")
    for folder in ("gnome-shell", "openbox-3"):
        if has(rf"(^|/){folder}(/|$)"):
            parts.add(folder)
    if has(r"(^|/)cinnamon/cinnamon\.css$") or any(re.search(r"(^|/)cinnamon$", d) for d in dirs):
        parts.add("desktop")
    if has(r"(^|/)cursors/[^/]+$") or has(r"\.(cur|ani)$"):
        parts.add("cursors")
    # icon themes: size folders and context folders, in either order (48x48/apps or apps/48, @2x too)
    size = r"(\d+(x\d+)?(@\dx?)?|scalable|symbolic)"
    context = (
        r"(apps|places|mimetypes|actions|devices|status|categories|panel|emblems|animations|emotes)"
    )
    icon_dir = re.compile(rf"(^|/)({size}/{context}|{context}/{size})(/|$)")
    icon_files = [n for n in lower if re.search(r"\.(png|svg)$", n) and icon_dir.search(n)]
    if (
        has(r"(^|/)icon-theme\.cache$")
        or len(icon_files) >= 10
        or (has(r"(^|/)index\.theme$") and any(icon_dir.search(d) for d in dirs))
    ):
        parts.add("icons")
    # pictures that aren't a theme's own assets
    theme_bits = r"(gtk-[234]\.0|metacity-1|xfwm4|cinnamon|gnome-shell|openbox-3|plasma|aurorae|assets|cursors|icons?|css|fonts?)/"
    pictures = [
        n
        for n in lower
        if IMAGE_RE.search(n)
        and not re.search(theme_bits, n)
        and not icon_dir.search(n)
        and not re.search(r"(preview|screenshot|thumbnail|logo)", n.rsplit("/", 1)[-1])
    ]
    if (
        pictures
        and not parts & {"plasma", "lookandfeel", "aurorae"}
        and (
            not parts
            or len(pictures) >= 3
            or any(re.search(r"wall|background", n) for n in pictures)
        )
    ):
        parts.add("wallpapers")
    parts.update(marker for marker in CINNAMON_MARKERS if "__drape_" + marker + "__" in names)
    return parts


# ---------------------------------------------------------------- reading archive listings over HTTP


class _RangeFile(io.RawIOBase):
    """A seekable file over HTTP range requests, fetching and caching fixed blocks."""

    def __init__(self, url, size):
        self.url, self.size, self.pos = url, size, 0
        self.blocks = {}
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def _block(self, i):
        if i not in self.blocks:
            start = i * BLOCK
            end = min(start + BLOCK, self.size) - 1
            r = http.get(
                self.url,
                headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"},
                timeout=(15, 60),
                retry_server_errors=False,
                stream=True,
            )
            try:
                r.raise_for_status()
                if r.status_code != 206:
                    raise OSError("Host does not support bounded ZIP range reads")
                self.blocks[i] = _read_prefix(r, end - start + 1)
                if len(self.blocks[i]) != end - start + 1:
                    raise OSError("Truncated ZIP range response")
            finally:
                r.close()
            self.fetched += len(self.blocks[i])
            if self.fetched > 4 * 1024 * 1024:
                raise OSError("zip table of contents is too large to peek at")
        return self.blocks[i]

    def readinto(self, buf):
        n = min(len(buf), self.size - self.pos)
        if n <= 0:
            return 0
        out = bytearray()
        while len(out) < n:
            i, off = divmod(self.pos + len(out), BLOCK)
            out += self._block(i)[off : off + n - len(out)]
        buf[:n] = out[:n]
        self.pos += n
        return n


def _size(url):
    r = http.get(
        url,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
        timeout=(15, 30),
        retry_server_errors=False,
        stream=True,
    )
    try:
        r.raise_for_status()
        total = r.headers.get("content-range", "").rsplit("/", 1)[-1]
        return int(total) if total.isdigit() else 0
    finally:
        r.close()


def _read_prefix(response, limit):
    """Close callers' streams after a bounded prefix, even if a host ignores Range."""
    result = bytearray()
    for chunk in response.iter_content(chunk_size=min(limit, 16384)):
        result.extend(chunk[: limit - len(result)])
        if len(result) >= limit:
            break
    return bytes(result)


CSS_LIMIT = 512 * 1024
DECOMPRESSED_LIMIT = 8 * 1024 * 1024
CINNAMON_MARKERS = {
    "cinnamon-legacy",
    "cinnamon-modern",
    "cinnamon-unknown",
    "cinnamon-modern-only",
    "cinnamon-pre54",
}


# Cinnamon stylesheet generation checks


def _css_root(name):
    match = re.match(r"^(.*(?:^|/)cinnamon/).*\.css$", name, re.I)
    return match[1] if match else None


def _cinnamon_markers(names, styles, complete):
    """Tag inspected CSS; partial imported styles remain unknown, not old."""
    from .theme_css import cinnamon_css_outdated, cinnamon_css_imports, OLD_DIALOG_RE

    roots = {_css_root(name) for name in names if name.lower().endswith("cinnamon/cinnamon.css")}
    if not roots:
        return []
    states = []
    old_supported = False
    modern_only = True
    for root in roots:
        main = next(
            (styles[name] for name in styles if name.lower() == (root + "cinnamon.css").lower()),
            None,
        )
        texts = [text for name, text in styles.items() if _css_root(name) == root]
        all_read = all(name in styles for name in names if _css_root(name) == root)
        clean = re.sub(r"/\*.*?\*/", "", "\n".join(texts), flags=re.S)
        old_supported = old_supported or bool(OLD_DIALOG_RE.search(clean))
        imports = cinnamon_css_imports(main or "")
        imports_resolved = "@import" not in (main or "") or (
            bool(imports)
            and all(
                posixpath.normpath(root + ref) in {posixpath.normpath(name) for name in styles}
                for ref in imports
            )
        )
        modern_only = (
            modern_only
            and complete
            and all_read
            and main is not None
            and imports_resolved
            and not OLD_DIALOG_RE.search(clean)
        )
        if main is None:
            states.append("unknown")
        elif not cinnamon_css_outdated("\n".join(texts)):
            states.append("modern")
        elif "@import" not in main or (
            complete
            and all_read
            and cinnamon_css_imports(main)
            and all(
                posixpath.normpath(root + ref) in {posixpath.normpath(name) for name in styles}
                for ref in cinnamon_css_imports(main)
            )
        ):
            states.append("legacy")
        else:
            states.append("unknown")
    status = (
        "modern"
        if "modern" in states
        else "legacy"
        if all(s == "legacy" for s in states)
        else "unknown"
    )
    markers = ["__drape_" + "cinnamon-" + status + "__"]
    if old_supported:
        markers.append("__drape_cinnamon-pre54__")
    if status == "modern" and modern_only:
        markers.append("__drape_cinnamon-modern-only__")
    return markers


def list_archive(url, filename, budget=TAR_BUDGETS[0]):
    """(paths inside, complete?) for a remote download, reading as little as possible."""
    name = filename.lower()
    if IMAGE_RE.search(name):
        return [filename], True
    if name.endswith(".zip"):
        size = _size(url)
        if not size:
            return [], False
        with zipfile.ZipFile(io.BufferedReader(_RangeFile(url, size), buffer_size=BLOCK)) as z:
            names, styles, remaining = z.namelist(), {}, CSS_LIMIT * 2
            for info in z.infolist():
                if _css_root(info.filename) and info.file_size <= min(CSS_LIMIT, remaining):
                    # Symlinks do not contain the target's CSS.
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        continue
                    styles[info.filename] = z.read(info).decode(errors="replace")
                    remaining -= info.file_size
            return names + _cinnamon_markers(names, styles, True), True
    if re.search(r"\.(tar(\.(gz|xz|bz2|zst))?|tgz|txz|tbz2?)$", name):
        r = http.get(
            url,
            headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{budget - 1}"},
            timeout=(15, 60),
            retry_server_errors=False,
            stream=True,
        )
        try:
            r.raise_for_status()
            raw = _read_prefix(r, budget)
        finally:
            r.close()
        complete = len(raw) < budget
        try:
            if name.endswith((".gz", ".tgz")):
                data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, DECOMPRESSED_LIMIT)
            elif name.endswith((".xz", ".txz")):
                data = lzma.LZMADecompressor(memlimit=64 * 1024 * 1024).decompress(
                    raw, max_length=DECOMPRESSED_LIMIT
                )
            elif name.endswith((".bz2", ".tbz", ".tbz2")):
                data = bz2.BZ2Decompressor().decompress(raw, max_length=DECOMPRESSED_LIMIT)
            else:
                data = raw
        except (zlib.error, lzma.LZMAError, OSError, EOFError):
            return [], False
        if len(data) >= DECOMPRESSED_LIMIT:
            complete = False
        names, styles, remaining = [], {}, CSS_LIMIT * 2
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r|") as t:
                for member in t:
                    names.append(member.name)
                    if (
                        member.isfile()
                        and _css_root(member.name)
                        and member.size <= min(CSS_LIMIT, remaining)
                    ):
                        styles[member.name] = t.extractfile(member).read().decode(errors="replace")
                        remaining -= member.size
        except (tarfile.TarError, EOFError, OSError):
            complete = False  # ran out of the part we read: what we saw is still useful
        return names + _cinnamon_markers(names, styles, complete), complete
    return [], False


# ---------------------------------------------------------------- cached lookups


def _load():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def cached(item_id, filename):
    entry = _load().get(f"{item_id}:{filename}")
    if not entry:
        return None
    parts = set(entry["parts"])
    if not parts and not entry["complete"] and entry.get("evidence_version", 1) < 2:
        return None  # Older releases cached failed requests as empty inspections.
    if not entry["complete"] and time.time() - entry.get("cinnamon_checked_at", 0) >= 600:
        return None
    if "desktop" in parts:
        from .desktop import cinnamon_filter_active

        if cinnamon_filter_active() and (
            not parts & CINNAMON_MARKERS
            or time.time() - entry.get("cinnamon_checked_at", 0) > 7 * 86400
        ):
            return None
    return parts, entry["complete"]


def contents(item_id, url, filename, use_cache=True, inspect_css=None, write_cache=True):
    """(parts, complete) for a download, from cache or by peeking. Blocking; call from a worker."""
    hit = cached(item_id, filename) if use_cache else None
    if hit is not None:
        return hit
    try:
        for budget in TAR_BUDGETS:
            names, complete = list_archive(url, filename, budget)
            parts = classify_names(names)
            if any(
                re.search(r"\.(zip|tar|tgz|txz|tbz2?|7z|tar\.(gz|xz|bz2|zst))$", n, re.I)
                for n in names
            ):
                complete = False  # an outer listing cannot prove what's in nested archives
            if inspect_css is None:
                from .desktop import cinnamon_filter_active

                check_css = cinnamon_filter_active()
            else:
                check_css = inspect_css

            needs_css = (
                check_css
                and "desktop" in parts
                and not parts & {"cinnamon-modern", "cinnamon-legacy"}
            )
            if complete or (parts - {"wallpapers"} and not needs_css):
                break
    except RateLimited:
        raise  # A server cooldown is not an inspection result and must not be cached.
    except (requests.RequestException, OSError, zipfile.BadZipFile, ValueError) as exc:
        # zipfile turns OSErrors (RateLimited and HTTPError included) from the end-record read
        # into BadZipFile, so look through the chain for what actually went wrong.
        cause = exc
        while cause is not None:
            if isinstance(cause, RateLimited):
                raise cause from None
            response = getattr(cause, "response", None)
            if isinstance(cause, requests.HTTPError) and response is not None:
                if response.status_code in EXPIRED_STATUSES:
                    raise LinkExpired(
                        f"Download link refused (HTTP {response.status_code})."
                    ) from exc
                break
            cause = cause.__cause__ or cause.__context__
        raise InspectionFailed(
            "Could not inspect the download; no compatibility evidence obtained."
        ) from exc
    if "desktop" in parts and not parts & CINNAMON_MARKERS:
        parts.add("cinnamon-unknown")
    if not complete and parts == {"wallpapers"}:
        parts = set()  # only saw pictures at the start: could be a theme's previews, so don't guess
    if write_cache:
        with _lock:
            data = _load()
            data[f"{item_id}:{filename}"] = {
                "parts": sorted(parts),
                "complete": complete,
                "cinnamon_checked_at": time.time(),
                "evidence_version": 2,
            }
            try:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                tmp = CACHE.with_suffix(".tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(CACHE)
            except OSError:
                pass
    return parts, complete


def installed_parts(entry):
    """Files already installed are stronger evidence than an incomplete remote listing."""
    from . import desktop

    parts = {p for c in entry["components"] for p in desktop.compatible_parts(c)}
    if "gtk" in parts:
        parts.add("gtk-3.0")
    if "desktop" in parts and desktop.current_desktop() == "cinnamon":
        parts.update({"cinnamon-modern", "cinnamon-pre54"})
    return parts


def inspect_downloads(item, kind, installed=None):
    """Inspect download variants and add successful checks to the local index.

    Returns (item, results): item is refreshed if its download links had expired, and results
    maps each file index to (parts, complete), or None when that file could not be checked."""
    from . import compatibility, desktop
    import sqlite3
    import sys

    best = item.best_file()
    if best is None:
        return item, {}
    index = compatibility.Index()
    context = compatibility.context()
    results = {}
    limited = None
    for name in [best.name, *(file.name for file in item.files if file != best)]:
        file = next((f for f in item.files if f.name == name), None)
        if file is None:
            continue  # dropped from the refreshed item
        entry = (installed or {}).get(item.id)
        if (
            kind == "packs"
            and entry
            and entry.get("file") == file.name
            and entry.get("changed") == item.changed
        ):
            parts = installed_parts(entry)
            if desktop.archive_compatible(parts, False, kind):
                result = (parts, False)
                try:
                    index.record(
                        item,
                        file,
                        result,
                        kind,
                        context,
                        "compatible",
                        basis="installed-components",
                    )
                except (OSError, sqlite3.Error):
                    pass
                return item, {file.index: result}
        try:
            result = index.inspection(item, file)
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"drape: compatibility index unavailable: {exc}", file=sys.stderr)
            result = None
        # The legacy cache lacks a checksum/revision key, so it must not seed this
        # index with stale evidence for a newly uploaded file of the same name.
        if result is None:
            try:
                item, result = contents_refreshing(item, file, use_cache=False)
            except RateLimited as exc:
                item = exc.item or item
                results[file.index] = None
                limited = exc
                continue
            except InspectionFailed:
                results[file.index] = None
                continue
            file = _file_named(item, file.name)
        results[file.index] = result
        status = desktop.archive_status(*result, kind)
        try:
            index.record(item, file, result, kind, context, status)
        except (OSError, sqlite3.Error) as exc:
            print(f"drape: could not save compatibility evidence: {exc}", file=sys.stderr)
        if status == "compatible":
            return item, results
    if limited is not None:
        raise RateLimited(limited.retry_after, checks=results, host=limited.host, item=item)
    return item, results
