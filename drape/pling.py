"""Pling OCS client shared by GNOME-Look, KDE-Look and Xfce-Look catalogs."""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import requests
from . import http

API = "https://api.pling.com/ocs/v1"
USER_AGENT = "drape/0.1 (+https://github.com/anolis/drape)"


# Catalog data models


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    categories: str  # comma-separated OCS category ids


# The pieces of a desktop look that can be installed independently.
KINDS = [
    Kind("packs", "Theme packs", "135,123,133,125,138,121,722,104,112,114,717"),
    Kind("icons", "Icons", "132"),
    Kind("cursors", "Cursors", "107"),
    Kind("gtk", "Controls", "135"),
    Kind("kvantum", "Qt applications", "123"),
    Kind("wm", "Window borders", "125"),
    Kind("desktop", "Desktop", "133"),
    Kind("lookandfeel", "Global themes", "121,722"),
    Kind("colors", "Color schemes", "112"),
    Kind("wallpapers", "Wallpapers", "295,261,58,300,283,302,303,360"),
    Kind("login", "Login screen", "101,154,131"),
    Kind("boot", "Boot splash", "108"),
]
KINDS_BY_KEY = {k.key: k for k in KINDS}

SORT_MODES = {"top": "top", "new": "new", "downloads": "down", "alpha": "alpha"}


@dataclass
class Download:
    index: int
    name: str
    url: str
    size_kb: int
    md5: str


@dataclass
class Item:
    id: str
    name: str
    author: str
    summary: str
    xdg_type: str
    changed: str
    downloads: int
    score: int
    page: str
    previews: list = field(default_factory=list)
    files: list = field(default_factory=list)
    category: str = ""

    def best_file(self):
        """The download most likely to be the installable theme: an archive or image, not the
        author's drafts/sources. Uploads are often ordered arbitrarily."""

        def rank(f):
            n = f.name.lower()
            return (
                bool(re.search(r"draft|source|src|psd|\bxcf\b|wip|template|for.?modif", n)),
                not re.search(r"\.(tar|tgz|zip|7z|xz|gz|bz2|zst|png|jpe?g|webp|svg)(\.|$)", n),
                f.index,
            )

        return min(self.files, key=rank) if self.files else None

    @classmethod
    def from_ocs(cls, d):
        previews = [d[k] for k in sorted(d) if k.startswith("previewpic") and d[k]]
        files = []
        i = 1
        while f"downloadlink{i}" in d:
            # downloadway 1 == direct file; other values are external links / packages
            if d.get(f"downloadlink{i}") and str(d.get(f"downloadway{i}", "1")) == "1":
                files.append(
                    Download(
                        index=i,
                        name=d.get(f"downloadname{i}") or f"file{i}",
                        url=d[f"downloadlink{i}"],
                        size_kb=int(d.get(f"downloadsize{i}") or 0),
                        md5=d.get(f"downloadmd5sum{i}") or "",
                    )
                )
            i += 1
        return cls(
            id=str(d["id"]),
            name=d.get("name") or "",
            author=d.get("personid") or "",
            summary=d.get("summary") or "",
            xdg_type=d.get("xdg_type") or "",
            changed=d.get("changed") or "",
            downloads=int(d.get("downloads") or 0),
            score=int(d.get("score") or 0),
            page=d.get("detailpage") or f"https://www.pling.com/p/{d['id']}",
            previews=previews,
            files=files,
            category=str(d.get("typeid") or ""),
        )


class PlingError(Exception):
    pass


# OCS requests and catalog parsing


def _get(path, params=None):
    params = dict(params or {}, format="json")
    try:
        # slow connections can take a while to deliver a page; only give up if nothing arrives at all
        r = http.get(
            f"{API}/{path}", params=params, timeout=(15, 90), headers={"User-Agent": USER_AGENT}
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise PlingError(f"Could not reach the Pling theme catalog: {e}") from e
    if data.get("status") != "ok":
        raise PlingError(data.get("message") or "The theme catalog returned an error")
    return data


def thumb_url(url):
    """gnome-look serves every preview at several sizes; cards only need the small one
    (about a quarter of the download)."""
    return url.replace("/cache/770x540-4/", "/cache/280x171-2/") if url else url


CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "drape" / "search"


def _search_params(kind, query, sort, page, pagesize, categories):
    return {
        "categories": categories or KINDS_BY_KEY[kind].categories,
        "search": query,
        "sortmode": SORT_MODES.get(sort, "top"),
        "page": page,
        "pagesize": pagesize,
    }


# Search cache and item lookups


def _cache_file(params):
    return CACHE / (hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")


def _parse(data):
    return [Item.from_ocs(d) for d in data.get("data") or []], int(data.get("totalitems") or 0)


def search(kind, query="", sort="top", page=0, pagesize=30, categories=None):
    """Return (items, total) for one kind of content, fresh from gnome-look (and remembered)."""
    params = _search_params(kind, query, sort, page, pagesize, categories)
    data = _get("content/data", params)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = _cache_file(params).with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(_cache_file(params))
    except OSError:
        pass
    return _parse(data)


def cached_search(
    kind, query="", sort="top", page=0, pagesize=30, categories=None, max_age=7 * 86400
):
    """The last results for this search, if we have them - shown instantly while fresh ones load.
    Download links inside may have expired; installs always re-fetch the item."""
    f = _cache_file(_search_params(kind, query, sort, page, pagesize, categories))
    try:
        if time.time() - f.stat().st_mtime > max_age:
            return None
        return _parse(json.loads(f.read_text()))
    except (OSError, ValueError):
        return None


def get(item_id):
    """Fetch one item fresh; download links are signed and expire, so always re-fetch before installing."""
    data = _get(f"content/data/{item_id}")
    rows = data.get("data") or []
    if not rows:
        raise PlingError(f"No item with id {item_id}")
    return Item.from_ocs(rows[0])


# Public uploader profiles and uploads, independent of theme categories


def profile(author):
    rows = _get(f"person/data/{quote(author, safe='')}").get("data") or []
    if not rows:
        raise PlingError("This uploader's profile is unavailable or private.")
    return rows[0]


def uploads(author, page=0):
    items, total = _parse(
        _get("content/data", {"user": author, "page": page, "pagesize": 20, "sortmode": "new"})
    )
    # Do not show an unrelated catalog if a provider stops honoring its author filter.
    return [item for item in items if item.author.casefold() == author.casefold()], total


def item_kind(item):
    return next(
        (
            kind.key
            for kind in KINDS
            if kind.key != "packs" and item.category in kind.categories.split(",")
        ),
        None,
    )
