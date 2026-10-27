"""Free memory reserved by records too big for the governor's count limits.

The gates count records, and the policy sizes each index pool by what free
memory holds of an *assumed* record (``INDEX_*_WORKING_SET_GB``). A record far
above that, a page carrying hundreds of megabytes of images, was admitted like
any other, and a few of them in flight together outgrew the container. Such a
record reserves what it is expected to hold out of the free memory the
governor last measured, and waits while that is spoken for.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections import deque

from app.services.resource_governor.gate import _Waiter
from app.services.resource_governor.models import ParseTier
from app.services.resource_governor.policy import (
    INDEX_HEAVY_WORKING_SET_GB,
    INDEX_LIGHT_WORKING_SET_GB,
)

# A record's own peak as a multiple of its size. A web page of inline photos,
# the worst case measured, peaks at 2.5x (its bytes and text while its images
# are shrunk); rounded up for the formats not measured. Images fetched while a
# page is parsed are not in its size: the per-record image budget bounds those.
RECORD_MEMORY_FACTOR = 4
_GIB = 1024 ** 3
# Between re-checks of a waiter nobody woke; a new limit is set from another
# thread and does not wake anyone.
_RECHECK_SECONDS = 1.0


def record_memory_cost(tier: ParseTier, size_bytes: int | None) -> int:
    """Bytes a record reserves: its estimate when that is above what the
    governor already assumes a record of its tier holds, else 0."""
    if not size_bytes:
        return 0
    estimate = size_bytes * RECORD_MEMORY_FACTOR
    assumed = (INDEX_HEAVY_WORKING_SET_GB if tier is ParseTier.HEAVY else INDEX_LIGHT_WORKING_SET_GB) * _GIB
    return estimate if estimate > assumed else 0


class MemoryBudget:
    """A first-come, first-served budget of bytes, bound to the event loop
    that first waits on it. ``limit`` comes from the governor's samples; with
    none (memory unknown) every reservation is admitted. A reservation larger
    than the whole budget is admitted alone, so no record waits for ever.
    """

    def __init__(self) -> None:
        self.limit: int | None = None
        self._reserved = 0
        # Bytes waiters already hold: a record waits here after its download.
        self._held = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: deque[_Waiter] = deque()

    def _fits(self, nbytes: int, held: int) -> bool:
        if self.limit is None or self._reserved == 0:
            return True
        # The caller's own held bytes are part of *nbytes*, not in addition to it.
        return self._reserved + self._held - held + nbytes <= self.limit

    async def reserve(self, nbytes: int, timeout: float | None = None, *, held: int = 0) -> bool:
        """Reserve *nbytes*; False if *timeout* passed first.

        *held* is what the caller already holds and is part of *nbytes*: a
        record that waits here has downloaded its content. It counts against
        the budget while the caller waits, so a queue of waiting records
        cannot hide their buffers between the governor's samples.
        """
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif loop is not self._loop:
            raise RuntimeError("MemoryBudget is bound to another event loop")
        if not self._waiters and self._fits(nbytes, 0):
            self._reserved += nbytes
            return True
        deadline = None if timeout is None else loop.time() + timeout
        waiter = _Waiter(loop)
        self._waiters.append(waiter)
        self._held += held
        try:
            while True:
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    return False
                await waiter.wait(_RECHECK_SECONDS if remaining is None else min(_RECHECK_SECONDS, remaining))
                if self._waiters[0] is waiter and self._fits(nbytes, held):
                    self._reserved += nbytes
                    return True
        finally:
            self._held -= held
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)
            self._wake_head()

    def release(self, nbytes: int) -> None:
        self._reserved = max(0, self._reserved - nbytes)
        self._wake_head()

    def _wake_head(self) -> None:
        if self._waiters:
            self._waiters[0].wake()

    def stats(self) -> dict[str, int | None]:
        return {
            "limit_bytes": self.limit,
            "reserved_bytes": self._reserved,
            "waiting": len(self._waiters),
            "waiting_held_bytes": self._held,
        }
