"""Downstream health, as seen from this process, folded into one window per
governor sample.

The governor's control law reads CPU and memory, which say how much *this*
container can do. An index permit is mostly time spent waiting on other
services -- parsing, embedding, the graph, the vector store, the lease Redis
-- and each of those has a fixed pool behind it that this container cannot
see. When one of them starts refusing, timing out or running out of
connections, the only thing that helps is admitting fewer records, and
nothing told the governor to.

This module is the missing input. Every client that observes a downstream
symptom reports it here (a throttle, a timeout, an exhausted pool, an
unreachable service); the governor drains the window on each sample and
``policy.next_limits`` treats it as one more shrink rule for the index
pools. Reporting is one lock acquisition and never blocks, so it is safe
from any loop or thread -- the report sites run on the consumer's worker
loop, its main loop, and executor threads alike.

Process-wide default instance, for the same reason
``backpressure.get_default_backpressure_coordinator`` has one: the service
clients that observe these symptoms are constructed from many places, and
threading a handle through every constructor would touch far more of the
codebase than one accumulator warrants.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class FeedbackWindow:
    """What downstream reported during one sample interval.

    ``incident`` is the hard signal: a service asked us to back off, ran
    out of connections, or could not be reached at all. ``timeout_count``
    is the soft one -- a request that took too long may be one pathological
    document rather than a stalled dependency, so the policy only reacts to
    a few of them together.
    """

    throttles: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    timeouts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    pool_exhaustions: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    unavailable: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    @staticmethod
    def empty() -> FeedbackWindow:
        return FeedbackWindow()

    @property
    def incident(self) -> bool:
        return bool(self.throttles or self.pool_exhaustions or self.unavailable)

    @property
    def timeout_count(self) -> int:
        return sum(self.timeouts.values())

    @property
    def is_empty(self) -> bool:
        return not (self.incident or self.timeouts)

    def describe(self) -> str:
        parts = []
        for label, counts in (
            ("throttled", self.throttles),
            ("pool_exhausted", self.pool_exhaustions),
            ("unavailable", self.unavailable),
            ("timeouts", self.timeouts),
        ):
            if counts:
                parts.append(
                    f"{label}=" + ",".join(f"{name}:{n}" for name, n in sorted(counts.items()))
                )
        return " ".join(parts) or "none"

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            "throttles": dict(self.throttles),
            "timeouts": dict(self.timeouts),
            "pool_exhaustions": dict(self.pool_exhaustions),
            "unavailable": dict(self.unavailable),
        }


class DownstreamFeedback:
    """Thread-safe accumulator of downstream symptoms since the last drain."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._throttles: Counter[str] = Counter()
        self._timeouts: Counter[str] = Counter()
        self._pool_exhaustions: Counter[str] = Counter()
        self._unavailable: Counter[str] = Counter()

    def report_throttle(self, service: str) -> None:
        """The service said 429 / Retry-After: it is up, and asking for less."""
        with self._lock:
            self._throttles[service] += 1

    def report_timeout(self, service: str) -> None:
        """A request to the service took longer than its timeout."""
        with self._lock:
            self._timeouts[service] += 1

    def report_pool_exhausted(self, service: str) -> None:
        """A client-side pool to the service (driver connections, Redis
        connections) had nothing free within its wait."""
        with self._lock:
            self._pool_exhaustions[service] += 1

    def report_unavailable(self, service: str) -> None:
        """The service could not be reached, or gave up after its retries."""
        with self._lock:
            self._unavailable[service] += 1

    def drain(self) -> FeedbackWindow:
        """Return everything reported since the last drain, and reset."""
        with self._lock:
            window = FeedbackWindow(
                throttles=MappingProxyType(dict(self._throttles)),
                timeouts=MappingProxyType(dict(self._timeouts)),
                pool_exhaustions=MappingProxyType(dict(self._pool_exhaustions)),
                unavailable=MappingProxyType(dict(self._unavailable)),
            )
            self._throttles.clear()
            self._timeouts.clear()
            self._pool_exhaustions.clear()
            self._unavailable.clear()
        return window


class _Default:
    instance: DownstreamFeedback | None = None
    lock = threading.Lock()


def get_default_downstream_feedback() -> DownstreamFeedback:
    """The process-wide accumulator, created on first use."""
    with _Default.lock:
        if _Default.instance is None:
            _Default.instance = DownstreamFeedback()
        return _Default.instance


def set_default_downstream_feedback(feedback: DownstreamFeedback | None) -> None:
    """Override the process-wide default; ``None`` resets to lazy creation
    (test isolation)."""
    with _Default.lock:
        _Default.instance = feedback
