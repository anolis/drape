"""Run the CLI after resolving any missing runtime dependencies."""

from .dependencies import ensure

if not ensure():
    raise SystemExit(1)

from .cli import main

main()
