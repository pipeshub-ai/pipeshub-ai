from ..backend import NOOP

NAMES = NOOP
MODEL_CALLS = NOOP
FALLBACKS = NOOP
LOOKUP_ERRORS = NOOP
LATENCY = NOOP


def record_name_outcome(kind: str, outcome: str, count: int = 1) -> None:
    return None


def record_model_call(result: str) -> None:
    return None


def record_fallback(reason: str, count: int = 1) -> None:
    return None


def record_lookup_error(lookup: str, outcome: str) -> None:
    return None


def record_latency(mode: str, seconds: float) -> None:
    return None
