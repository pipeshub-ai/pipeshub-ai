from __future__ import annotations

import pytest

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.evaluator import evaluate
from app.services.sandbox_alerts.window import WindowTotals

S = AlertSettings()


def totals(**kw) -> WindowTotals:
    reasons = {"slots": 0, "memory": 0, "cpu": 0, "orgShare": 0}
    reasons.update(kw.pop("reasons", {}))
    return WindowTotals(window_s=1800, waited_by_reason=reasons, **kw)


@pytest.mark.parametrize(
    ("busy_ticks", "waited", "level"),
    [(107, 100, None), (108, 30, "warning"), (108, 29, None)],
    ids=["59.4%-100-waited", "60%-30-waited", "60%-29-waited"],
)
def test_warning_edges(busy_ticks: int, waited: int, level: str | None) -> None:
    assert evaluate(totals(busy_ticks=busy_ticks, waited=waited, runs=200, reasons={"slots": waited}), S).level == level


@pytest.mark.parametrize(
    ("refused", "runs", "level"),
    [(9, 100, None), (10, 200, "critical"), (10, 201, None)],
)
def test_critical_edges(refused: int, runs: int, level: str | None) -> None:
    assert evaluate(totals(refused=refused, waited=refused, runs=runs), S).level == level


def test_critical_wins() -> None:
    decision = evaluate(totals(busy_ticks=180, waited=100, refused=50, runs=100), S)
    assert decision.level == "critical"
    assert decision.warning and decision.critical


def test_busy_share_is_of_the_whole_window() -> None:
    assert evaluate(totals(busy_ticks=60), S).busy_percent == pytest.approx(100 / 3)


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ({"slots": 5, "memory": 5, "cpu": 5}, "memory"),
        ({"slots": 5, "cpu": 5}, "cpu"),
        ({"slots": 6, "memory": 5}, "slots"),
        ({}, None),
    ],
)
def test_reason_is_the_largest_count(reasons: dict, expected: str | None) -> None:
    assert evaluate(totals(reasons=reasons), S).reason == expected


def test_indexing_slowed_since_comes_from_the_window() -> None:
    assert evaluate(totals(backed_off_since=123.0), S).indexing_slowed_since == 123.0
