"""Counters for identity forwarding.

In-process and dependency free: the forwarding hook runs on every model request,
so recording must never block, raise or import a metrics client. The telemetry
exporter, or a test, reads ``snapshot()``.
"""

from __future__ import annotations

import threading
from collections import defaultdict

LabelValues = tuple[str, ...]

IDENTITY_FORWARDED = "llm_identity_forwarded_total"
IDENTITY_MISSING = "identity_missing_total"
GATEWAY_POLICY_REFUSALS = "gateway_policy_refusals_total"
GROUP_RESOLUTION_SECONDS = "identity_group_resolution_seconds"

_lock = threading.Lock()
_counters: dict[str, dict[LabelValues, float]] = defaultdict(lambda: defaultdict(float))
_observations: dict[str, dict[LabelValues, list]] = defaultdict(lambda: defaultdict(list))

# Cap what is kept per series: a long-running worker must not grow without bound
# because nothing scraped the histogram.
_MAX_OBSERVATIONS = 512


def inc(name: str, *labels: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name][labels] += value


def observe(name: str, *labels: str, value: float) -> None:
    with _lock:
        series = _observations[name][labels]
        series.append(value)
        if len(series) > _MAX_OBSERVATIONS:
            del series[: len(series) - _MAX_OBSERVATIONS]


def counter_value(name: str, *labels: str) -> float:
    with _lock:
        return _counters[name][labels] if labels in _counters[name] else 0.0


def snapshot() -> dict[str, dict[LabelValues, float]]:
    with _lock:
        return {name: dict(series) for name, series in _counters.items()}


def reset() -> None:
    with _lock:
        _counters.clear()
        _observations.clear()
