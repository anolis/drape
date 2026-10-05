"""Bounded headless archive inspection for batch jobs and visible-card checks."""

import argparse
import json
import resource
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import compatibility, http, peek, pling


def scan_item(item, index):
    """Record raw archive evidence, not a verdict for the worker machine's desktop."""
    failures = 0
    for original in list(item.files):
        file = next(
            (candidate for candidate in item.files if candidate.name == original.name), None
        )
        if file is None:
            failures += 1
            continue
        if index.inspection(item, file) is not None:
            continue
        try:
            host, remaining = index.scan_cooldown()
            if remaining:
                raise peek.RateLimited(remaining, host=host)
            try:
                result = peek.contents(
                    item.id,
                    file.url,
                    file.name,
                    use_cache=False,
                    inspect_css=True,
                    write_cache=False,
                )
            except peek.LinkExpired:
                item = peek.fresh_item(item)
                file = next(
                    (candidate for candidate in item.files if candidate.name == file.name), None
                )
                if file is None:
                    raise peek.InspectionFailed("Download no longer listed")
                result = peek.contents(
                    item.id,
                    file.url,
                    file.name,
                    use_cache=False,
                    inspect_css=True,
                    write_cache=False,
                )
            index.record(item, file, result, "archive", {"scanner": "headless"}, "unknown")
        except peek.RateLimited as exc:
            index.defer_scans(exc.host, exc.retry_after)
            print(
                f"{item.id}: rate-limited; retry after {round(exc.retry_after)}s", file=sys.stderr
            )
            return failures + 1
        except (peek.InspectionFailed, MemoryError):
            print(f"{item.id}: inspection failed; no evidence saved", file=sys.stderr)
            failures += 1
    return failures


def inspect_one(payload, index):
    """IPC checks one file; only successfully inspected evidence reaches SQLite."""
    data = payload["item"]
    data["files"] = [pling.Download(**file) for file in data["files"]]
    item = pling.Item(**data)
    file = item.files[0]
    cached = index.inspection(item, file)
    if cached is not None:
        return {"status": "cached"}
    host, remaining = index.scan_cooldown()
    if remaining:
        return {
            "status": "rate_limited",
            "retry_after": remaining,
            **({"host": host} if host else {}),
        }
    try:
        try:
            result = peek.contents(
                item.id, file.url, file.name, use_cache=False, inspect_css=True, write_cache=False
            )
        except peek.LinkExpired:
            item = peek.fresh_item(item)
            file = next((f for f in item.files if f.name == file.name), None)
            if file is None:
                raise peek.InspectionFailed("Download no longer listed")
            result = peek.contents(
                item.id, file.url, file.name, use_cache=False, inspect_css=True, write_cache=False
            )
        index.record(item, file, result, "archive", {"scanner": "headless"}, "unknown")
        # Return refreshed metadata so the card uses the same cache revision.
        return {"status": "checked", "changed": item.changed, "file": asdict(file)}
    except peek.RateLimited as exc:
        # Write before returning IPC: scrolling may cancel the parent callback.
        index.defer_scans(exc.host, exc.retry_after)
        return {
            "status": "rate_limited",
            "retry_after": exc.retry_after,
            **({"host": exc.host} if exc.host else {}),
        }
    except (peek.InspectionFailed, MemoryError):
        return {"status": "failed"}


def scan_installed(manifest, index, progress=None, stats=None):
    """Backfill existing installations without catalog or file-host requests."""
    from . import local_inspection
    from .records import ManifestStore

    entries = ManifestStore(manifest).load()
    cached = index.installed_fingerprints()
    stats = stats if stats is not None else {}
    stats.update(reused=0, scanned=0)
    count = 0
    if progress:
        progress(0, len(entries), "")
    for item_id, entry in entries.items():
        signature = local_inspection.fingerprint(entry)
        reuse = signature is not None and cached.get(item_id) == signature
        if progress:
            title = entry.get("title", item_id)
            progress(
                count,
                len(entries),
                ("Using cached evidence — " if reuse else "Inspecting — ") + title,
            )
        if reuse:
            stats["reused"] += 1
        else:
            paths = [
                component["path"]
                for component in entry.get("components", [])
                if component.get("path")
            ]
            parts, _ = local_inspection.inspect_paths(paths)
            # A concurrent edit during inspection must not be labeled as a reusable snapshot.
            if signature != local_inspection.fingerprint(entry):
                signature = None
            index.record_installed(item_id, entry, parts, signature)
            stats["scanned"] += 1
        count += 1
        if progress:
            progress(count, len(entries), "")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progress-file", type=Path, help="local inspection progress snapshot")
    parser.add_argument("--installed-local", type=Path, help="backfill installed theme evidence")
    parser.add_argument("--inspect-json", action="store_true", help="inspect one file from stdin")
    parser.add_argument("--request-interval", type=float, default=0)
    parser.add_argument("ids", nargs="*", help="public Pling item IDs to inspect")
    parser.add_argument(
        "--kind", choices=[kind.key for kind in pling.KINDS], help="inspect a catalog category"
    )
    parser.add_argument("--query", default="")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--database", type=Path, default=compatibility.PATH)
    parser.add_argument("--output", type=Path, default=Path("compatibility.json"))
    parser.add_argument(
        "--memory-mib", type=int, default=512, help="address-space limit for this process"
    )
    parser.add_argument("--interval", type=float, default=1, help="seconds between items")
    args = parser.parse_args(argv)
    if (
        not (args.ids or args.kind or args.inspect_json or args.installed_local)
        or not 1 <= args.pages <= 100
        or args.memory_mib < 128
        or args.interval < 0
        or args.request_interval < 0
    ):
        parser.error(
            "Provide item IDs or --kind; pages must be 1–100, memory at least 128 MiB and interval nonnegative"
        )
    # Bound this process independently of the desktop app, including archive decompression.
    limit = args.memory_mib * 1024 * 1024
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    limit = min(limit, hard) if hard != resource.RLIM_INFINITY else limit
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    index = compatibility.Index(args.database)
    if args.installed_local:
        try:
            stats = {}

            def progress(done, total, title):
                if args.progress_file:
                    # A small atomic snapshot cannot block the worker on a full UI pipe.
                    temporary = args.progress_file.with_suffix(".tmp")
                    temporary.write_text(
                        json.dumps(
                            {"done": done, "total": total, "title": str(title)[:200], **stats}
                        )
                    )
                    temporary.replace(args.progress_file)

            count = scan_installed(args.installed_local, index, progress, stats)
            print(json.dumps({"status": "checked", "count": count}))
            return 0
        except (ValueError, OSError) as exc:
            print(f"worker: local inspection failed: {exc}", file=sys.stderr)
            return 1
    http.request_interval = args.request_interval
    if args.inspect_json:
        try:
            payload = json.loads(sys.stdin.read(49153))
            result = inspect_one(payload, index)
        except (ValueError, KeyError, TypeError, OSError, pling.PlingError):
            result = {"status": "failed"}
        encoded = json.dumps(result)
        print(encoded if len(encoded.encode()) < 4096 else '{"status": "failed"}')
        return 0
    failures, seen = 0, set()
    try:

        def items():
            for item_id in args.ids:
                yield pling.get(item_id)
            if args.kind:
                for page in range(args.pages):
                    batch, _ = pling.search(args.kind, args.query, "new", page, 30)
                    yield from batch
                    if not batch:
                        break

        for item in items():
            if item.id in seen:
                continue
            if seen:
                time.sleep(args.interval)
            seen.add(item.id)
            failures += scan_item(item, index)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(index.export(), indent=2) + "\n")
        temporary.replace(args.output)
    except (pling.PlingError, ValueError, OSError) as exc:
        print(f"worker: {exc}", file=sys.stderr)
        return 1
    print(
        f"Inspected {len(seen)} items; {failures} deferred/failed checks. Snapshot: {args.output}"
    )
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
