"""drape command line: search, install, apply and remove themes from gnome-look.org."""

import argparse
import sys
from pathlib import Path

from . import desktop, installer, pling


def _progress(done, total):
    if total:
        sys.stderr.write(f"\r  {done * 100 // total:3d}%  {done // 1024} KiB")
    else:
        sys.stderr.write(f"\r  {done // 1024} KiB")
    sys.stderr.flush()


def _print_entry(key, e):
    parts = sorted({p for c in e["components"] for p in c["provides"]})
    print(f"{key:>10}  {e['title']}  [{', '.join(parts)}]")
    for c in e["components"][:6]:
        print(f"{'':12}- {c['name']}")
    if len(e["components"]) > 6:
        print(f"{'':12}  ... {len(e['components']) - 6} more")


def cmd_search(a):
    items, total = pling.search(a.kind, a.query, a.sort, a.page, max(a.limit, 10))
    items = items[:a.limit]  # the API won't return pages smaller than 10
    for it in items:
        print(f"{it.id:>10}  {it.name[:48]:48}  {it.author[:16]:16}  score {it.score:3d}  {it.downloads:>7} dl")
    print(f"\n{len(items)} of {total} {pling.KINDS_BY_KEY[a.kind].label.lower()}", file=sys.stderr)


def cmd_show(a):
    it = pling.get(a.id)
    print(f"{it.name} by {it.author}  ({it.xdg_type}, updated {it.changed[:10]})\n{it.page}\n")
    print(it.summary)
    print("\nDownloads:")
    for f in it.files:
        print(f"  {f.index}: {f.name}  ({f.size_kb} KiB)")


def cmd_install(a):
    it = pling.get(a.id)
    print(f"Installing {it.name}...", file=sys.stderr)
    e = installer.install_item(it, a.file, _progress, a.force)
    print(file=sys.stderr)
    _print_entry(it.id, e)
    if a.apply:
        _apply(e)


def cmd_install_url(a):
    key, e = installer.install_url(a.url, _progress, a.force)
    print(file=sys.stderr)
    _print_entry(key, e)
    if a.apply:
        _apply(e)


def cmd_file(a):
    p = Path(a.path)
    e = installer.install_file(p, f"file:{p.name}", p.name.split(".")[0], source=str(p.resolve()),
                               replace_foreign=a.force, file=p.name)
    _print_entry(f"file:{p.name}", e)


def _apply(entry, name=None):
    comps = [c for c in entry["components"] if name in (None, c["name"])]
    if not comps:
        sys.exit(f"No component named {name!r}")
    applied = []
    seen = set()
    for c in comps:
        # for multi-variant downloads apply the first variant of each part only
        parts = [p for p in c["provides"] if p not in seen]
        applied += desktop.apply_component(c, only=parts)
        seen.update(c["provides"])
    print("Applied: " + (", ".join(applied) or "nothing (unsupported desktop?)"))


def cmd_apply(a):
    m = installer.load_manifest()
    if a.key not in m:
        sys.exit(f"{a.key} is not installed (see `drape list`)")
    _apply(m[a.key], a.name)


def cmd_list(a):
    m = installer.load_manifest()
    if not m:
        print("Nothing installed with drape yet.")
    for k, e in sorted(m.items(), key=lambda kv: kv[1]["title"].lower()):
        _print_entry(k, e)


def cmd_remove(a):
    e = installer.remove(a.key)
    print(f"Removed {e['title']}")


def cmd_updates(a):
    m = installer.load_manifest()
    for k, e in m.items():
        if not k.isdigit():
            continue
        try:
            it = pling.get(k)
        except pling.PlingError as err:
            print(f"{k}: {err}", file=sys.stderr)
            continue
        if it.changed and it.changed != e.get("changed"):
            print(f"{k:>10}  {e['title']}: update available ({e.get('changed', '?')[:10]} -> {it.changed[:10]})")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="drape", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search gnome-look.org")
    s.add_argument("kind", choices=[k.key for k in pling.KINDS])
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--sort", choices=list(pling.SORT_MODES), default="downloads")
    s.add_argument("--page", type=int, default=0)
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("show", help="show details and downloads for an item")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("install", help="install an item by id")
    s.add_argument("id")
    s.add_argument("--file", type=int, help="download number (see `show`), default first")
    s.add_argument("--apply", action="store_true", help="apply after installing")
    s.add_argument("--force", action="store_true", help="overwrite themes not installed by drape")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("install-url", help="install from an ocs:// link or direct URL")
    s.add_argument("url")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_install_url)

    s = sub.add_parser("install-file", help="install from a local archive")
    s.add_argument("path")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_file)

    s = sub.add_parser("apply", help="apply an installed item")
    s.add_argument("key")
    s.add_argument("name", nargs="?", help="which variant, if the item installed several")
    s.set_defaults(func=cmd_apply)

    sub.add_parser("list", help="list installed items").set_defaults(func=cmd_list)

    s = sub.add_parser("remove", help="uninstall an item")
    s.add_argument("key")
    s.set_defaults(func=cmd_remove)

    sub.add_parser("updates", help="check installed items for updates").set_defaults(func=cmd_updates)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except (pling.PlingError, installer.InstallError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
