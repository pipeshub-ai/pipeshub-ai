"""Single source of truth for how each ``ProgressStatus`` maps to a display
bucket in the org-wide indexing progress bar.

The bar reasons by *category*, never by an individual status string, so it stays
correct whatever status the pipeline happens to write. The Node ticker mirrors
this exact mapping (``progress-status.constant.ts``); the two must never
disagree. ``assert_all_statuses_classified`` is what the coverage test uses to
guarantee a newly added ``ProgressStatus`` value cannot ship unclassified.
"""

from app.config.constants.arangodb import ProgressStatus

# Bucket names shared with the Node side.
PENDING = "pending"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

# QUEUED / IN_PROGRESS / NOT_STARTED hold the bar open. Everything else is
# settled. AUTO_INDEX_OFF and PAUSED are deliberately *settled*, not pending —
# otherwise a paused/auto-index-off record would keep the bar visible forever.
BUCKET_BY_STATUS: dict[str, str] = {
    ProgressStatus.QUEUED.value: PENDING,
    ProgressStatus.IN_PROGRESS.value: PENDING,
    ProgressStatus.NOT_STARTED.value: PENDING,
    ProgressStatus.COMPLETED.value: DONE,
    ProgressStatus.EMPTY.value: DONE,
    ProgressStatus.FAILED.value: FAILED,
    ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value: SKIPPED,
    ProgressStatus.AUTO_INDEX_OFF.value: SKIPPED,
    ProgressStatus.ENABLE_MULTIMODAL_MODELS.value: SKIPPED,
    ProgressStatus.PAUSED.value: SKIPPED,
}

PENDING_STATUSES = frozenset(s for s, b in BUCKET_BY_STATUS.items() if b == PENDING)


def classify(status: str | None) -> str | None:
    """Return the display bucket for a raw ``indexingStatus`` value, or None."""
    if status is None:
        return None
    return BUCKET_BY_STATUS.get(status)


def assert_all_statuses_classified() -> None:
    """Raise if any ``ProgressStatus`` value is missing from the map.

    Guards against a new enum value silently drifting the bar. Exercised by the
    §A6 coverage test.
    """
    missing = [s.value for s in ProgressStatus if s.value not in BUCKET_BY_STATUS]
    if missing:
        raise AssertionError(
            f"ProgressStatus values missing from progress classification: {missing}"
        )
