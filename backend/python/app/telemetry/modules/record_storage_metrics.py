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


# outcome: written (manifest stored), failed (indexing went on without it),
# unavailable (the storage service has no artifact routes); and for a stage's
# enrichment sidecar of a revision, sidecar_written, sidecar_failed and
# sidecar_unavailable.
ARTIFACT_WRITES = METRICS_BACKEND.counter(
    "pipeshub_record_artifact_writes_total",
    "Content revisions indexing tried to store as artifact sets, and their enrichment sidecars, by outcome",
    ["outcome"],
)


def record_artifact_write(outcome: str) -> None:
    ARTIFACT_WRITES.inc(outcome)
