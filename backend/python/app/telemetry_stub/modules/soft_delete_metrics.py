from ..backend import NOOP

RECORDS_SOFT_DELETED = NOOP
RECORDS_RESTORED = NOOP


def record_soft_deleted(source: str, count: int) -> None:
    return None


def record_restored(source: str, count: int) -> None:
    return None


PURGE_RUNS = NOOP
PURGE_RECORDS = NOOP
PURGE_RUN_DURATION = NOOP
TRASH_BACKLOG = NOOP
OLDEST_PENDING_AGE = NOOP


def record_purge_run(status: str, duration_seconds: float | None = None) -> None:
    return None


def record_purged(result: str, count: int) -> None:
    return None


def set_trash_backlog(
    pending: int, stuck: int, oldest_pending_age_seconds: float
) -> None:
    return None
