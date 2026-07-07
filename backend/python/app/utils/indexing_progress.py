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
