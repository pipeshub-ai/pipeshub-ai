"""A per-run cap on how fast an engine's events may arrive (SEC-DOS-02, C2.31, C6.39).

A token bucket: ``burst`` events at once, refilled at ``per_second``. An engine faster than that is throttled:
the reader waits, so the engine blocks writing its own output instead of the worker growing a queue. An
engine that stays throttled for ``max_throttled_s`` in total is flooding, not busy: ``EngineEventFlood`` ends
the attempt with a code the chat words for the person. Time and sleep are injected, so a test floods a
million events in well under a second.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

EVENT_FLOOD = "event_flood"
# The shortest wait a throttled reader sleeps: one sleep lets a batch of events through, not one each.
MIN_WAIT_S = 0.01


class EngineEventFlood(EngineRefusal):
    """An engine kept sending events faster than the run's cap for longer than it may be throttled."""

    reason_code = EVENT_FLOOD


@dataclass(frozen=True, slots=True)
class EventRate:
    """A sustained rate well above any engine's real output (a streamed command's lines arrive batched), and a
    burst that covers a turn's catch-up after a pause."""

    per_second: float = 2000.0
    burst: int = 20_000
    max_throttled_s: float = 30.0

    def __post_init__(self) -> None:
        if self.per_second <= 0 or self.burst < 1 or self.max_throttled_s < 0:
            raise ValueError("an event rate needs a positive rate and burst and a non-negative throttle budget")


class EventRateLimiter:
    """One run's bucket. ``take`` before handling each event; it waits while throttled and raises
    ``EngineEventFlood`` once the run has been throttled for longer than its budget."""

    def __init__(
        self,
        rate: EventRate,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._rate = rate
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(rate.burst)
        self._last = clock()
        self.events = 0
        self.throttled_s = 0.0

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(float(self._rate.burst), self._tokens + max(0.0, now - self._last) * self._rate.per_second)
        self._last = now

    async def take(self) -> None:
        self.events += 1
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return
        wait = max(MIN_WAIT_S, (1 - self._tokens) / self._rate.per_second)
        if self.throttled_s + wait > self._rate.max_throttled_s:
            raise EngineEventFlood(
                f"the engine sent events faster than {self._rate.per_second:g}/s for over "
                f"{self._rate.max_throttled_s:g} s"
            )
        self.throttled_s += wait
        await self._sleep(wait)
        self._refill()
        # What the wait was owed, whether or not the clock moved: a clock that stands still never refills.
        self._tokens = max(self._tokens, wait * self._rate.per_second) - 1


__all__ = ["EVENT_FLOOD", "EngineEventFlood", "EventRate", "EventRateLimiter"]
