"""Decide whether the window calls for a warning or a critical alert."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.agent_loop_lib.sandbox.load_ledger import TICK_S

if TYPE_CHECKING:
    from app.services.sandbox_alerts.config import AlertSettings
    from app.services.sandbox_alerts.window import WindowTotals

Level = Literal["warning", "critical"]

# Ties go to the reason an admin can do the most about.
_REASON_ORDER = ("memory", "cpu", "slots")


@dataclass(frozen=True)
class AlertDecision:
    level: Level | None
    warning: bool
    critical: bool
    busy_percent: float
    refused_percent: float
    reason: str | None
    indexing_slowed_since: float | None
    totals: WindowTotals

    @property
    def any(self) -> bool:
        return self.warning or self.critical


def evaluate(totals: WindowTotals, settings: AlertSettings) -> AlertDecision:
    """Warning: runs were held back for at least ``warn_busy_pct`` of the
    whole window (not of the time observed, so a short burst after a restart
    cannot look like 100 %) and at least ``warn_min_waited`` runs waited.
    Critical: at least ``crit_min_refused`` runs were refused and they are at
    least ``crit_refused_pct`` of all runs. Critical wins."""
    window_ticks = max(1, totals.window_s // TICK_S)
    # A window that is not a whole number of ticks can touch one tick more.
    busy_ticks = min(totals.busy_ticks, window_ticks)
    busy_percent = 100.0 * busy_ticks / window_ticks
    refused_percent = 100.0 * totals.refused / totals.runs if totals.runs else 0.0
    # Integer comparisons, so a threshold met exactly is not lost to rounding.
    warning = (
        busy_ticks * 100 >= settings.warn_busy_pct * window_ticks
        and totals.waited >= settings.warn_min_waited
    )
    critical = (
        totals.refused >= settings.crit_min_refused
        and totals.refused * 100 >= settings.crit_refused_pct * totals.runs
    )
    counts = totals.waited_by_reason
    reason = max(_REASON_ORDER, key=lambda r: (counts.get(r, 0), -_REASON_ORDER.index(r)))
    if counts.get(reason, 0) == 0:
        reason = None
    level: Level | None = "critical" if critical else "warning" if warning else None
    return AlertDecision(
        level=level, warning=warning, critical=critical,
        busy_percent=busy_percent, refused_percent=refused_percent,
        reason=reason, indexing_slowed_since=totals.backed_off_since, totals=totals,
    )
