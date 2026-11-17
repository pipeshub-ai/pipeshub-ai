"""The alert event's wording with a cap of one, and the email cooldown it
carries so the email can say how often it may come."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.evaluator import AlertDecision
from app.services.sandbox_alerts.event import build_alert_event, reason_text
from app.services.sandbox_alerts.window import WindowTotals

FIRED = datetime(2026, 11, 16, 10, 42, tzinfo=UTC).timestamp()


def _decision() -> AlertDecision:
    totals = WindowTotals(
        window_s=1800, runs=20, waited=8, refused=0,
        waited_by_reason={"slots": 8, "memory": 0, "cpu": 0, "orgShare": 0}, max_wait_s=30.0,
        backed_off_since=None,
    )
    return AlertDecision(
        level="warning", warning=True, critical=False, busy_percent=40.0, refused_percent=0.0,
        reason="slots", indexing_slowed_since=None, totals=totals,
    )


def _build(settings: AlertSettings, ceiling: int = 1) -> dict:
    snapshot = {"limit": ceiling, "ceiling": ceiling, "floor": 1, "mode": "compose", "live": None}
    return build_alert_event(
        _decision(), snapshot, settings, alert_id="a1", host="h", fired_at=FIRED, slot_wait_s=120,
    )


def test_a_cap_of_one_reads_in_the_singular() -> None:
    assert reason_text("slots", "host", 1).startswith("The only code-run slot was in use.")
    assert "All 1" not in _build(AlertSettings())["message"]


def test_a_larger_cap_keeps_the_plural() -> None:
    assert reason_text("slots", "host", 6).startswith("All 6 code-run slots were in use.")


def test_the_payload_carries_the_email_cooldown() -> None:
    assert _build(AlertSettings())["payload"]["emailCooldownSeconds"] == 86400
    short = dataclasses.replace(AlertSettings(), cooldown_s=600)
    assert _build(short)["payload"]["emailCooldownSeconds"] == 600
