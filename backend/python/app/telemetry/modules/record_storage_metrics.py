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


# outcome: served, or why the artifact set could not be: no_pointer (no current
# revision on the mapping), stale (the stored record holds another revision, or
# the mapping does not say which), missing (a file or the set is gone), too_large,
# integrity (a file's size or sha256 is not the manifest's), invalid (a manifest
# or file this reader cannot use), failed (storage or another error).
ARTIFACT_READS = METRICS_BACKEND.counter(
    "pipeshub_record_artifact_reads_total",
    "Record reads from artifact sets, by outcome",
    ["outcome"],
)

# outcome: match, mismatch, unavailable (the artifact read did not serve), legacy_failed,
# timeout (the artifact read outlasted the served read's grace), skipped (too many in flight).
ARTIFACT_SHADOW_READS = METRICS_BACKEND.counter(
    "pipeshub_record_artifact_shadow_reads_total",
    "Stored-record reads compared with their artifact set, by outcome",
    ["outcome"],
)

# reason: the first part that differed: record_field, semantic_metadata,
# container_field, block_count, block, group_count, group, legacy_missing.
ARTIFACT_SHADOW_MISMATCHES = METRICS_BACKEND.counter(
    "pipeshub_record_artifact_shadow_mismatches_total",
    "Shadow reads whose artifact set differed from the stored record, by first difference",
    ["reason"],
)


def record_artifact_read(outcome: str) -> None:
    ARTIFACT_READS.inc(outcome)


def record_artifact_shadow_read(outcome: str, reason: str | None = None) -> None:
    ARTIFACT_SHADOW_READS.inc(outcome)
    if reason is not None:
        ARTIFACT_SHADOW_MISMATCHES.inc(reason)


# kind=revision: artifact-set revisions the retention sweep looked at, outcome
# deleted, kept or failed; kind=virtual_record, outcome=skipped_trashed: virtual
# records left whole because a record holding them is in the trash, or
# skipped_busy: left for the next pass because a holder's record lease was held;
# kind=stage_state, outcome=deleted: stage states of revisions no record is on.
RETENTION = METRICS_BACKEND.counter(
    "pipeshub_record_retention_total",
    "What the revision retention sweep removed or kept, by kind and outcome",
    ["kind", "outcome"],
)


def record_retention(kind: str, outcome: str, count: int = 1) -> None:
    if count:
        RETENTION.inc(kind, outcome, value=float(count))
