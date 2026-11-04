from ..backend import NOOP

WATERMARK_LAG = NOOP
BUFFER_DEPTH = NOOP
ACTIVE_KEYS = NOOP
DISPATCHED = NOOP
DEFERRED = NOOP
LANES_PAUSED = NOOP
LANE_ASSIGNMENT_FALLBACKS = NOOP
LANE_CONNECTORS = NOOP
LANE_OLDEST_WAITING = NOOP
DWELL_EXCEEDED = NOOP
MISSING_KEY = NOOP
CONNECTOR_OFF_SETTLED = NOOP
GATE_WAITERS = NOOP


def record_watermark_lag(topic: str, partition: int, lag: int) -> None:
    return None


def record_scheduler_depth(
    broker: str, pending: int, active_by_level: dict[str, int]
) -> None:
    return None


def record_dispatch(broker: str, org: str) -> None:
    return None


def record_deferred(broker: str, reason: str) -> None:
    return None


def record_lanes_paused(broker: str, count: int) -> None:
    return None


def record_lane_assignment_fallback(reason: str) -> None:
    return None


def record_lane_connectors(lane: str, size: str, count: int) -> None:
    return None


def record_lane_oldest_waiting(lane: str, seconds: float) -> None:
    return None


def record_dwell_exceeded(broker: str, count: int = 1) -> None:
    return None


def record_missing_key(broker: str, field: str) -> None:
    return None


def record_gate_waiters(broker: str, tier: str, count: int) -> None:
    return None


def record_connector_off_settled(broker: str, count: int) -> None:
    return None
