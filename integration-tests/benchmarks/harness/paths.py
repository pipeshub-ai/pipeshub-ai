"""Filesystem anchors for the harness and the helper import bootstrap.

Report and cache directories are scoped by dataset so two benchmarks never
write into each other's runs. For FRAMES these resolve to the same paths the
harness has always used, so existing runs keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BENCHMARKS_DIR = PACKAGE_DIR.parent
INTEGRATION_TESTS_DIR = BENCHMARKS_DIR.parent
HELPER_DIR = INTEGRATION_TESTS_DIR / "helper"
DATASETS_DIR = BENCHMARKS_DIR / "datasets"
# Grader prompts are harness-level: the rubrics are reusable across datasets.
PROMPTS_DIR = PACKAGE_DIR / "grading" / "prompts"


def reports_dir(dataset: str) -> Path:
    return INTEGRATION_TESTS_DIR / "reports" / f"{dataset}-benchmark"


def cache_dir(dataset: str) -> Path:
    return INTEGRATION_TESTS_DIR / ".cache" / dataset


def dataset_dir(dataset: str) -> Path:
    return DATASETS_DIR / dataset


def ensure_helper_importable() -> None:
    """Helpers import each other as both `pipeshub_client` and
    `helper.pipeshub_client`, so both roots have to be importable."""
    for path in (INTEGRATION_TESTS_DIR, HELPER_DIR):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
