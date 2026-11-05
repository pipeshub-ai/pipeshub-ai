"""The gauges the worker publishes: how much of a connector still takes today's walk, the change log, the rows the drain cannot read.

A record is *legacy* when the class compiler refused it (``legacy_reason`` > 0): readers answer it with the walk, which is exact and slow. The compiler and its reason names come with
the stamping change; until then the reason is its number and nothing writes one, so the gauge stays empty.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import drainer, restamp
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import Tx

LEGACY = METRICS_BACKEND.gauge("pipeshub_kg_legacy_records", "Records of a connector that keep today's walk, by the compiler's reason", ["org", "app", "reason"])
FENCE_AGE = METRICS_BACKEND.gauge("pipeshub_kg_fence_oldest_seconds", "Age of the oldest queued restamp job (a fence hides until it is done)", ["org"])
QUEUE = METRICS_BACKEND.gauge("pipeshub_kg_restamp_queue", "Queued restamp jobs, by whether a worker holds them", ["org", "state"])
QUARANTINED = METRICS_BACKEND.gauge("pipeshub_kg_quarantined_rows", "Change-log rows the drain cannot read (alert on > 0)", ["org"])
LAG = METRICS_BACKEND.gauge("pipeshub_kg_change_log_rows", "Committed change-log rows not yet projected", ["org"])


async def legacy_counts(tx: Tx, org: str) -> dict[tuple[int, str], int]:
    """{(app_sk, reason): records}. Reads the partial index of legacy rows only."""
    rows = await tx.fetch("""/* pipeshub:kg.legacy_counts */ SELECT app_sk, legacy_reason, count(*) AS n FROM kg.records
        WHERE org_id = $1 AND class_id IS NULL AND legacy_reason > 0 GROUP BY app_sk, legacy_reason""", org)
    return {(r["app_sk"], str(r["legacy_reason"])): r["n"] for r in rows}


async def publish(tx: Tx, org: str) -> None:
    """Set the gauges for one org (the worker calls it after a pass)."""
    names = {r["sk"]: r["id"] for r in await tx.fetch("/* pipeshub:kg.app_names */ SELECT sk, id FROM kg.node_keys WHERE org_id = $1 AND coll = 1", org)}
    LEGACY.clear()
    for (app_sk, reason), n in (await legacy_counts(tx, org)).items():
        LEGACY.set(org, names.get(app_sk, str(app_sk)), reason, value=n)
    FENCE_AGE.set(org, value=await restamp.oldest_seconds(tx, org))
    held = {r["held"]: r["n"] for r in await tx.fetch("/* pipeshub:kg.queue_state */ SELECT claimed_by IS NOT NULL AS held, count(*) AS n FROM kg.restamp_queue WHERE org_id = $1 GROUP BY 1", org)}
    QUEUE.set(org, "held", value=held.get(True, 0))
    QUEUE.set(org, "waiting", value=held.get(False, 0))
    QUARANTINED.set(org, value=len(await drainer.quarantined(tx, org)))
    LAG.set(org, value=await tx.fetchval("/* pipeshub:kg.lag_gauge */ SELECT COALESCE(sum(cardinality(ends)), 0) FROM kg.graph_changes WHERE org_id = $1", org))
