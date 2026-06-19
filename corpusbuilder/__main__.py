"""``python -m corpusbuilder`` entry point."""

from __future__ import annotations

import sys

from corpusbuilder.cli import main

if __name__ == "__main__":
    sys.exit(main())
