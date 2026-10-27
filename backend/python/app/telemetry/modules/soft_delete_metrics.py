"""Records moved to the trash, back, and out of it for good by the purge.

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

PURGE_RUNS = METRICS_BACKEND.counter(
    "pipeshub_purge_runs_total",
    "Trash purge passes, by how they ended",
    ["status"],
)

PURGE_RECORDS = METRICS_BACKEND.counter(
    "pipeshub_purge_records_total",
    "Records the trash purge looked at, by outcome",
    ["result"],
)

PURGE_RUN_DURATION = METRICS_BACKEND.histogram(
    "pipeshub_purge_run_duration_seconds",
    "Wall-clock time of a trash purge run, from its start to its end",
    ["status"],
    buckets=(10.0, 60.0, 300.0, 900.0, 1800.0, 3600.0, 7200.0, 21600.0),
)

# Prometheus needs a label on every series; "state" splits the backlog.
TRASH_BACKLOG = METRICS_BACKEND.gauge(
    "pipeshub_soft_deleted_records",
    "Records in the trash: pending the purge, or stuck after too many failed purges",
    ["state"],
)

OLDEST_PENDING_AGE = METRICS_BACKEND.gauge(
    "pipeshub_purge_oldest_pending_age_seconds",
    "How long the oldest record still waiting for the purge has been in the trash",
    ["state"],
)


def record_purge_run(status: str, duration_seconds: float | None = None) -> None:
    PURGE_RUNS.inc(status)
    if duration_seconds is not None:
        PURGE_RUN_DURATION.observe(status, value=duration_seconds)


def record_purged(result: str, count: int) -> None:
    if count > 0:
        PURGE_RECORDS.inc(result, value=count)


def set_trash_backlog(pending: int, stuck: int, oldest_pending_age_seconds: float) -> None:
    TRASH_BACKLOG.set("pending", value=pending)
    TRASH_BACKLOG.set("stuck", value=stuck)
    OLDEST_PENDING_AGE.set("pending", value=oldest_pending_age_seconds)
