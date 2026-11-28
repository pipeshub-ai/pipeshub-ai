from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.evaluator import AlertDecision
from app.services.sandbox_alerts.event import (
    build_alert_event,
    build_resolved_event,
    memory_source,
)
from app.services.sandbox_alerts.window import WindowTotals

FIRED = datetime(2026, 11, 16, 10, 42, tzinfo=UTC).timestamp()
SINCE = datetime(2026, 11, 16, 10, 13, 40, tzinfo=UTC).timestamp()
GIB = 1024**3


def decision(level: str | None = "warning", reason: str | None = "memory", since: float | None = SINCE,
             busy: float = 72.0, refused_pct: float = 0.0, **kw) -> AlertDecision:
    t = WindowTotals(
        window_s=1800, runs=kw.get("runs", 412), waited=kw.get("waited", 57), refused=kw.get("refused", 0),
        waited_by_reason={"slots": 4, "memory": 53, "cpu": 0, "orgShare": 0}, max_wait_s=118.2,
        backed_off_since=since,
    )
    return AlertDecision(
        level=level, warning=level is not None, critical=level == "critical",  # type: ignore[arg-type]
        busy_percent=busy, refused_percent=refused_pct, reason=reason,
        indexing_slowed_since=since, totals=t,
    )


def snapshot(mode: str = "compose", source: str | None = "host", total: int | None = 16 * GIB,
             free: int | None = int(1.1 * GIB)) -> dict:
    live = None if source is None else {"source": source, "mem_total_bytes": total, "mem_available_bytes": free}
    return {"limit": 3, "ceiling": 6, "floor": 2, "mode": mode, "live": live}


def build(d: AlertDecision | None = None, snap: dict | None = None) -> dict:
    return build_alert_event(
        d or decision(), snap or snapshot(), AlertSettings(),
        alert_id="a1", host="pipeshub-7d9c", fired_at=FIRED, slot_wait_s=120,
    )


def test_the_document_is_for_the_platform_admins_with_no_org() -> None:
    event = build()
    assert event["audience"] == "platformAdmins"
    assert "orgId" not in event
    assert event["payload"]["v"] == 1
    assert event["email"] is True
    assert event["originService"] == "AI Service"


@pytest.mark.parametrize(
    ("level", "type_", "severity"),
    [
        ("warning", "SANDBOX_CAPACITY_WARNING", "warning"),
        ("critical", "SANDBOX_CAPACITY_CRITICAL", "critical"),
    ],
)
def test_type_and_severity_per_level(level: str, type_: str, severity: str) -> None:
    event = build(decision(level))
    assert (event["type"], event["severity"], event["payload"]["level"]) == (type_, severity, level)


def test_the_event_carries_no_text_or_link() -> None:
    """Node builds every sentence and the link from the structured fields."""
    event = build()
    assert not {"title", "message", "redirectLink"} & set(event)
    assert not {"reasonText", "indexingText"} & set(event["payload"])


def test_the_fields_node_words_the_warning_from() -> None:
    payload = build()["payload"]
    assert (payload["busyPercent"], payload["windowMinutes"], payload["waited"]) == (72, 30, 57)
    assert (payload["reason"], payload["memorySource"]) == ("memory", "host")
    assert payload["indexingSlowed"] is True
    assert payload["indexingSlowedSince"] == "2026-11-16T10:13:40Z"


def test_the_fields_node_words_the_critical_from() -> None:
    payload = build(decision("critical", reason="slots", refused=14, runs=210, refused_pct=100 * 14 / 210))["payload"]
    assert (payload["refused"], payload["runs"], payload["refusedPercent"]) == (14, 210, 6.7)
    assert (payload["slotWaitSeconds"], payload["reason"], payload["limit"]["ceiling"]) == (120, "slots", 6)


@pytest.mark.parametrize("reason", ["cpu", "slots"])
def test_no_indexing_outside_memory(reason: str) -> None:
    payload = build(decision(reason=reason))["payload"]
    assert payload["indexingSlowed"] is False
    assert payload["indexingSlowedSince"] is None


def test_no_indexing_on_a_sidecar() -> None:
    payload = build(decision(reason="memory"), snapshot(mode="helm_dind", source="sidecar"))["payload"]
    assert payload["indexingSlowed"] is False
    assert payload["memorySource"] == "sidecar"


def test_no_indexing_when_indexing_did_not_slow() -> None:
    assert build(decision(since=None))["payload"]["indexingSlowed"] is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [("sidecar", "sidecar"), ("sidecar-containers", "sidecar"), ("host", "host"), ("cgroup", "host"), (None, "host")],
)
def test_memory_source(source: str | None, expected: str) -> None:
    assert memory_source(snapshot(source=source)) == expected


def test_percent_rounding() -> None:
    assert build(decision(busy=59.6))["payload"]["busyPercent"] == 60
    critical = build(decision("critical", refused=10, runs=201, refused_pct=100 * 10 / 201))
    assert critical["payload"]["refusedPercent"] == 5.0
    assert build(decision("critical", refused_pct=6.66))["payload"]["refusedPercent"] == 6.7


def test_expires_six_hours_after_it_fired() -> None:
    payload = build()["payload"]
    assert payload["firedAt"] == "2026-11-16T10:42:00Z"
    assert payload["expiresAt"] == "2026-11-16T16:42:00Z"


def test_memory_figures_and_limits() -> None:
    payload = build()["payload"]
    assert payload["limit"] == {"current": 3, "ceiling": 6, "floor": 2}
    assert (payload["hostMemoryTotalGb"], payload["hostMemoryFreeGb"]) == (16.0, 1.1)
    off = build(snap=snapshot(source=None))["payload"]
    assert (off["hostMemoryTotalGb"], off["hostMemoryFreeGb"]) == (None, None)


def test_the_payload_has_only_the_listed_keys() -> None:
    assert set(build()["payload"]) == {
        "v", "alertId", "level", "firedAt", "expiresAt", "host", "sandboxMode", "memorySource",
        "windowMinutes", "runs", "waited", "refused", "busyPercent", "refusedPercent",
        "longestWaitSeconds", "slotWaitSeconds", "emailCooldownSeconds", "reason", "reasonCounts",
        "indexingSlowed", "indexingSlowedSince", "limit",
        "hostMemoryTotalGb", "hostMemoryFreeGb", "type", "email",
    }


def test_the_type_and_email_flag_are_inside_the_signed_payload_too() -> None:
    # A signature covers only the payload: Node reads the type and the email flag from there.
    event = build()
    assert event["payload"]["type"] == event["type"]
    assert event["payload"]["email"] is event["email"]


def test_the_resolved_notice_is_in_app_only() -> None:
    event = build_resolved_event(
        level="critical", resolved_alert_id="a1", alert_fired_at=FIRED, clear_since=FIRED + 600,
        settings=AlertSettings(), alert_id="r1", host="pipeshub-7d9c", fired_at=FIRED + 2400,
    )
    assert event["type"] == "SANDBOX_CAPACITY_RESOLVED"
    assert event["severity"] == "success"
    assert event["email"] is False
    assert not {"title", "message", "redirectLink"} & set(event)
    payload = event["payload"]
    assert payload["resolvesAlertId"] == "a1"
    assert (payload["level"], payload["host"], payload["windowMinutes"]) == ("critical", "pipeshub-7d9c", 30)
    assert (payload["clearSince"], payload["alertFiredAt"]) == ("2026-11-16T10:52:00Z", "2026-11-16T10:42:00Z")
