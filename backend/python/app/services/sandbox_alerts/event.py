"""The ``notification``-topic events of the code sandbox load alerts.

Node delivers an event with ``audience: "platformAdmins"`` to the
installation's admins: an in-app notification each and, when ``email`` is
true, an email. The event carries only the type, enums, identifiers and
numbers; Node builds every sentence and the link from fixed templates, so the
text cannot be set by whoever writes to the broker.
"""

from __future__ import annotations

import math
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
# The documented titles and guide link. Node renders them; tests pin both
# sides to the deployment docs.
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


def indexing_slowed(decision: AlertDecision, sandbox_mode: str) -> bool:
    """Only for memory: CPU and slot waits do not wait for indexing to back
    off, and on a sidecar indexing does not share the sandboxes' memory."""
    return (
        decision.reason == "memory"
        and decision.indexing_slowed_since is not None
        and sandbox_mode != "helm_dind"
    )


def slot_wait_seconds(env: dict[str, str] | None = None) -> int:
    """The wait the sandbox settings use: blank, not a number, not finite or
    below 0 is the 120 s default."""
    raw = ((os.environ if env is None else env).get("SANDBOX_SLOT_WAIT_S") or "").strip()
    try:
        value = float(raw) if raw else math.nan
    except ValueError:
        value = math.nan
    return round(value) if math.isfinite(value) and value >= 0 else 120


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
    indexing = indexing_slowed(decision, sandbox_mode)
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
        # How often each level may be emailed, for the email's footer.
        "emailCooldownSeconds": settings.cooldown_s,
        "reason": reason,
        "reasonCounts": {r: totals.waited_by_reason.get(r, 0) for r in ("slots", "memory", "cpu")},
        "indexingSlowed": indexing,
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
    return {
        "audience": NotificationAudience.PLATFORM_ADMINS.value,
        "type": NotificationType.SANDBOX_CAPACITY_RESOLVED.value,
        "severity": NotificationSeverity.SUCCESS.value,
        "status": NotificationStatus.UNREAD.value,
        "originService": NotificationOrigin.AI.value,
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
            "alertFiredAt": _iso(alert_fired_at),
            "windowMinutes": minutes,
        },
    }
