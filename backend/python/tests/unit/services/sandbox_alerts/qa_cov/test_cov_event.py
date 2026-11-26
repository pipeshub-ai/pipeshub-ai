"""Edge paths of the alert event builder (event.py)."""

from __future__ import annotations

import pytest

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.evaluator import evaluate
from app.services.sandbox_alerts.event import build_alert_event, slot_wait_seconds
from app.services.sandbox_alerts.window import WindowTotals

T0 = 1_800_000_000.0


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        (None, 120),
        ("", 120),
        ("   ", 120),
        ("30", 30),
        (" 30.4 ", 30),
        ("0", 0),
        ("abc", 120),
        ("nan", 120),
        # The sandbox settings treat these as the 120 s default, so the alert
        # must say what the runs actually waited, and must not fail to build.
        ("inf", 120),
        ("-inf", 120),
        ("1e400", 120),
        ("-5", 120),
    ],
)
def test_the_slot_wait_matches_what_runs_wait(raw: str | None, seconds: int) -> None:
    env = {} if raw is None else {"SANDBOX_SLOT_WAIT_S": raw}
    assert slot_wait_seconds(env) == seconds


def test_an_infinite_slot_wait_still_builds_the_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_SLOT_WAIT_S", "inf")
    totals = WindowTotals(window_s=1800, runs=20, waited=10, refused=10, busy_ticks=150)
    decision = evaluate(totals, AlertSettings())
    assert decision.level == "critical"
    event = build_alert_event(
        decision, {"limit": 2, "ceiling": 2, "floor": 1, "mode": "compose", "live": None},
        AlertSettings(), alert_id="a", host="h", fired_at=T0,
    )
    assert event["payload"]["slotWaitSeconds"] == 120


def test_a_decision_without_a_level_is_not_an_alert() -> None:
    decision = evaluate(WindowTotals(window_s=1800), AlertSettings())
    assert decision.level is None
    with pytest.raises(ValueError, match="no alert to build"):
        build_alert_event(decision, {}, AlertSettings(), alert_id="a", host="h", fired_at=T0)
