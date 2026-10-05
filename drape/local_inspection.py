"""Bounded inspection of extracted downloads and installed files, without HTTP."""

import hashlib
import json
import os
from pathlib import Path

from . import peek

MAX_PATHS = 30000


def fingerprint(entry):
    """Cheap identity/structure/Cinnamon-style metadata; no archive or asset reads."""
    identity = [entry.get(key, "") for key in ("file", "changed", "download_md5")]
    identity.append(entry.get("components", []))
    candidates = set()
    for component in entry.get("components", []):
        if not component.get("path"):
            continue
        root = Path(component["path"])
        candidates.add(root)
        try:
            if root.is_dir():
                # Changes to engine roots, indexes and paired Qt files affect classification.
                children = list(root.iterdir())
                if len(children) > 4096:
                    return None
                candidates.update(children)
                for folder in children:
                    if (
                        folder.name in ("gtk-2.0", "gtk-3.0", "gtk-4.0", "metacity-1", "xfwm4")
                        and folder.is_dir()
                    ):
                        candidates.update(folder.iterdir())
                    if folder.name == "cinnamon" and folder.is_dir():
                        for directory, folders, files in os.walk(folder, followlinks=False):
                            candidates.add(Path(directory))
                            folders[:] = [
                                name
                                for name in folders
                                if not (Path(directory) / name).is_symlink()
                            ]
                            candidates.update(
                                Path(directory) / name for name in files if name.endswith(".css")
                            )
                            if len(candidates) > 4096:
                                return None
                if len(candidates) > 4096:
                    return None
        except OSError:
            return None
    metadata = []
    for path in sorted(candidates):
        try:
            stat = path.stat()
            metadata.append(
                (str(path), stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            )
        except FileNotFoundError:
            metadata.append((str(path), "missing"))
        except OSError:
            return None
    return hashlib.sha256(json.dumps([identity, metadata], sort_keys=True).encode()).hexdigest()


def inspect_paths(paths):
    """Return raw evidence; missing, unreadable or truncated trees stay incomplete."""
    names, styles = [], {}
    complete, remaining = True, peek.CSS_LIMIT * 2
    for source in paths:
        source = Path(source)
        if not source.exists():
            complete = False
            continue
        if source.is_file():
            names.append(source.name)
            continue

        def failed(_error):
            nonlocal complete
            complete = False

        for directory, folders, files in os.walk(source, followlinks=False, onerror=failed):
            directory = Path(directory)
            folders[:] = [name for name in folders if not (directory / name).is_symlink()]
            for name in folders + files:
                path = directory / name
                relative = source.name + "/" + path.relative_to(source).as_posix()
                names.append(relative)
                if len(names) >= MAX_PATHS:
                    return peek.classify_names(
                        names + peek._cinnamon_markers(names, styles, False)
                    ), False
                if peek._css_root(relative) and relative.lower().endswith(".css"):
                    try:
                        # Do not read symlinks pointing outside the installed/extracted root.
                        if not path.resolve().is_relative_to(source.resolve()):
                            continue
                        with path.open("rb") as stream:
                            data = stream.read(min(peek.CSS_LIMIT, remaining) + 1)
                        if len(data) <= min(peek.CSS_LIMIT, remaining):
                            styles[relative] = data.decode("utf-8", errors="replace")
                            remaining -= len(data)
                    except OSError:
                        complete = False
    return peek.classify_names(names + peek._cinnamon_markers(names, styles, complete)), complete
