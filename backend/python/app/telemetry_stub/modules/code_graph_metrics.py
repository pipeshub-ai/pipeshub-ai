from ..backend import NOOP

BUILDS = NOOP
BUILD_DURATION = NOOP
EDGES_WRITTEN = NOOP
REFERENCES = NOOP
EVENTS = NOOP
OWED_BUILDS = NOOP


def record_build(outcome: str, result: object = None, mode: str | None = None) -> None:
    return None


def record_busy_request() -> None:
    return None


def record_reconcile_sweep(due: int, waiting: int, started: int) -> None:
    return None
