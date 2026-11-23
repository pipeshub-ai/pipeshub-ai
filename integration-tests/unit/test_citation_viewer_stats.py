"""The arithmetic of the citation viewer benchmark: budgets and the history check."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "perf" / "citation_viewer"))

from viewer_stats import (
    HISTORY_WINDOW,
    NOT_MODIFIED_P95_BUDGET_MS,
    append_history,
    median,
    over_budget,
    percentile,
    read_history,
    regressions,
)

pytestmark = pytest.mark.unit


def test_percentile_is_nearest_rank():
    assert percentile([], 95) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([float(i) for i in range(1, 101)], 95) == 95.0
    assert percentile([3.0, 1.0, 2.0], 100) == 3.0


def test_median_of_even_and_odd_counts():
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_a_304_over_its_budget_is_reported_at_either_client_count():
    assert over_budget({1: 10.0, 16: 30.0}) == []
    found = over_budget({1: 10.0, 16: NOT_MODIFIED_P95_BUDGET_MS + 1})
    assert len(found) == 1 and found[0].startswith("16 clients")
    assert over_budget({1: None}) == ["1 clients: no samples"]


def test_a_regression_over_ten_percent_of_the_recent_median_fails_and_a_small_one_does_not():
    history = [{"p": 100.0}, {"p": 102.0}, {"p": 98.0}]
    assert regressions(history, {"p": 109.0}, ["p"]) == []
    assert len(regressions(history, {"p": 111.0}, ["p"])) == 1
    assert regressions([], {"p": 1000.0}, ["p"]) == []  # no history, nothing to compare


def test_only_the_recent_window_counts():
    old = [{"p": 1000.0}] * 5
    recent = [{"p": 100.0}] * HISTORY_WINDOW
    assert len(regressions(old + recent, {"p": 120.0}, ["p"])) == 1


def test_history_round_trips_and_appends(tmp_path):
    path = tmp_path / "h" / "citation-viewer.jsonl"
    assert read_history(path) == []
    append_history(path, {"p": 1.0})
    append_history(path, {"p": 2.0})
    assert [r["p"] for r in read_history(path)] == [1.0, 2.0]
