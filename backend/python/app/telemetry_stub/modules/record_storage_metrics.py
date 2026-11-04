from ..backend import NOOP

REJECTED_TOO_LARGE = NOOP


def record_rejected_too_large(status: int) -> None:
    return None


ARTIFACT_WRITES = NOOP


def record_artifact_write(outcome: str) -> None:
    return None


ARTIFACT_READS = NOOP
ARTIFACT_SHADOW_READS = NOOP
ARTIFACT_SHADOW_MISMATCHES = NOOP


def record_artifact_read(outcome: str) -> None:
    return None


def record_artifact_shadow_read(outcome: str, reason: str | None = None) -> None:
    return None


RETENTION = NOOP


def record_retention(kind: str, outcome: str, count: int = 1) -> None:
    return None


RECORD_READER_RELOOKUP = NOOP


def record_reader_relookup(outcome: str, trigger: str) -> None:
    return None


STORE_SWITCH = NOOP


def record_store_switch(layout: str, outcome: str, count: int = 1) -> None:
    return None


RETIRE_MARK = NOOP


def record_retire_mark(outcome: str) -> None:
    return None


PROPERTY_PROJECTIONS = NOOP


def record_property_projection(outcome: str) -> None:
    return None


PROPERTY_VALUES_DROPPED = NOOP


def record_property_value_dropped() -> None:
    return None
