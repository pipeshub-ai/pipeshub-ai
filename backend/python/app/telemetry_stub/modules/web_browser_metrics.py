from ..backend import NOOP

RESTARTS = NOOP
REJECTED_FETCHES = NOOP


def record_restart(reason: str, *, ok: bool) -> None:
    return None


def record_rejected_fetch(reason: str) -> None:
    return None
