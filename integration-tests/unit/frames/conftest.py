"""FRAMES harness unit tests run offline: no stack, no network, no provider keys."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import benchmarks.harness  # noqa: E402,F401 — bootstraps the helper import roots
