"""Counters for writes to the entities vector collection (KG-31).

Low-cardinality on purpose: operation and outcome only, never org or entity
ids (those go to the warning log of a failed write).
"""

from __future__ import annotations

from app.telemetry.backend import METRICS_BACKEND

WRITES = METRICS_BACKEND.counter(
    "pipeshub_entity_index_writes_total",
    "Entities handled by entity index writes, by operation and outcome",
    ["operation", "outcome"],
)


def record_writes(operation: str, outcome: str, count: int) -> None:
    if count > 0:
        WRITES.inc(operation, outcome, value=float(count))


__all__ = ["record_writes"]
