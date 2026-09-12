#!/usr/bin/env python3
"""Entry point: `python loadtest/run.py <command>` (also `python -m lt` from here)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lt.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
