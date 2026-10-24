"""Metrics for storing a record's parsed content in blob storage."""

from app.telemetry.backend import METRICS_BACKEND

# status: 400 is the storage service's own upload limit, 413 a proxy's body limit in front of it.
REJECTED_TOO_LARGE = METRICS_BACKEND.counter(
    "pipeshub_record_storage_rejected_too_large_total",
    "Records that failed to index because the storage service refused their content as too large",
    ["status"],
)


def record_rejected_too_large(status: int) -> None:
    REJECTED_TOO_LARGE.inc(str(status))
