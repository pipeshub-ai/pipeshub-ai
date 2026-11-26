"""The ``stress`` marker's selection, shared by the root conftest and its own test (a conftest is never imported by name)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence


def skip_stress_tests(config: pytest.Config, items: Sequence[pytest.Item]) -> None:
    """``stress`` tests are probabilistic load tests: they run on request (``-m stress`` or
    ``RUN_STRESS_TESTS=1``), never in a shared runner's unit job."""
    if os.environ.get("RUN_STRESS_TESTS") == "1" or "stress" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="a stress test: run with -m stress or RUN_STRESS_TESTS=1")
    for item in items:
        if "stress" in item.keywords:
            item.add_marker(skip)
