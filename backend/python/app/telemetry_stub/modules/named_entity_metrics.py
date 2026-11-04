from ..backend import NOOP

ENTITIES = NOOP
RUNS = NOOP
FALLBACKS = NOOP
GROUNDING = NOOP
STEP_DOWNS = NOOP
STAGE_SECONDS = NOOP
EXTRACT_SECONDS = NOOP
TURNS = NOOP
TOOL_CALLS = NOOP
TOKENS = NOOP


def record_run(extraction: object, *, outcome: str) -> None:
    return None


def record_fallback(to_strategy: str, reason: str) -> None:
    return None


def record_step_down(mode: str) -> None:
    return None


def observe_stage(strategy: str, seconds: float) -> None:
    return None


def observe_extract(strategy: str, seconds: float) -> None:
    return None


SWEEP = NOOP


def record_sweep(outcome: str, count: int = 1) -> None:
    return None
