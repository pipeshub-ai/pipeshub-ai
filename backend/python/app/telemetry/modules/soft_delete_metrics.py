"""Records moved to the trash and back, by who moved them.

No per-org or per-connector labels, like ``scheduling_metrics``: the label set
stays bounded however many tenants an install serves.
"""

from app.telemetry.backend import METRICS_BACKEND

RECORDS_SOFT_DELETED = METRICS_BACKEND.counter(
    "pipeshub_records_soft_deleted_total",
    "Records moved to the trash",
    ["source"],
)

RECORDS_RESTORED = METRICS_BACKEND.counter(
    "pipeshub_records_restored_total",
    "Records brought back from the trash",
    ["source"],
)


def record_soft_deleted(source: str, count: int) -> None:
    if count > 0:
        RECORDS_SOFT_DELETED.inc(source, value=count)


def record_restored(source: str, count: int) -> None:
    if count > 0:
        RECORDS_RESTORED.inc(source, value=count)
