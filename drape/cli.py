"""Search, install, apply and remove themes from GNOME-Look, KDE-Look and Xfce-Look."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import desktop, installer, pling, settings, peek


# Terminal output helpers


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


# Catalog commands


def cmd_search(a):
    only = a.kind == "packs" or (not a.all_themes and settings.get("only_applicable"))
    categories, note = desktop.scope(a.kind, only)
    if categories == "":
        print(note, file=sys.stderr)
        return
    items, total = pling.search(a.kind, a.query, a.sort, a.page, max(a.limit, 10), categories)
    if only:
        installed = installer.load_manifest() if a.kind == "packs" else None
        visible = []
        for item in items:
            try:
                status = desktop.download_status(
                    list(peek.inspect_downloads(item, a.kind, installed).values()), a.kind
                )
            except peek.RateLimited as exc:
                print(
                    f"{item.name}: compatibility check rate-limited; retry in {int(exc.retry_after) + 1}s.",
                    file=sys.stderr,
                )
                visible.append(item)
            else:
                if status == "compatible":
                    visible.append(item)
        items = visible
    items = items[: a.limit]  # the API won't return pages smaller than 10
    for it in items:
        print(
            f"{it.id:>10}  {it.name[:48]:48}  {it.author[:16]:16}  score {it.score:3d}  {it.downloads:>7} dl"
        )
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
    e = installer.install_item(
        it, a.file, _progress, a.force, _replace(a), only_applicable=False if a.all_themes else None
    )
    print(file=sys.stderr)
    _print_entry(it.id, e)
    if a.apply:
        _apply(e)


def cmd_install_url(a):
    key, e = installer.install_url(
        a.url, _progress, a.force, _replace(a), only_applicable=False if a.all_themes else None
    )
    print(file=sys.stderr)
    _print_entry(key, e)
    if a.apply:
        _apply(e)


def _replace(a):
    """--replace: uninstall items whose theme names clash (the installer refuses if they still have
    system copies, which need root to remove - `drape remove <id>` does that)."""
    return a.replace


def _conflict_hint(e):
    ids = ", ".join(e.owners)
    return f"{e}\nTo uninstall {ids} and install this instead, add --replace."


def cmd_file(a):
    p = Path(a.path)
    e = installer.install_file(
        p,
        f"file:{p.name}",
        p.name.split(".")[0],
        source=str(p.resolve()),
        replace_foreign=a.force,
        file=p.name,
        replace_items=_replace(a),
        only_applicable=False if a.all_themes else None,
    )
    _print_entry(f"file:{p.name}", e)


# Applying installed components


def _apply(entry, name=None):
    comps = [c for c in entry["components"] if name in (None, c["name"])]
    if not comps:
        sys.exit(f"No component named {name!r}")
    system_comps = [c for c in comps if c.get("system")]
    if system_comps:
        _apply_system(system_comps[0])
        return
    applied = []
    seen = set()
    for c in comps:
        # for multi-variant downloads apply the first variant of each part only
        parts = [p for p in c["provides"] if p not in seen]
        changed = desktop.apply_component(c, only=parts)
        applied += changed
        seen.update(changed)
    print("Applied: " + (", ".join(applied) or "nothing (unsupported desktop?)"))


def _apply_system(c):
    """Boot splash / login screen themes: copy into place and apply as root (asks for a password)."""
    from . import system

    kind, name = c["system"], c["name"]
    req = system.requirement(kind)
    if not req.installed:
        how = (
            f"install it with: sudo apt install {req.package}"
            if req.package
            else f"get it from {req.url}"
        )
        sys.exit(f"{name} needs {req.label}, which isn't installed - {how}")
    cmds = [
        ["install", kind, c["path"], "--name", name],
        {
            "plymouth": ["set-plymouth", name],
            "sddm": ["sddm-theme", name],
            "webgreeter": ["web-greeter-theme", name],
        }[kind],
    ]
    if kind == "plymouth":
        print("Rebuilding the boot image takes a minute…", file=sys.stderr)
    r = system.run_helper(*cmds)
    if not r.ok:
        sys.exit(r.output or "failed")
    print(f"Applied: {kind} {name}")
    if not req.active:
        print(
            f"Note: {req.label} isn't your active login screen - switch to it in drape's Lock & login page."
        )


def cmd_apply(a):
    m = installer.load_manifest()
    if a.key not in m:
        sys.exit(f"{a.key} is not installed (see `drape list`)")
    _apply(m[a.key], a.name)


# Installed records and removal


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
            print(
                f"{k:>10}  {e['title']}: update available ({e.get('changed', '?')[:10]} -> {it.changed[:10]})"
            )


def cmd_compatibility(a):
    from .compatibility import Index

    try:
        index = Index()
        if a.operation == "export":
            if a.since < 0:
                raise ValueError("The diff cursor must be nonnegative")
            document = json.dumps(index.export(a.since), indent=2) + "\n"
            if a.output:
                Path(a.output).write_text(document)
            else:
                print(document, end="")
        else:
            count = index.import_records(json.loads(Path(a.path).read_text()))
            print(f"Imported {count} compatibility observations.")
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise installer.InstallError(f"Compatibility index: {exc}") from exc


# Command parsing and dispatch


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
    s.add_argument(
        "--replace",
        action="store_true",
        help="if a theme name clashes with another installed item, uninstall that item",
    )
    s.add_argument("id")
    s.add_argument("--file", type=int, help="download number (see `show`), default first")
    s.add_argument("--apply", action="store_true", help="apply after installing")
    s.add_argument("--force", action="store_true", help="overwrite themes not installed by drape")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("install-url", help="install from an ocs:// link or direct URL")
    s.add_argument(
        "--replace",
        action="store_true",
        help="if a theme name clashes with another installed item, uninstall that item",
    )
    s.add_argument("url")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_install_url)

    s = sub.add_parser("install-file", help="install from a local archive")
    s.add_argument(
        "--replace",
        action="store_true",
        help="if a theme name clashes with another installed item, uninstall that item",
    )
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

    sub.add_parser("updates", help="check installed items for updates").set_defaults(
        func=cmd_updates
    )

    s = sub.add_parser(
        "compatibility", help="export local evidence or import reviewed compatibility data"
    )
    operations = s.add_subparsers(dest="operation", required=True)
    export = operations.add_parser("export")
    export.add_argument("--since", type=int, default=0, help="cursor from the previous export")
    export.add_argument("--output", help="JSON output file (default: stdout)")
    export.set_defaults(func=cmd_compatibility)
    imported = operations.add_parser("import")
    imported.add_argument("path", help="reviewed compatibility JSON file")
    imported.set_defaults(func=cmd_compatibility)

    for name in ("search", "install", "install-url", "install-file"):
        sub.choices[name].add_argument(
            "--all-themes",
            action="store_true",
            help="include themes for other desktops (Apply still checks compatibility)",
        )
    a = ap.parse_args(argv)
    try:
        a.func(a)
    except installer.ConflictError as e:
        sys.exit(f"error: {_conflict_hint(e)}")
    except (pling.PlingError, installer.InstallError, desktop.ApplyError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
