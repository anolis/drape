"""Minimal client for the Pling / gnome-look.org OCS API."""

import re
from dataclasses import dataclass, field

import requests

API = "https://api.pling.com/ocs/v1"
USER_AGENT = "drape/0.1 (+https://github.com/anolis/drape)"


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    categories: str  # comma-separated OCS category ids


# The pieces of a desktop look that can be installed independently.
KINDS = [
    Kind("icons", "Icons", "132"),
    Kind("cursors", "Cursors", "107"),
    Kind("gtk", "Controls", "135"),
    Kind("wm", "Window borders", "125"),
    Kind("desktop", "Desktop", "133"),
    Kind("wallpapers", "Wallpapers", "295,261,58,300,283,302,303,360"),
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
                files.append(Download(
                    index=i,
                    name=d.get(f"downloadname{i}") or f"file{i}",
                    url=d[f"downloadlink{i}"],
                    size_kb=int(d.get(f"downloadsize{i}") or 0),
                    md5=d.get(f"downloadmd5sum{i}") or "",
                ))
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
        )


class PlingError(Exception):
    pass


def _get(path, params=None):
    params = dict(params or {}, format="json")
    try:
        r = requests.get(f"{API}/{path}", params=params, timeout=20,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise PlingError(f"Could not reach gnome-look.org: {e}") from e
    if data.get("status") != "ok":
        raise PlingError(data.get("message") or "gnome-look.org returned an error")
    return data


def search(kind, query="", sort="top", page=0, pagesize=30):
    """Return (items, total) for one kind of content."""
    data = _get("content/data", {
        "categories": KINDS_BY_KEY[kind].categories,
        "search": query,
        "sortmode": SORT_MODES.get(sort, "top"),
        "page": page,
        "pagesize": pagesize,
    })
    items = [Item.from_ocs(d) for d in data.get("data") or []]
    return items, int(data.get("totalitems") or 0)


def get(item_id):
    """Fetch one item fresh; download links are signed and expire, so always re-fetch before installing."""
    data = _get(f"content/data/{item_id}")
    rows = data.get("data") or []
    if not rows:
        raise PlingError(f"No item with id {item_id}")
    return Item.from_ocs(rows[0])
