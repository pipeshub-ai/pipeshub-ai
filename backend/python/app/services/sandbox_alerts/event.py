"""The ``notification``-topic events of the code sandbox load alerts.

Node delivers an event with ``audience: "platformAdmins"`` to the
installation's admins: an in-app notification each and, when ``email`` is
true, an email built from ``payload``. The text is English and stored as is.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.services.notification.types import (
    NotificationAudience,
    NotificationOrigin,
    NotificationSeverity,
    NotificationStatus,
    NotificationType,
)

if TYPE_CHECKING:
    from app.services.sandbox_alerts.config import AlertSettings
    from app.services.sandbox_alerts.evaluator import AlertDecision

PAYLOAD_VERSION = 1
DOCS_URL = "https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts"
# Node drops an event it receives later than this, so an outage does not
# deliver a stale alert hours afterwards.
EXPIRES_AFTER = timedelta(hours=6)

_TITLES = {
    "warning": "Code runs are waiting for capacity",
    "critical": "Code runs are being refused",
}
RESOLVED_TITLE = "Code runs are no longer held back"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%H:%M UTC")


def _pct(value: float) -> int | float:
    """Whole percents, with one decimal below 10 % so 4.97 % does not read as 5 %."""
    if value >= 10 or float(value).is_integer():
        return round(value)
    return round(value, 1)


def _gb(value: object) -> float | None:
    return round(value / 1024**3, 1) if isinstance(value, int) else None


def memory_source(snapshot: dict[str, Any]) -> str:
    live = snapshot.get("live") or {}
    source = live.get("source") if isinstance(live, dict) else None
    return "sidecar" if isinstance(source, str) and source.startswith("sidecar") else "host"


def reason_text(reason: str | None, source: str, ceiling: object) -> str:
    sidecar = source == "sidecar"
    if reason == "memory":
        if sidecar:
            return "The code runner was short of memory, so new runs were held back."
        return "Free memory on the host was low, so new runs were held back to protect the databases and the app."
    if reason == "cpu":
        who = "The code runner's" if sidecar else "The host's"
        return f"{who} CPU stayed busy, so fewer runs were allowed at once."
    return (
        f"All {ceiling} code-run slots were in use. The host still had free memory and CPU, "
        "so the slot limit was the bottleneck."
    )


def indexing_text(decision: AlertDecision, sandbox_mode: str) -> str | None:
    """Only for memory: CPU and slot waits do not wait for indexing to back
    off, and on a sidecar indexing does not share the sandboxes' memory."""
    since = decision.indexing_slowed_since
    if decision.reason != "memory" or since is None or sandbox_mode == "helm_dind":
        return None
    return f"Indexing was already slowed down from {_hhmm(since)} to give chat priority."


def slot_wait_seconds(env: dict[str, str] | None = None) -> int:
    raw = ((os.environ if env is None else env).get("SANDBOX_SLOT_WAIT_S") or "").strip()
    try:
        return round(float(raw)) if raw else 120
    except ValueError:
        return 120


def build_alert_event(
    decision: AlertDecision,
    snapshot: dict[str, Any],
    settings: AlertSettings,
    *,
    alert_id: str,
    host: str,
    fired_at: float,
    slot_wait_s: int | None = None,
    email: bool = True,
) -> dict[str, Any]:
    """The event for a warning or critical *decision*; *snapshot* is the
    governor's ``snapshot()``. *email* false: in-app only, because this level
    was already emailed within the cooldown."""
    level = decision.level
    if level is None:
        raise ValueError("no alert to build")
    totals = decision.totals
    sandbox_mode = str(snapshot.get("mode") or "")
    source = memory_source(snapshot)
    live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else {}
    minutes = round(settings.window_s / 60)
    slot_wait = slot_wait_seconds() if slot_wait_s is None else slot_wait_s
    busy = _pct(decision.busy_percent)
    refused_pct = _pct(decision.refused_percent)
    reason = decision.reason or "slots"
    because = reason_text(reason, source, snapshot.get("ceiling"))
    indexing = indexing_text(decision, sandbox_mode)
    if level == "critical":
        lead = (
            f"{totals.refused} of {totals.runs} code runs in the last {minutes} minutes "
            f"({refused_pct}%) were refused after waiting {slot_wait} s for a free sandbox."
        )
    else:
        lead = (
            f"For {busy}% of the last {minutes} minutes, code runs in chat waited for a free "
            f"sandbox ({totals.waited} runs waited)."
        )
    message = " ".join(part for part in (lead, because, indexing) if part)
    payload: dict[str, Any] = {
        "v": PAYLOAD_VERSION,
        "alertId": alert_id,
        "level": level,
        "firedAt": _iso(fired_at),
        "expiresAt": _iso(fired_at + EXPIRES_AFTER.total_seconds()),
        "host": host,
        "sandboxMode": sandbox_mode,
        "memorySource": source,
        "windowMinutes": minutes,
        "runs": totals.runs,
        "waited": totals.waited,
        "refused": totals.refused,
        "busyPercent": busy,
        "refusedPercent": refused_pct,
        "longestWaitSeconds": round(totals.max_wait_s),
        "slotWaitSeconds": slot_wait,
        "reason": reason,
        "reasonCounts": {r: totals.waited_by_reason.get(r, 0) for r in ("slots", "memory", "cpu")},
        "reasonText": because,
        "indexingText": indexing,
        "indexingSlowed": indexing is not None,
        "indexingSlowedSince": _iso(decision.indexing_slowed_since) if indexing else None,
        "limit": {
            "current": snapshot.get("limit"),
            "ceiling": snapshot.get("ceiling"),
            "floor": snapshot.get("floor"),
        },
        "hostMemoryTotalGb": _gb(live.get("mem_total_bytes")),
        "hostMemoryFreeGb": _gb(live.get("mem_available_bytes")),
    }
    critical = level == "critical"
    return {
        "audience": NotificationAudience.PLATFORM_ADMINS.value,
        "type": (NotificationType.SANDBOX_CAPACITY_CRITICAL if critical else NotificationType.SANDBOX_CAPACITY_WARNING).value,
        "severity": (NotificationSeverity.CRITICAL if critical else NotificationSeverity.WARNING).value,
        "status": NotificationStatus.UNREAD.value,
        "originService": NotificationOrigin.AI.value,
        "title": _TITLES[level],
        "message": message,
        "redirectLink": DOCS_URL,
        "email": email,
        "isDeleted": False,
        "payload": payload,
    }


def build_resolved_event(
    *,
    level: str,
    resolved_alert_id: str,
    alert_fired_at: float,
    clear_since: float,
    settings: AlertSettings,
    alert_id: str,
    host: str,
    fired_at: float,
) -> dict[str, Any]:
    """The in-app only notice that the *level* alert from *alert_fired_at*
    has cleared for a full window."""
    minutes = round(settings.window_s / 60)
    message = (
        f"Since {_hhmm(clear_since)}, code runs in chat on {host} have had enough capacity for "
        f"{minutes} minutes. The {level} alert from {_hhmm(alert_fired_at)} is resolved."
    )
    return {
        "audience": NotificationAudience.PLATFORM_ADMINS.value,
        "type": NotificationType.SANDBOX_CAPACITY_RESOLVED.value,
        "severity": NotificationSeverity.SUCCESS.value,
        "status": NotificationStatus.UNREAD.value,
        "originService": NotificationOrigin.AI.value,
        "title": RESOLVED_TITLE,
        "message": message,
        "redirectLink": DOCS_URL,
        "email": False,
        "isDeleted": False,
        "payload": {
            "v": PAYLOAD_VERSION,
            "alertId": alert_id,
            "level": level,
            "resolvesAlertId": resolved_alert_id,
            "firedAt": _iso(fired_at),
            "expiresAt": _iso(fired_at + EXPIRES_AFTER.total_seconds()),
            "host": host,
            "clearSince": _iso(clear_since),
            "windowMinutes": minutes,
        },
    }
