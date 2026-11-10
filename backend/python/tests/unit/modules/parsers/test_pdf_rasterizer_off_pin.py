"""``PARSE_LANES`` off leaves the PDF rasterizer exactly as it was (T75).

``fixtures/docling_raster_off_trace.json`` was recorded from the sources before the lane work: a
hash of every top-level statement and method, and the executor creation and submission order.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.modules.parsers.docling_raster_trace import record_raster
from tests.unit.modules.parsers.off_pin_support import (
    TRACE,
    body_without_lane_branch,
    changed,
    plain,
    top_names,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

NAME = "pdf_rasterizer.py"
# What may differ from main, and why. Everything else is pinned by hash.
MODIFIED_ALLOWED = {
    "_run_in_pool",  # a first statement; the rest of the body is pinned below
    "render_all_pages_from_bytes",  # the same
    "if:TYPE_CHECKING",  # type-only imports (Callable, Iterator)
    "import:from typing import TYPE_CHECKING, Dict, List, Optional, Tuple",  # TypeVar joined it
}
ADDED_ALLOWED = {
    "T", "LANE_WORKER_ENV", "LANE_RASTER_INPROC_ENV", "_inproc_lock", "_raster_jobs", "_raster_jobs_lock",
    "_cancel_event", "cancel_on", "raster_jobs", "_lanes_active", "_lane_route", "_run_on_route",
}


def test_off_pin_covers_whole_module_except_allowlist() -> None:
    modified, added = changed(NAME)
    assert modified <= MODIFIED_ALLOWED, sorted(modified - MODIFIED_ALLOWED)
    assert top_names(added) <= ADDED_ALLOWED, sorted(top_names(added) - ADDED_ALLOWED)


def test_off_pin_allowlist_is_not_padded() -> None:
    modified, added = changed(NAME)
    assert modified == MODIFIED_ALLOWED
    assert top_names(added) == ADDED_ALLOWED


@pytest.mark.parametrize("function", ["_run_in_pool", "render_all_pages_from_bytes"])
def test_off_pin_only_the_lane_branch_was_added_to_the_functions(function: str) -> None:
    assert body_without_lane_branch(NAME, function) == TRACE["bodies"][function]


@pytest.fixture(params=["unset", "off", "empty", "junk"])
def lanes_off(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.param == "unset":
        monkeypatch.delenv("PARSE_LANES", raising=False)
    else:
        monkeypatch.setenv("PARSE_LANES", {"off": "off", "empty": "", "junk": "yes"}[request.param])
    monkeypatch.delenv("PIPESHUB_LANE_WORKER", raising=False)
    monkeypatch.delenv("PIPESHUB_LANE_RASTER_INPROC", raising=False)
    yield


def test_off_replays_the_recorded_executor_trace(lanes_off: None) -> None:
    assert plain(record_raster()) == TRACE["raster"]


def test_off_ignores_the_worker_marker_and_the_inproc_knob(lanes_off: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lane worker's environment means nothing unless the budget is on."""
    monkeypatch.setenv("PIPESHUB_LANE_WORKER", "1")
    monkeypatch.setenv("PIPESHUB_LANE_RASTER_INPROC", "1")
    assert plain(record_raster()) == TRACE["raster"]
