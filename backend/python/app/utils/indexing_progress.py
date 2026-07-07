"""Helpers for recording indexing-pipeline progress on record documents.

These produce the partial record-document fields written at the shared pipeline
checkpoints (start, parsing-complete, completed, failed). They are file-type and
model agnostic by construction: every path through the indexing pipeline crosses
the same checkpoints, so instrumenting them here covers all formats without
touching individual parsers.

The fields are intentionally minimal:
- ``indexingStage``: coarse phase, refining the binary ``indexingStatus``.
- ``lastActivityTimestamp``: heartbeat the UI uses to flag stalled records
  (an IN_PROGRESS record whose heartbeat has not advanced for a while).
- ``indexingProgress``: optional substage metrics, currently emitted by the
  shared embedding path once the total embeddable document count is known.
"""

import json
from typing import Any

from app.config.constants.arangodb import IndexingStage, ProgressStatus
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Maps a terminal/active record status to the stage the UI should show. Statuses
# absent here (e.g. EMPTY, AUTO_INDEX_OFF) are terminal and rendered purely from
# ``indexingStatus``, so they need no stage.
_STATUS_TO_STAGE: dict[ProgressStatus, IndexingStage] = {
    ProgressStatus.QUEUED: IndexingStage.QUEUED,
    ProgressStatus.IN_PROGRESS: IndexingStage.EXTRACTING,
    ProgressStatus.COMPLETED: IndexingStage.COMPLETED,
    ProgressStatus.FAILED: IndexingStage.FAILED,
}


def format_indexing_progress_message(
    *,
    phase: str,
    current: int,
    total: int,
    unit: str,
) -> str:
    """User-facing copy for substage progress on a single uploaded record."""
    normalized_total = max(0, int(total))
    normalized_current = max(0, min(int(current), normalized_total))
    if phase == "embedding" or unit in ("chunks", "documents"):
        return f"Embedding chunk {normalized_current} of {normalized_total}"
    if phase == "extracting" and unit in ("pages", "page"):
        return f"Extracting page {normalized_current} of {normalized_total}"
    return f"Processing {normalized_current} of {normalized_total} {unit}"


def build_indexing_substage_progress(
    *,
    current: int,
    total: int,
    unit: str,
    phase: str,
    message: str | None = None,
) -> dict[str, Any]:
    normalized_total = max(0, int(total))
    normalized_current = max(0, min(int(current), normalized_total))
    progress: dict[str, Any] = {
        "current": normalized_current,
        "total": normalized_total,
        "unit": unit,
        "phase": phase,
    }
    progress["message"] = message or format_indexing_progress_message(
        phase=phase,
        current=normalized_current,
        total=normalized_total,
        unit=unit,
    )
    return progress


def build_indexing_progress(
    stage: IndexingStage,
    *,
    timestamp: int | None = None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the record-document fields for a given pipeline ``stage``.

    The heartbeat defaults to now; pass ``timestamp`` to reuse one already
    computed by the caller so the stage and existing status fields agree.
    """
    fields = {
        "indexingStage": stage.value,
        "lastActivityTimestamp": (
            timestamp if timestamp is not None else get_epoch_timestamp_in_ms()
        ),
    }
    fields["indexingProgress"] = progress
    return fields


def stage_for_status(status: ProgressStatus) -> IndexingStage | None:
    """Map a ``ProgressStatus`` to its stage, or ``None`` if the status is a
    terminal one the UI renders directly from ``indexingStatus``."""
    return _STATUS_TO_STAGE.get(status)


# Per-record weight contributed to a container's weighted-average progress.
# Terminal statuses (done or intentionally-not-indexed) count as fully "resolved"
# so a container can reach 100% even when some records failed or were skipped;
# the failed/skipped counts are surfaced separately for visibility.
_RESOLVED_STATUSES = frozenset({
    ProgressStatus.COMPLETED.value,
    ProgressStatus.FAILED.value,
    ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
    ProgressStatus.AUTO_INDEX_OFF.value,
    ProgressStatus.EMPTY.value,
    ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
})
_QUEUED_STATUSES = frozenset({
    ProgressStatus.QUEUED.value,
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.PAUSED.value,
    None,
})
# Partial credit for an IN_PROGRESS record, refined by its coarse stage.
_STAGE_WEIGHT: dict[str | None, float] = {
    IndexingStage.QUEUED.value: 0.10,
    IndexingStage.EXTRACTING.value: 0.35,
    IndexingStage.INDEXING.value: 0.80,
    IndexingStage.COMPLETED.value: 0.95,
}
_IN_PROGRESS_DEFAULT_WEIGHT = 0.5


def build_container_rollup(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate per-status/stage leaf-record counts into a container rollup.

    ``rows`` are ``{"status", "stage", "cnt"}`` groupings for the indexable leaf
    records in a container's subtree (folders and internal records already
    excluded by the provider query). Returns ``None`` when the subtree has no
    indexable records, so containers without files show no progress bar.

    Weighting is equal per record (each record contributes a fraction of one),
    with in-progress records earning partial credit from their stage.
    """
    total = 0
    completed = in_progress = queued = failed = skipped = 0
    weighted = 0.0

    for row in rows:
        cnt = int(row.get("cnt") or 0)
        if cnt <= 0:
            continue
        status = row.get("status")
        stage = row.get("stage")
        total += cnt

        if status == ProgressStatus.COMPLETED.value:
            completed += cnt
            weighted += cnt
        elif status == ProgressStatus.FAILED.value:
            failed += cnt
            weighted += cnt
        elif status in _RESOLVED_STATUSES:
            skipped += cnt
            weighted += cnt
        elif status == ProgressStatus.IN_PROGRESS.value:
            in_progress += cnt
            weighted += cnt * _STAGE_WEIGHT.get(stage, _IN_PROGRESS_DEFAULT_WEIGHT)
        else:  # QUEUED / NOT_STARTED / PAUSED / unknown
            queued += cnt

    if total == 0:
        return None

    percent = max(0, min(100, round(100.0 * weighted / total)))
    is_active = in_progress > 0 or queued > 0
    if is_active:
        status_label = "IN_PROGRESS" if in_progress > 0 else "QUEUED"
    else:
        status_label = "COMPLETED_WITH_ERRORS" if failed > 0 else "COMPLETED"

    return {
        "total": total,
        "completed": completed,
        "inProgress": in_progress,
        "queued": queued,
        "failed": failed,
        "skipped": skipped,
        "percent": percent,
        "status": status_label,
        "isActive": is_active,
    }


def normalize_indexing_progress(value: Any) -> dict[str, Any] | None:
    """Coerce a stored ``indexingProgress`` value into a dict (or ``None``).

    ArangoDB stores it as a native object, but Neo4j cannot hold nested maps as
    node properties, so the Neo4j provider persists it as a JSON string. This
    normalizes both representations back to a dict at the API boundary.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
