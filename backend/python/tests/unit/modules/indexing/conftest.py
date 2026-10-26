from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.modules.indexing import lane_upkeep

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _no_lane_report_left_behind() -> Iterator[None]:
    """Each upkeep pass keeps its report in module state that ``GET /health``
    reads; one left behind shows up in health tests that run later in the
    same process."""
    yield
    lane_upkeep._last_reports.clear()
