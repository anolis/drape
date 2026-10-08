"""Child-only image-format adapter for GlitchPEG; originals stay untouched."""

import fcntl
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000


def jpeg_copy(source, cache):
    source, cache = Path(source), Path(cache)
    info = source.stat()
    if info.st_size > MAX_BYTES:
        raise ValueError("GlitchPEG's source image exceeds 64 MiB.")
    key = hashlib.sha256(
        f"{source.resolve()}:{info.st_size}:{info.st_mtime_ns}".encode()
    ).hexdigest()
    target = cache / f"image-{key}.jpg"
    if target.is_file():
        return target
    with Image.open(source) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("GlitchPEG's source image exceeds 40 million pixels.")
        if image.format == "JPEG":
            return source
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Serialize conversion and eviction across helper processes/monitors;
        # other monitors reuse the completed copy rather than decoding it again.
        with (cache / "cache.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.is_file():
                return target
            with tempfile.NamedTemporaryFile(dir=cache, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
            try:
                image.convert("RGB").save(temporary, format="JPEG", quality=90)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            # Only adapter-owned derivatives are evicted; source files and
            # XScreenSaver's own image library/cache stay untouched.
            older = sorted(
                cache.glob("image-*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True
            )
            for path in older[8:]:
                if path != target:
                    path.unlink(missing_ok=True)
    return target


def main():
    program = os.environ["DRAPE_IMAGE_HELPER"]
    result = subprocess.run(
        [program, *sys.argv[1:]], stdout=subprocess.PIPE, text=True, timeout=60, check=False
    )
    if result.returncode:
        return result.returncode
    source = result.stdout.strip()
    if not source:
        raise ValueError("GlitchPEG needs an image in XScreenSaver's configured image folder.")
    print(jpeg_copy(source, os.environ["DRAPE_IMAGE_CACHE"]), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - errors go into the owned animation log.
        print(f"Drape image helper: {str(exc)[:600]}", file=sys.stderr)
        sys.exit(1)
