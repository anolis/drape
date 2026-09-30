"""GUI entry point. Interface implementation lives in drape.ui."""

import sys

from .ui.application import App, main
from .ui.window import Window

__all__ = ["App", "Window", "main"]


if __name__ == "__main__":
    sys.exit(main())
