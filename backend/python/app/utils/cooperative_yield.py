"""Hand the event loop back during a long synchronous loop that must stay on the loop.

A loop that has no ``await`` of its own holds the event loop for its whole run: on a
workbook with a few hundred thousand cells that is long enough to stall every other
record in flight and the loop's own timers. :class:`CooperativeYield` lets such a loop
pause between iterations.

Work is counted in *cells* (or whatever the loop's unit of work is), never in rows: a
row of 16,000 columns is a thousand times heavier than a row of 16. The clock is read
only every ``check_every`` units, and the loop yields once ``budget_seconds`` of wall
time have passed since the last yield (or the start), so the cost on a fast loop is a
counter add per unit and the cost on a slow one is a ``sleep(0)`` every few milliseconds.

Usage, in an ``async def`` loop::

    pacer = CooperativeYield()
    for row in rows:
        for cell in row:
            handle(cell)
            if pacer.due():
                await pacer.pause()

``due`` is a plain method (no coroutine is created per unit); ``pause`` is the
``asyncio.sleep(0)``. ``await pacer.tick(n)`` does both for a loop whose unit of work is
already ``n`` cells.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# TODO(PX-3 M3): set from the Excel post-load measurement on a 290,000-row and a
# 16,000-column workbook. Placeholders: yield about every 10 ms, read the clock every
# 32 units (a clock read is about 50 ns; a cell is about 5 us and a Block about 50 us, so the overshoot past the budget stays a few ms).
DEFAULT_BUDGET_SECONDS = 0.010
DEFAULT_CHECK_EVERY = 32


class CooperativeYield:
    """Counts units of work and says when the loop should let others run."""

    __slots__ = ("_budget", "_check_every", "_clock", "_last", "_pending")

    def __init__(
        self,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        check_every: int = DEFAULT_CHECK_EVERY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if check_every < 1:
            raise ValueError("check_every must be at least 1")
        if budget_seconds < 0:
            raise ValueError("budget_seconds must not be negative")
        self._budget = budget_seconds
        self._check_every = check_every
        self._clock = clock
        self._pending = 0
        self._last = clock()

    def due(self, units: int = 1) -> bool:
        """Record ``units`` of work done; True when the loop should now ``await pause()``."""
        self._pending += units
        if self._pending < self._check_every:
            return False
        self._pending = 0
        return self._clock() - self._last >= self._budget

    async def pause(self) -> None:
        """Let every other ready task run once, then restart the budget."""
        await asyncio.sleep(0)
        self._pending = 0
        self._last = self._clock()

    async def tick(self, units: int = 1) -> None:
        """``due`` then ``pause``, for a loop whose iteration is already ``units`` of work."""
        if self.due(units):
            await self.pause()
