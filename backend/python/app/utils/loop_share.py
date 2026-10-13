"""Hold background sweeps to a share of an event loop that indexing needs.

On Neo4j the indexing service runs its sweeps (record label repair, the
entity-index rebuild, the orphan scan in stale recovery) on the worker loop
that runs every in-flight record. Each sweep step is short, but the steps come
back to back, so a sweep that is working through a large tenant can hold the
loop for a large share of its time: every record's await then resumes late,
and its slot stays held while the host's CPUs sit idle.

``LoopShare`` paces a sweep between units of work. After each unit it yields
once and measures how long the rest of the loop took to hand control back. If
other work was waiting, it sleeps long enough that the sweep holds at most
``share`` of the loop's wall time, measured from the unit's own duration; on an
idle loop it only yields, so a sweep runs as fast as it did before. The sweep
still does every unit of work, in the same order: only the gaps between units
change.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Bound at import: tests that patch asyncio.sleep to count a sweep's interval
# waits must not count these as well.
_sleep = asyncio.sleep

DEFAULT_SHARE = 0.1
# Handing control back after one yield takes about this long on an idle loop
# (tens of microseconds); longer means other callbacks were ready and ran first.
BUSY_PROBE_SECONDS = 0.002


class LoopShare:
    def __init__(
        self,
        share: float = DEFAULT_SHARE,
        *,
        max_pause_seconds: float,
        busy_probe_seconds: float = BUSY_PROBE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = _sleep,
    ) -> None:
        if not 0 < share <= 1:
            raise ValueError(f"share must be in (0, 1], got {share}")
        self.share = share
        self.max_pause_seconds = max(0.0, max_pause_seconds)
        self.busy_probe_seconds = busy_probe_seconds
        self._clock = clock
        self._sleep = sleep
        self._started = clock()

    def begin(self) -> None:
        """Start timing a unit of work now, so an idle wait before it is not counted."""
        self._started = self._clock()

    async def pause(self) -> float:
        """End the current unit of work and start the next; returns the seconds paused."""
        elapsed = self._clock() - self._started
        probe = self._clock()
        await self._sleep(0)
        paused = 0.0
        if self._clock() - probe >= self.busy_probe_seconds:
            paused = min(self.max_pause_seconds, elapsed * (1 - self.share) / self.share)
            if paused > 0:
                await self._sleep(paused)
        self._started = self._clock()
        return paused


__all__ = ["BUSY_PROBE_SECONDS", "DEFAULT_SHARE", "LoopShare"]
