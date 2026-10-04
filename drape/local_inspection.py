"""Bounded inspection of extracted downloads and installed files, without HTTP."""

import os
from pathlib import Path

from . import peek

MAX_PATHS = 30000


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
