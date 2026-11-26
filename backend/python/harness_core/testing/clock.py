"""Manually driven implementation of :class:`harness_core.clock.Clock`."""

from __future__ import annotations

import asyncio
import heapq
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(order=True)
class _Timer:
    deadline: float
    seq: int
    callback: Callable[[], None] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class TimerHandle:
    def __init__(self, timer: _Timer) -> None:
        self._timer = timer

    @property
    def deadline(self) -> float:
        return self._timer.deadline

    def cancel(self) -> None:
        self._timer.cancelled = True


class ManualClock:
    """Time moves only when the test says so.

    Timers are keyed to the suspend-aware clock, so ``suspend(dt)`` fires
    everything that became due while the host slept (as a real resume would),
    while ``monotonic()`` does not move. Due timers fire in deadline order,
    ties in scheduling order, and each callback observes ``now`` equal to its
    own deadline.
    """

    def __init__(self, start: datetime = _EPOCH) -> None:
        self._wall = start
        self._monotonic = 0.0
        self._suspend_aware = 0.0
        self._timers: list[_Timer] = []
        self._seq = itertools.count()

    def now(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def suspend_aware(self) -> float:
        return self._suspend_aware

    def call_later(self, delay_s: float, callback: Callable[[], None]) -> TimerHandle:
        if delay_s < 0:
            raise ValueError("delay must be non-negative")
        timer = _Timer(self._suspend_aware + delay_s, next(self._seq), callback)
        heapq.heappush(self._timers, timer)
        return TimerHandle(timer)

    def pending_timers(self) -> int:
        return sum(1 for t in self._timers if not t.cancelled)

    def advance(self, dt_s: float) -> int:
        """Advance every clock by ``dt_s``; returns how many timers fired."""
        return self._move(dt_s, running=True)

    def suspend(self, dt_s: float) -> int:
        """Simulate host sleep: wall and suspend-aware time move, monotonic does not."""
        return self._move(dt_s, running=False)

    def jump(self, dt_s: float) -> None:
        """A wall-clock step (NTP, an operator, a restored VM), either way: only ``now()`` moves; the monotonic and
        suspend-aware clocks and every timer stay where they are (CH-14)."""
        self._wall += timedelta(seconds=dt_s)

    def _move(self, dt_s: float, *, running: bool) -> int:
        if dt_s < 0:
            raise ValueError("time only moves forward")
        target = self._suspend_aware + dt_s
        fired = 0
        while self._timers and self._timers[0].deadline <= target:
            timer = heapq.heappop(self._timers)
            if timer.cancelled:
                continue
            self._set(timer.deadline, running=running)
            timer.callback()
            fired += 1
        self._set(target, running=running)
        return fired

    def _set(self, suspend_aware: float, *, running: bool) -> None:
        step = suspend_aware - self._suspend_aware
        self._suspend_aware = suspend_aware
        self._wall += timedelta(seconds=step)
        if running:
            self._monotonic += step

    async def sleep(self, delay_s: float) -> None:
        """Awaitable that completes when the test advances past ``delay_s``."""
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def wake() -> None:
            if not future.done():
                future.set_result(None)

        handle = self.call_later(delay_s, wake)
        try:
            await future
        finally:
            handle.cancel()


async def settle(condition: Callable[[], bool], *, turns: int = 1_000) -> None:
    """Lets other tasks run until ``condition`` holds; fails after ``turns`` loop turns, never after seconds."""
    for _ in range(turns):
        if condition():
            return
        await asyncio.sleep(0)
    if not condition():
        raise AssertionError(f"condition not reached in {turns} loop turns")


__all__ = ["ManualClock", "TimerHandle", "settle"]
