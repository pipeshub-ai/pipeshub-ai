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
# or file this reader cannot use), stale_sidecar (the classification the mapping names is
# not stored, or is not of this stored write; kept apart from stale), failed (storage or
# another error).
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
# deleted, kept, unfenced (storage does not report the delete fence), changed (storage refused the delete: written again since it
# was listed), gone (already removed when the delete arrived) or failed;
# kind=virtual_record, outcome=skipped_trashed: virtual
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


# trigger: not_found (the gateway answered 404), signed_url (a 403 or 404 on a URL
# signed in this call), ranged (a failed ranged download).
# outcome: served (the mapping named a new document and it was read), unchanged
# (same document: the original error is re-raised), no_mapping (no mapping any
# more: the original error is re-raised), lookup_failed (the graph did not answer),
# failed (the new document could not be read either).
RECORD_READER_RELOOKUP = METRICS_BACKEND.counter(
    "pipeshub_record_reader_relookup_total",
    "Stored-record reads that took one fresh mapping lookup after the document read failed, by outcome",
    ["outcome", "trigger"],
)


def record_reader_relookup(outcome: str, trigger: str) -> None:
    RECORD_READER_RELOOKUP.inc(outcome, trigger)


# outcome: switched (the mapping now names the writer's document), conflict (the
# compare-and-set found the mapping moved: the write re-reads once, then is
# superseded), superseded (the write gave up: its document is a stray),
# record_gone (the record was deleted before the switch), deadline (the writer took
# longer than its deadline), retired_overflow (an entry was dropped from a full
# ``retired`` list), invalidated (an in-place write marked the stored run unservable
# before its upload), invalidation_skipped (it was already unservable).
STORE_SWITCH = METRICS_BACKEND.counter(
    "pipeshub_record_store_switch_total",
    "Stored-record writes switching the virtual record's mapping, by layout and outcome",
    ["layout", "outcome"],
)


def record_store_switch(layout: str, outcome: str, count: int = 1) -> None:
    if count:
        STORE_SWITCH.inc(layout, outcome, value=float(count))


# outcome: marked, gone (no such document, nothing to fence), failed (the mark could not be
# set; the switch goes on), unavailable (the storage service has no retire route: no write fence).
RETIRE_MARK = METRICS_BACKEND.counter(
    "pipeshub_record_retire_mark_total",
    "Retire marks set on replaced stored documents, by outcome",
    ["outcome"],
)


def record_retire_mark(outcome: str) -> None:
    RETIRE_MARK.inc(outcome)
