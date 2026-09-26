"""Download, unpack, classify and install theme content, keeping a manifest for removal."""

import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from . import wincursors
from .pling import USER_AGENT

HOME = Path.home()
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")

ICONS_DIR = DATA_HOME / "icons"
# libXcursor on older systems only looks in ~/.icons, so cursors go there
CURSORS_DIR = HOME / ".icons"
THEMES_DIR = HOME / ".themes"
WALLPAPER_DIR = DATA_HOME / "backgrounds" / "drape"
MANIFEST = DATA_HOME / "drape" / "installed.json"
CINNAMON_BG_FOLDERS = CONFIG_HOME / "cinnamon" / "backgrounds" / "user-folders.lst"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".svg", ".bmp", ".jxl", ".avif"}
# theme sub-directories -> what they provide
THEME_PARTS = {
    "gtk-3.0": "gtk", "gtk-4.0": "gtk", "gtk-2.0": "gtk",
    "metacity-1": "wm", "cinnamon": "desktop", "xfwm4": "xfwm",
}
MAX_NESTING = 2


class InstallError(Exception):
    pass


@dataclass
class Component:
    """One installable thing found inside a download."""
    provides: tuple  # e.g. ("icons",), ("gtk", "wm", "desktop"), ("wallpapers",)
    path: Path       # directory (or image file) in the extraction area
    name: str        # name it is installed and applied under
    windows: bool = False  # a Windows .cur/.ani pack that needs converting


# ---------------------------------------------------------------- manifest

def load_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}


def save_manifest(m):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    tmp.replace(MANIFEST)


def _owner_of(path, manifest):
    for key, entry in manifest.items():
        if str(path) in entry.get("paths", []):
            return key
    return None


# ---------------------------------------------------------------- download

def download(url, dest_dir, filename=None, md5=None, progress=None):
    headers = {"User-Agent": USER_AGENT}
    try:
        with requests.get(url, stream=True, timeout=30, headers=headers) as r:
            r.raise_for_status()
            if not filename:
                cd = r.headers.get("content-disposition", "")
                m = re.search(r'filename="?([^";]+)', cd)
                filename = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
            out = Path(dest_dir) / Path(filename).name
            # Content-Length may be missing (chunked/gzip) - progress just goes indeterminate
            total = int(r.headers.get("content-length") or 0)
            done = 0
            h = hashlib.md5()
            with open(out, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise InstallError("gnome-look.org no longer has this file - the upload is broken. "
                               "Try another download for this item or let its author know.") from e
        raise InstallError(f"Download failed (HTTP {e.response.status_code if e.response is not None else '?'}).") from e
    except requests.RequestException as e:
        raise InstallError(f"Download failed: {e.__class__.__name__}. Check your connection.") from e
    with open(out, "rb") as f:
        head = f.read(512).lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        raise InstallError("This download is a web page, not a theme file - the upload is probably broken. "
                           "Try another variant of this item.")
    if md5 and h.hexdigest() != md5:
        raise InstallError("Downloaded file is corrupt (checksum mismatch). Try again.")
    return out


# ---------------------------------------------------------------- extraction

def _is_archive(p):
    return p.is_file() and (tarfile.is_tarfile(p) or zipfile.is_zipfile(p) or p.suffix.lower() in (".7z", ".rar"))


def _safe_member(member, path):
    """tarfile's 'data' filter, except a bad member (e.g. a symlink to the author's own
    /home/...) is skipped instead of failing the whole install."""
    try:
        return tarfile.data_filter(member, path)
    except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
        return None


def extract(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)  # zipfile strips absolute paths and '..'
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter=_safe_member)
    elif archive.suffix.lower() in (".7z", ".rar") and shutil.which("7z"):
        subprocess.run(["7z", "x", "-y", f"-o{dest}", str(archive)],
                       check=True, capture_output=True)
    else:
        raise InstallError(f"Unsupported archive format: {archive.name}")


def unpack_all(path, work):
    """Extract `path` (and archives nested inside it) under `work`; return the root to scan."""
    if path.suffix.lower() in IMAGE_EXTS and not _is_archive(path):
        root = work / "unpacked"
        root.mkdir()
        shutil.copy2(path, root / path.name)
        return root
    root = work / "unpacked"
    extract(path, root)
    for _ in range(MAX_NESTING):
        nested = [p for p in root.rglob("*") if _is_archive(p) and p.suffix.lower() not in IMAGE_EXTS]
        if not nested:
            break
        for p in nested:
            extract(p, p.with_name(p.name + ".d"))
            p.unlink()
    return root


# ---------------------------------------------------------------- classification

def _icon_theme_section(d):
    idx = d / "index.theme"
    if not idx.is_file():
        return None
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(idx.read_text(errors="replace"))
    except configparser.Error:
        return None
    return cp["Icon Theme"] if cp.has_section("Icon Theme") else None


def _classify_dir(d):
    """Return a tuple of what directory `d` provides, or None if it is not a theme root."""
    # [Icon Theme] is checked first: some icon packs ship extra gtk-3.0/ tweaks inside
    section = _icon_theme_section(d)
    # only real Xcursor files count - Windows packs also ship a cursors/ folder
    has_cursors = (d / "cursors").is_dir() and any(wincursors.is_xcursor(p) for p in (d / "cursors").iterdir())
    if section is not None:
        # a cursor theme is an icon theme whose only content is cursors/
        other = [c for c in d.iterdir() if c.is_dir() and c.name != "cursors"]
        if has_cursors and not other:
            return ("cursors",)
        return ("icons", "cursors") if has_cursors else ("icons",)
    if has_cursors:
        return ("cursors",)
    parts = sorted({v for k, v in THEME_PARTS.items() if (d / k).is_dir()})
    return tuple(parts) or None


def _sanitize(name):
    name = re.sub(r"[^\w.+ -]", "_", name).strip(" .")
    return name or "theme"


def classify(root, fallback_name):
    components = []

    def walk(d):
        kind = _classify_dir(d)
        if kind:
            name = d.name if d != root else _sanitize(fallback_name)
            components.append(Component(kind, d, name.removesuffix(".d")))
            return
        if sum(1 for p in d.iterdir() if wincursors.is_windows_cursor(p)) >= 3:
            # name a bare "cursors" folder after the pack that contains it
            named = d.parent if d.name.lower() in ("cursors", "cursor") and d != root else d
            name = named.name if named != root else _sanitize(fallback_name)
            components.append(Component(("cursors",), d, name.removesuffix(".d"), windows=True))
            return
        for c in sorted(d.iterdir()):
            if c.is_dir() and not c.is_symlink() and not c.name.startswith("__MACOSX"):
                walk(c)

    walk(root)
    if not components:
        images = [p for p in sorted(root.rglob("*"))
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS and "__MACOSX" not in p.parts]
        if images:
            components = [Component(("wallpapers",), p, p.name) for p in images]
    return components


# ---------------------------------------------------------------- install / remove

def _dest_for(comp, wallpaper_dir):
    if comp.provides == ("wallpapers",):
        return wallpaper_dir / comp.name
    if "icons" in comp.provides:
        return ICONS_DIR / comp.name
    if comp.provides == ("cursors",):
        return CURSORS_DIR / comp.name
    return THEMES_DIR / comp.name


def _register_wallpaper_folder(folder):
    """Make Cinnamon's Backgrounds settings list the folder."""
    try:
        CINNAMON_BG_FOLDERS.parent.mkdir(parents=True, exist_ok=True)
        lines = CINNAMON_BG_FOLDERS.read_text().splitlines() if CINNAMON_BG_FOLDERS.exists() else []
        if str(folder) not in lines:
            lines.append(str(folder))
            CINNAMON_BG_FOLDERS.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def install_file(path, key, title, changed="", source="", replace_foreign=False, file="", preview=""):
    """Install from a local file. `key` identifies the entry in the manifest."""
    manifest = load_manifest()
    with tempfile.TemporaryDirectory(prefix="drape-") as work:
        root = unpack_all(Path(path), Path(work))
        comps = classify(root, title)
        if not comps:
            raise InstallError("Couldn't find an icon theme, cursor theme, GTK/window/desktop theme or images in this download.")

        wall_dir = WALLPAPER_DIR / _sanitize(title)
        installed, provides = [], []
        for c in comps:
            dest = _dest_for(c, wall_dir)
            if dest.exists() or dest.is_symlink():
                owner = _owner_of(dest, manifest)
                if owner not in (None, key):
                    raise InstallError(f"'{dest.name}' is already installed by another item ({manifest[owner]['title']}).")
                if owner is None and not replace_foreign:
                    raise InstallError(f"'{dest}' already exists and wasn't installed by drape.")
                shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if c.windows:
                try:
                    wincursors.convert_theme(c.path, dest, c.name)
                except wincursors.ConversionError as e:
                    shutil.rmtree(dest, ignore_errors=True)
                    raise InstallError(str(e)) from e
            elif c.path.is_dir():
                shutil.copytree(c.path, dest, symlinks=True)
            else:
                shutil.copy2(c.path, dest)
            installed.append(str(dest))
            provides.append({"provides": list(c.provides), "name": c.name, "path": str(dest)})
            if "icons" in c.provides and shutil.which("gtk-update-icon-cache"):
                subprocess.run(["gtk-update-icon-cache", "-q", "-f", "-t", str(dest)],
                               capture_output=True)

        if any(c.provides == ("wallpapers",) for c in comps):
            _register_wallpaper_folder(wall_dir)
            installed.append(str(wall_dir))

    # drop paths from a previous version of this item that the new version no longer has
    old = manifest.get(key, {})
    for p in set(old.get("paths", [])) - set(installed):
        _remove_path(Path(p))

    manifest[key] = {
        "title": title,
        "changed": changed,
        "source": source,
        "file": file,
        "preview": preview,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "paths": installed,
        "components": provides,
    }
    save_manifest(manifest)
    return manifest[key]


def install_item(item, file_index=None, progress=None, replace_foreign=False):
    """Install a pling.Item. The item should be freshly fetched (download links expire)."""
    if not item.files:
        raise InstallError("This item has no direct downloads (it may link to an external site).")
    f = next((f for f in item.files if f.index == file_index), None) or item.best_file()
    with tempfile.TemporaryDirectory(prefix="drape-dl-") as tmp:
        path = download(f.url, tmp, f.name, f.md5, progress)
        return install_file(path, item.id, item.name, item.changed, item.page,
                            replace_foreign, file=f.name,
                            preview=item.previews[0] if item.previews else "")


def install_url(url, progress=None, replace_foreign=False):
    """Install from an ocs://install?url=...&filename=... link (gnome-look "Install" buttons) or a
    plain download URL. Returns (manifest key, entry)."""
    u = urllib.parse.urlparse(url)
    filename = None
    if u.scheme in ("ocs", "ocss"):
        q = urllib.parse.parse_qs(u.query)
        url, filename = q.get("url", [""])[0], q.get("filename", [None])[0]
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise InstallError("This link doesn't contain a download address.")
    with tempfile.TemporaryDirectory(prefix="drape-dl-") as tmp:
        path = download(url, tmp, filename, progress=progress)
        key = f"url:{path.name}"
        title = re.sub(r"(\.(tar|zip|tgz|gz|xz|bz2|zst|7z))+$", "", path.name, flags=re.I)
        return key, install_file(path, key, title, source=url,
                                 replace_foreign=replace_foreign, file=path.name)


def set_preview(key, url):
    """Record a preview image for an entry installed before previews were tracked."""
    m = load_manifest()
    if key in m:
        m[key]["preview"] = url
        save_manifest(m)


def _remove_path(p):
    # only ever delete inside the directories we install into
    allowed = (ICONS_DIR, CURSORS_DIR, THEMES_DIR, WALLPAPER_DIR)
    if not any(p.is_relative_to(a) and p != a for a in allowed):
        return
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists() or p.is_symlink():
        p.unlink()


def remove(key):
    manifest = load_manifest()
    entry = manifest.pop(key, None)
    if entry is None:
        raise InstallError(f"{key} is not installed")
    for p in entry["paths"]:
        _remove_path(Path(p))
    save_manifest(manifest)
    return entry
