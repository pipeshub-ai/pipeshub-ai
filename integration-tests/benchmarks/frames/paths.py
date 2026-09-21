"""Filesystem anchors for the harness and the helper import bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
INTEGRATION_TESTS_DIR = PACKAGE_DIR.parent.parent
HELPER_DIR = INTEGRATION_TESTS_DIR / "helper"
REPORTS_DIR = INTEGRATION_TESTS_DIR / "reports" / "frames-benchmark"
CACHE_DIR = INTEGRATION_TESTS_DIR / ".cache" / "frames"
DATA_DIR = PACKAGE_DIR / "data"
CONFIG_DIR = PACKAGE_DIR / "configs"
PROMPTS_DIR = PACKAGE_DIR / "grading" / "prompts"
SPLIT_FILE = DATA_DIR / "split_v1.json"


def ensure_helper_importable() -> None:
    """Helpers import each other as both `pipeshub_client` and
    `helper.pipeshub_client`; both roots must be importable."""
    for path in (INTEGRATION_TESTS_DIR, HELPER_DIR):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
