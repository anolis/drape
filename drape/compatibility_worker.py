"""Ad-hoc, headless archive inspection. Never imported or launched by the GTK app."""

import argparse
import json
from pathlib import Path
import resource
import sys
import time

from . import compatibility, peek, pling


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
            print(
                f"{item.id}: rate-limited; retry after {round(exc.retry_after)}s", file=sys.stderr
            )
            return failures + 1
        except (peek.InspectionFailed, MemoryError):
            print(f"{item.id}: inspection failed; no evidence saved", file=sys.stderr)
            failures += 1
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
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
        not (args.ids or args.kind)
        or not 1 <= args.pages <= 100
        or args.memory_mib < 128
        or args.interval < 0
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
