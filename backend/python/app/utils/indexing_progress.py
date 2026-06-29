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
"""

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


def build_indexing_progress(
    stage: IndexingStage, *, timestamp: int | None = None
) -> dict[str, Any]:
    """Return the record-document fields for a given pipeline ``stage``.

    The heartbeat defaults to now; pass ``timestamp`` to reuse one already
    computed by the caller so the stage and existing status fields agree.
    """
    return {
        "indexingStage": stage.value,
        "lastActivityTimestamp": (
            timestamp if timestamp is not None else get_epoch_timestamp_in_ms()
        ),
    }


def stage_for_status(status: ProgressStatus) -> IndexingStage | None:
    """Map a ``ProgressStatus`` to its stage, or ``None`` if the status is a
    terminal one the UI renders directly from ``indexingStatus``."""
    return _STATUS_TO_STAGE.get(status)
