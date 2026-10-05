"""Download, unpack, classify and install theme content, keeping a manifest for removal."""

import configparser
import hashlib
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path

import requests

from . import helper, wincursors, kde, http, qt
from .pling import USER_AGENT
from .records import InstallError, ManifestStore

HOME = Path.home()
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")

ICONS_DIR = DATA_HOME / "icons"
# libXcursor on older systems only looks in ~/.icons, so cursors go there
CURSORS_DIR = HOME / ".icons"
THEMES_DIR = HOME / ".themes"
WALLPAPER_DIR = DATA_HOME / "backgrounds" / "drape"
MANIFEST = DATA_HOME / "drape" / "installed.json"
# boot splash / login themes are kept here and copied into /usr/share by the root helper
STAGING_DIR = DATA_HOME / "drape" / "system"
CINNAMON_BG_FOLDERS = CONFIG_HOME / "cinnamon" / "backgrounds" / "user-folders.lst"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".svg", ".bmp", ".jxl", ".avif"}
# theme sub-directories -> what they provide
THEME_PARTS = {
    "gtk-3.0": "gtk",
    "gtk-4.0": "gtk",
    "gtk-2.0": "gtk",
    "metacity-1": "wm",
    "cinnamon": "desktop",
    "xfwm4": "xfwm",
    "gnome-shell": "gnome-shell",
    "openbox-3": "openbox-3",
}
MAX_NESTING = 2


class IncompatibleError(InstallError):
    """The archive has no components usable in the current session."""


class ConflictError(InstallError):
    """Something being installed has the same name as part of another installed item."""

    def __init__(self, owners, title):
        self.owners = owners  # {manifest key: {"title": ..., "names": [clashing names]}}
        self.title = title
        clash = "; ".join(f"{', '.join(o['names'])} (from {o['title']})" for o in owners.values())
        super().__init__(f"{title} has the same name as something already installed: {clash}.")


# Classified components and installation records


@dataclass
class Component:
    """One installable thing found inside a download."""

    provides: tuple  # e.g. ("icons",), ("gtk", "wm", "desktop"), ("wallpapers",)
    path: Path  # directory (or image file) in the extraction area
    name: str  # name it is installed and applied under
    windows: bool = False  # a Windows .cur/.ani pack that needs converting
    qt_name: str | None = None  # original paired-file stem, retained when names collide


# ---------------------------------------------------------------- manifest


def load_manifest():
    return ManifestStore(MANIFEST).load()


def save_manifest(m):
    ManifestStore(MANIFEST).save(m)


def _serialized_files(fn):
    """Keep name checks, copies, removals and record commits in one file operation."""

    @wraps(fn)
    def run(*args, **kwargs):
        _report_status(kwargs.get("status"), "Waiting to install…")
        with ManifestStore(MANIFEST).operations():
            return fn(*args, **kwargs)

    return run


def _owner_of(path, manifest):
    for key, entry in manifest.items():
        if str(path) in entry.get("paths", []):
            return key
    return None


# ---------------------------------------------------------------- download


def _report_status(status, text, fraction=None):
    if status:
        status(text, fraction)


# Download verification and archive extraction


def download(url, dest_dir, filename=None, md5=None, progress=None, status=None):
    _report_status(status, "Downloading…", 0)
    headers = {"User-Agent": USER_AGENT}
    try:
        with http.get(url, stream=True, timeout=30, headers=headers) as r:
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
    except http.RateLimited as e:
        raise InstallError(
            "The download server is rate-limiting requests (HTTP 429). "
            f"Try again in {math.ceil(e.retry_after)} seconds. No theme was installed."
        ) from e
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in http.RETRY_STATUSES:
            host = urllib.parse.urlsplit(e.response.url or url).hostname or "the download server"
            raise InstallError(
                f"{host} returned HTTP {e.response.status_code} after 3 attempts. "
                "The theme file could not be downloaded. Try again later or use the author's "
                "original download; no theme was installed."
            ) from e
        if e.response is not None and e.response.status_code == 404:
            raise InstallError(
                "The theme catalog no longer has this file - the upload is broken. "
                "Try another download for this item or let its author know."
            ) from e
        raise InstallError(
            f"Download failed (HTTP {e.response.status_code if e.response is not None else '?'})."
        ) from e
    except requests.RequestException as e:
        raise InstallError(
            f"Download failed: {e.__class__.__name__}. Check your connection."
        ) from e
    _report_status(status, "Verifying download…", 0.40)
    with open(out, "rb") as f:
        head = f.read(512).lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        raise InstallError(
            "This download is a web page, not a theme file - the upload is probably broken. "
            "Try another variant of this item."
        )
    if md5 and h.hexdigest() != md5:
        raise InstallError("Downloaded file is corrupt (checksum mismatch). Try again.")
    return out


# ---------------------------------------------------------------- extraction


def _is_archive(p):
    return p.is_file() and (
        tarfile.is_tarfile(p) or zipfile.is_zipfile(p) or p.suffix.lower() in (".7z", ".rar")
    )


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
        subprocess.run(
            ["7z", "x", "-y", f"-o{dest}", str(archive)], check=True, capture_output=True
        )
    else:
        raise InstallError(f"Unsupported archive format: {archive.name}")


def unpack_all(path, work, status=None):
    """Extract `path` (and archives nested inside it) under `work`; return the root to scan."""
    if path.suffix.lower() in IMAGE_EXTS | {".colors"} and not _is_archive(path):
        root = work / "unpacked"
        root.mkdir()
        shutil.copy2(path, root / path.name)
        return root
    root = work / "unpacked"
    _report_status(status, "Extracting files…", 0.42)
    extract(path, root)
    for _ in range(MAX_NESTING):
        _report_status(status, "Checking for nested archives…", 0.55)
        nested = [
            p for p in root.rglob("*") if _is_archive(p) and p.suffix.lower() not in IMAGE_EXTS
        ]
        if not nested:
            break
        for p in nested:
            _report_status(status, f"Extracting {p.name}…")
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


# themes that live in system directories and are installed by the root helper
SYSTEM_KINDS = ("plymouth", "sddm", "webgreeter", "gdm")


def _read(p, limit=200_000):
    try:
        with open(p, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _system_kind(d):
    """Boot splash or login screen theme types, recognised by their contents."""
    if any("[Plymouth Theme]" in _read(p) for p in d.glob("*.plymouth")):
        return "plymouth"
    meta = d / "metadata.desktop"
    if (meta.is_file() and "SddmGreeterTheme" in _read(meta)) or (
        (d / "Main.qml").is_file() and ((d / "theme.conf").is_file() or meta.is_file())
    ):
        return "sddm"
    if (d / "index.html").is_file():
        markers = any((d / m).is_file() for m in ("index.yml", "index.theme", "theme.json"))
        scripts = [p for p in d.rglob("*.js") if "node_modules" not in p.parts][:40]
        if (
            markers
            or "lightdm" in _read(d / "index.html")
            or any("lightdm" in _read(js) for js in scripts)
        ):
            return "webgreeter"
    if any(d.glob("*.gresource")) and (
        "gdm" in d.name.lower() or any("gnome-shell" in p.name for p in d.glob("*.gresource"))
    ):
        return "gdm"
    return None


def _classify_dir(d):
    """Return a tuple of what directory `d` provides, or None if it is not a theme root."""
    kind = _system_kind(d)
    if kind:
        return (kind,)
    # [Icon Theme] is checked first: some icon packs ship extra gtk-3.0/ tweaks inside
    section = _icon_theme_section(d)
    # only real Xcursor files count - Windows packs also ship a cursors/ folder
    has_cursors = (d / "cursors").is_dir() and any(
        wincursors.is_xcursor(p) for p in (d / "cursors").iterdir()
    )
    if section is not None:
        # a cursor theme is an icon theme whose only content is cursors/
        other = [c for c in d.iterdir() if c.is_dir() and c.name != "cursors"]
        if has_cursors and not other:
            return ("cursors",)
        return ("icons", "cursors") if has_cursors else ("icons",)
    if has_cursors:
        return ("cursors",)
    kde_kind = kde.classify_dir(d)
    if kde_kind:
        return (kde_kind,)
    parts = sorted({v for k, v in THEME_PARTS.items() if (d / k).is_dir()})
    return tuple(parts) or None


def _sanitize(name):
    name = re.sub(r"[^\w.+ -]", "_", name).strip(" .")
    return name or "theme"


def classify(root, fallback_name, include_wallpapers=False):
    components = []

    def walk(d):
        # Kvantum variants may share a directory; install each matching file pair.
        qt_names = qt.theme_names(d)
        for name in qt_names:
            components.append(Component(("kvantum",), d, name, qt_name=name))
        kind = _classify_dir(d)
        if kind:
            name = d.name if d != root else _sanitize(fallback_name)
            if kind[0] in kde.DIRECTORIES:
                try:
                    name = kde.theme_name(d, name)
                except ValueError as e:
                    raise InstallError(str(e)) from e
            components.append(Component(kind, d, name.removesuffix(".d")))
            # GTK packs often tuck a Qt companion into a Kvantum subfolder.
            for child in sorted(d.iterdir()):
                if (
                    child.is_dir()
                    and not child.is_symlink()
                    and (child.name.lower() == "kvantum" or qt.theme_names(child))
                ):
                    walk(child)
            return
        for file in sorted(d.glob("*.colors")):
            if "[Colors:Window]" in _read(file):
                components.append(Component(("colors",), file, file.stem))
        if sum(1 for p in d.iterdir() if wincursors.is_windows_cursor(p)) >= 3:
            # name a bare "cursors" folder after the pack that contains it
            named = d.parent if d.name.lower() in ("cursors", "cursor") and d != root else d
            name = named.name if named != root else _sanitize(fallback_name)
            components.append(Component(("cursors",), d, name.removesuffix(".d"), windows=True))
            return
        for c in sorted(d.iterdir()):
            if (
                c.is_dir()
                and not c.is_symlink()
                and c.name != ".git"
                and not c.name.startswith("__MACOSX")
            ):
                walk(c)

    walk(root)
    if not components or include_wallpapers:
        # images inside a web page's assets (fonts, css, ...) aren't wallpapers
        skip = {"__MACOSX", ".git", "css", "fonts", "font", "js", "node_modules"}
        images = [
            p
            for p in sorted(root.rglob("*"))
            if p.is_file()
            and p.suffix.lower() in IMAGE_EXTS
            and not skip & set(p.relative_to(root).parts[:-1])
        ]
        if components:
            theme_roots = [c.path for c in components if c.path.is_dir()]
            images = [
                p
                for p in images
                if (
                    re.search(r"wallpaper|background", str(p.relative_to(root)), re.I)
                    or not any(p.is_relative_to(folder) for folder in theme_roots)
                )
                and not re.search(
                    r"preview|screenshot|thumbnail|logo", str(p.relative_to(root)), re.I
                )
            ]
        components.extend(Component(("wallpapers",), p, p.name) for p in images)
    return components


# ---------------------------------------------------------------- install / remove


def _system_name(name):
    """Names the root helper accepts: ASCII, starting with a letter or digit."""
    name = re.sub(r"[^A-Za-z0-9 ._+-]", "_", name).strip(" ._-+")
    return name if name and name[0].isalnum() else f"theme{name}"


def _dest_for(comp, wallpaper_dir):
    if comp.provides == ("kvantum",):
        return qt.THEMES_DIR / comp.name
    if comp.provides[0] in kde.DIRECTORIES:
        folder = kde.DATA_HOME / kde.DIRECTORIES[comp.provides[0]]
        return folder / (comp.name + ".colors" if comp.provides[0] == "colors" else comp.name)
    if comp.provides[0] in SYSTEM_KINDS:
        return STAGING_DIR / comp.provides[0] / _system_name(comp.name)
    if comp.provides == ("wallpapers",):
        return wallpaper_dir / comp.name
    if "icons" in comp.provides:
        return ICONS_DIR / comp.name
    if comp.provides == ("cursors",):
        return CURSORS_DIR / comp.name
    return THEMES_DIR / comp.name


def _unique_names(comps, root, wall_dir):
    """Rename components that would be installed to the same place, e.g. 4k/bg.png and
    1080p/bg.png, so one doesn't overwrite the other."""
    taken, unique = set(), []
    for c in comps:
        stem, suffix = (
            (Path(c.name).stem, Path(c.name).suffix) if c.path.is_file() else (c.name, "")
        )
        candidates = [c.name]
        if c.provides == ("wallpapers",) and c.path.parent != root:
            candidates.append(f"{c.path.parent.name} - {c.name}")
        candidates += (f"{stem}-{n}{suffix}" for n in range(2, len(comps) + 2))
        for name in candidates:
            renamed = replace(c, name=name)
            dest = _dest_for(renamed, wall_dir)
            if dest not in taken:
                taken.add(dest)
                unique.append(renamed)
                break
    return unique


def _delete(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


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


# Validated installation transaction


@_serialized_files
def install_file(
    path,
    key,
    title,
    changed="",
    source="",
    replace_foreign=False,
    file="",
    preview="",
    replace_items=False,
    only_applicable=None,
    status=None,
    required_kind=None,
    author="",
    catalog_download=None,
):
    """Install from a local file. `key` identifies the entry in the manifest."""
    manifest = load_manifest()
    with tempfile.TemporaryDirectory(prefix="drape-") as work:
        root = unpack_all(Path(path), Path(work), status)
        _report_status(status, "Inspecting theme files…", 0.60)
        if file or catalog_download is not None:
            # Inspect the whole download before desktop filtering discards components.
            # This already runs in the installation thread, with no extra archive reads.
            from . import compatibility, local_inspection, pling

            try:
                evidence = local_inspection.inspect_paths([root])
                cached_download = catalog_download or pling.Download(1, file, source, 0, "")
                catalog_item = pling.Item(
                    key, title, author, "", "", changed, 0, 0, source, files=[cached_download]
                )
                compatibility.Index().record(
                    catalog_item,
                    cached_download,
                    evidence,
                    "archive",
                    {"scanner": "downloaded"},
                    "unknown",
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                # Optional cache failure must never turn a successful download into a failed install.
                print(f"drape: could not cache downloaded theme evidence: {exc}")
        comps = classify(root, title, include_wallpapers=required_kind == "packs")
        if not comps:
            raise IncompatibleError(
                "Couldn't find a supported theme, color scheme or wallpaper in this download."
            )

        wall_dir = WALLPAPER_DIR / _sanitize(title)
        installed, provides = [], []
        if any(c.provides == ("gdm",) for c in comps):
            raise InstallError(
                "This is a GDM login theme. Those work by replacing a core GNOME Shell file, which "
                "breaks when GNOME updates, so drape doesn't install them yet."
            )
        from . import desktop, settings

        if required_kind == "libadwaita" and not any(
            "libadwaita"
            in desktop.compatible_parts(
                {"provides": c.provides, "path": str(c.path), "name": c.name}
            )
            for c in comps
        ):
            raise IncompatibleError(
                "This download has no usable GTK 4 stylesheet for native GNOME apps. Nothing was installed."
            )
        if required_kind == "packs":
            only_applicable = True
        if only_applicable is None:
            only_applicable = settings.get("only_applicable")
        skipped = []
        if only_applicable:
            desktop.running_wm(refresh=True)
            usable = []
            reasons = set()
            for c in comps:
                if (
                    c.provides[0] in SYSTEM_KINDS and desktop.system_part_compatible(c.provides[0])
                ) or (
                    c.provides[0] not in SYSTEM_KINDS
                    and desktop.compatible_parts(
                        {"provides": c.provides, "path": str(c.path), "name": c.name}
                    )
                ):
                    usable.append(c)
                else:
                    skipped.append(c.name)
                    if "desktop" in c.provides:
                        reason = desktop.cinnamon_rejection_reason(c.path)
                        if reason:
                            reasons.add(reason)
            comps = usable
            if not comps:
                raise IncompatibleError(
                    (" ".join(sorted(reasons)) + " " if reasons else "")
                    + "This download has no supported themes for "
                    f"{desktop.current_desktop() or 'this desktop'} / "
                    f"{desktop.running_wm() or 'unknown window manager'}. "
                    "Nothing was installed. Turn off 'Hide incompatible themes for this desktop' "
                    "to install themes for another session."
                )
        if required_kind == "packs" and not desktop.pack_components(
            [{"provides": c.provides, "path": str(c.path), "name": c.name} for c in comps]
        ):
            raise IncompatibleError(
                "This download is not a theme pack for the current desktop. "
                "It needs multiple supported appearance parts or a compatible KDE global theme."
            )
        comps = _unique_names(comps, root, wall_dir)
        # check every name before copying anything, so a clash can't leave a half-installed pack
        _report_status(status, "Checking installed themes…", 0.65)
        conflicts, foreign = {}, []
        for c in comps:
            dest = _dest_for(c, wall_dir)
            if dest.exists() or dest.is_symlink():
                owner = _owner_of(dest, manifest)
                if owner not in (None, key):
                    conflicts.setdefault(owner, {"title": manifest[owner]["title"], "names": []})[
                        "names"
                    ].append(dest.name)
                elif owner is None and not replace_foreign:
                    foreign.append(dest)
        if conflicts and not replace_items:
            raise ConflictError(conflicts, title)
        if foreign:
            raise InstallError(f"'{foreign[0]}' already exists and wasn't installed by drape.")
        # system copies with the same name as something this item installs are simply replaced when
        # it's applied (even the active boot splash); any others must be removed first, which needs root
        taking_over = {
            (c.provides[0], _system_name(c.name)) for c in comps if c.provides[0] in SYSTEM_KINDS
        }
        for owner in conflicts:
            leftover = [
                cp for cp in system_copies(manifest[owner]) if (cp[0], cp[1]) not in taking_over
            ]
            if leftover:
                raise InstallError(
                    f"{manifest[owner]['title']} has copies for the login screen or boot splash, "
                    f"which need your password to remove: run `drape remove {owner}` first."
                )
        for owner in conflicts:  # the user chose to replace these
            remove(owner)
        manifest = load_manifest()

        # existing copies are moved aside rather than deleted, so a failure part-way through
        # can put everything back the way it was
        copied, backups = [], []
        try:
            for index, c in enumerate(comps):
                dest = _dest_for(c, wall_dir)
                _report_status(status, f"Installing {c.name}…", 0.68 + 0.22 * index / len(comps))
                if dest.exists() or dest.is_symlink():
                    backup = dest.with_name(f".{dest.name}.drape-old")
                    _delete(backup)
                    dest.rename(backup)
                    backups.append((backup, dest))
                dest.parent.mkdir(parents=True, exist_ok=True)
                copied.append(dest)
                if c.windows:
                    try:
                        wincursors.convert_theme(c.path, dest, c.name)
                    except wincursors.ConversionError as e:
                        raise InstallError(str(e)) from e
                elif c.qt_name:
                    dest.mkdir()
                    for suffix in (".kvconfig", ".svg"):
                        shutil.copy2(c.path / (c.qt_name + suffix), dest / (c.name + suffix))
                    for name in ("preview.png", "screenshot.png"):
                        if (c.path / name).is_file():
                            shutil.copy2(c.path / name, dest / name)
                elif c.path.is_dir():
                    shutil.copytree(
                        c.path, dest, symlinks=True, ignore=shutil.ignore_patterns(".git")
                    )
                else:
                    shutil.copy2(c.path, dest)
                installed.append(str(dest))
                comp = {"provides": list(c.provides), "name": c.name, "path": str(dest)}
                if c.provides[0] in SYSTEM_KINDS:
                    comp["name"] = dest.name
                    comp["system"] = c.provides[
                        0
                    ]  # still needs copying into /usr/share by the helper
                provides.append(comp)
                if "icons" in c.provides and shutil.which("gtk-update-icon-cache"):
                    _report_status(
                        status,
                        f"Updating icon cache for {c.name}…",
                        0.68 + 0.22 * (index + 0.8) / len(comps),
                    )
                    subprocess.run(
                        ["gtk-update-icon-cache", "-q", "-f", "-t", str(dest)], capture_output=True
                    )
        except BaseException:
            for dest in reversed(copied):
                try:
                    _delete(dest)
                except OSError:
                    pass
            for backup, dest in reversed(backups):
                try:
                    backup.rename(dest)
                except OSError:
                    pass
            raise
        for backup, _ in backups:
            _delete(backup)

        if any(c.provides == ("wallpapers",) for c in comps):
            if desktop.current_desktop() == "cinnamon":
                _register_wallpaper_folder(wall_dir)
            installed.append(str(wall_dir))

        _report_status(status, "Cleaning up extracted files…", 0.90)

    # drop paths from a previous version of this item that the new version no longer has
    old = manifest.get(key, {})
    for p in set(old.get("paths", [])) - set(installed):
        _remove_path(Path(p))

    entry = {
        "title": title,
        "changed": changed,
        "source": source,
        "file": file,
        "preview": preview,
        "author": author,
        "download_md5": catalog_download.md5 if catalog_download is not None else "",
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "paths": installed,
        "components": provides,
        "skipped": skipped,
    }
    _report_status(status, "Saving installation…", 0.98)
    with ManifestStore(MANIFEST).edit() as latest:
        # Preview/variant updates can happen during a large file copy. Preserve
        # those edits and all unrelated installations from the latest records.
        current = latest.get(key, {})
        if "chosen" in current:
            entry["chosen"] = current["chosen"]
        if not entry["preview"] and current.get("preview"):
            entry["preview"] = current["preview"]
        latest[key] = entry
    return entry


# Catalog downloads and variant fallback


def install_item(
    item,
    file_index=None,
    progress=None,
    replace_foreign=False,
    replace_items=False,
    only_applicable=None,
    status=None,
    required_kind=None,
):
    """Install a pling.Item. The item should be freshly fetched (download links expire)."""
    if not item.files:
        raise InstallError("This item has no direct downloads (it may link to an external site).")
    if file_index is not None:
        files = [f for f in item.files if f.index == file_index]
        if not files:
            raise InstallError(f"No download numbered {file_index}.")
    else:
        best = item.best_file()
        files = [best] + [f for f in item.files if f != best]
    with tempfile.TemporaryDirectory(prefix="drape-dl-") as tmp:
        for f in files:
            path = download(f.url, tmp, f.name, f.md5, progress, status)
            try:
                return install_file(
                    path,
                    item.id,
                    item.name,
                    item.changed,
                    item.page,
                    replace_foreign,
                    file=f.name,
                    preview=item.previews[0] if item.previews else "",
                    author=item.author,
                    catalog_download=f,
                    replace_items=replace_items,
                    only_applicable=True if required_kind == "packs" else only_applicable,
                    status=status,
                    required_kind=required_kind,
                )
            except IncompatibleError as e:
                last_error = e
        raise last_error


def _url_key(url, filename):
    """Reuse the record of an earlier install from this URL; otherwise pick a key no other URL
    has, since unrelated downloads often share a file name like theme.tar.gz."""
    manifest = load_manifest()
    for key, entry in manifest.items():
        if key.startswith("url:") and entry.get("source") == url:
            return key
    key = f"url:{filename}"
    if key in manifest:
        key += "-" + hashlib.sha1(url.encode()).hexdigest()[:8]
    return key


def install_url(
    url, progress=None, replace_foreign=False, replace_items=False, only_applicable=None
):
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
        key = _url_key(url, path.name)
        title = re.sub(r"(\.(tar|zip|tgz|gz|xz|bz2|zst|7z))+$", "", path.name, flags=re.I)
        return key, install_file(
            path,
            key,
            title,
            source=url,
            replace_foreign=replace_foreign,
            file=path.name,
            replace_items=replace_items,
            only_applicable=only_applicable,
        )


# Record-only metadata updates


def set_chosen(key, kind, name):
    """Remember the variant last picked for a category, so plain Apply re-uses it."""
    with ManifestStore(MANIFEST).edit() as m:
        if key in m:
            m[key].setdefault("chosen", {})[kind] = name


def set_preview(key, url):
    """Record a preview without overwriting unrelated installations or preferences."""
    with ManifestStore(MANIFEST).edit() as m:
        if key in m:
            m[key]["preview"] = url


# Owned-file removal and removal plans


def _remove_path(p):
    # only ever delete inside the directories we install into
    allowed = (
        ICONS_DIR,
        CURSORS_DIR,
        THEMES_DIR,
        WALLPAPER_DIR,
        STAGING_DIR,
        qt.THEMES_DIR,
        *(kde.DATA_HOME / folder for folder in kde.DIRECTORIES.values()),
    )
    if not any(
        p.is_relative_to(a) and p != a and p.parent.resolve().is_relative_to(a.resolve())
        for a in allowed
    ):
        raise InstallError(f"Refusing to delete outside drape's install folders: {p}")
    _delete(p)


def removal_plan(selection, manifest):
    """Resolve card selections; a whole-pack selection supersedes its individual images."""
    selected = set(selection)
    whole = {key for key, path in selected if path is None}
    plan = []
    for key, path in sorted(selected, key=lambda item: (item[0], item[1] or "")):
        if path is not None and key in whole:
            continue
        entry = manifest.get(key)
        if entry is None:
            raise InstallError(f"{key} is no longer installed. Refresh your installed items.")
        if path is not None:
            comps = [c for c in entry["components"] if c["path"] == path]
            if not comps or any(c["provides"] != ["wallpapers"] for c in comps):
                raise InstallError(
                    "The selected wallpaper is no longer installed. Refresh your installed items."
                )
            entry = dict(entry, components=comps, paths=[path])
        plan.append((key, path, entry))
    return plan


@_serialized_files
def remove_component(key, path):
    """Remove one component; other records and concurrent metadata edits are retained."""
    manifest = load_manifest()
    entry = manifest.get(key)
    if entry is None:
        raise InstallError(f"{key} is not installed")
    comps = [c for c in entry["components"] if c["path"] != path]
    if len(comps) == len(entry["components"]):
        raise InstallError(f"{path} is not part of {entry['title']}")
    if not comps:
        return remove(key)
    _remove_path(Path(path))
    with ManifestStore(MANIFEST).edit() as latest:
        entry = latest[key]
        entry["components"] = comps
        entry["paths"] = [p for p in entry["paths"] if p != path]
    return entry


@_serialized_files
def remove(key):
    manifest = load_manifest()
    entry = manifest.get(key)
    if entry is None:
        raise InstallError(f"{key} is not installed")
    for p in entry["paths"]:
        _remove_path(Path(p))
    with ManifestStore(MANIFEST).edit() as latest:
        latest.pop(key, None)
    return entry


# ---------------------------------------------------------------- copies in system directories


def system_file_name(component):
    """File name for a wallpaper copied to /usr/share/backgrounds/drape; includes the pack name,
    since packs often use generic names like 1920x1080.png."""
    p = Path(component["path"])
    stem = f"{p.parent.name} - {p.stem}" if p.parent.parent == WALLPAPER_DIR else p.stem
    return _system_name(stem) + p.suffix.lower()


def system_copies(entry):
    """(helper kind, name, path, label) for every copy drape put in a system directory for this
    entry: boot splash / login themes, and things used for the login screen."""
    found = []
    for c in entry["components"]:
        if c.get("system"):
            path = helper.DIRS[c["system"]] / c["name"]
            if (path / helper.MARKER).exists():
                label = {
                    "plymouth": "Boot splash",
                    "sddm": "SDDM login theme",
                    "webgreeter": "Web greeter login theme",
                }[c["system"]]
                found.append((c["system"], c["name"], path, label + " (system copy)"))
            continue
        for kind in ("gtk", "icons", "background"):
            if kind == "background" and c["provides"] != ["wallpapers"]:
                continue
            name = system_file_name(c) if kind == "background" else c["name"]
            path = helper.DIRS[kind] / name
            marker = (
                Path(str(path) + helper.MARKER) if kind == "background" else path / helper.MARKER
            )
            if marker.exists():
                found.append((kind, name, path, "Login screen copy"))
    return found
