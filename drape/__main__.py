from .dependencies import ensure

if not ensure():
    raise SystemExit(1)

from .cli import main

main()
