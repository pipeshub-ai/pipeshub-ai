from ..backend import NOOP

WRITES = NOOP


def record_writes(operation: str, outcome: str, count: int) -> None:
    return None
