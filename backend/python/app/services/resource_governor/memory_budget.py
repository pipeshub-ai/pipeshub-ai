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
    """A budget of bytes, bound to the event loop that first waits on it.
    ``limit`` comes from the governor's samples; with none (memory unknown)
    every reservation is admitted.

    A sample of free memory already lacks what running records have
    allocated, so a running record counts twice: there and in its
    reservation. That is on purpose: a record admitted since the last sample
    has allocated nothing yet, and nothing tells how much of its reservation
    a running record has used. Big records run fewer at a time than memory
    would hold, never more.

    Waiters are served first come, first served, except that one already
    holding its content (a record that downloaded before it could reserve)
    goes ahead of those holding none: its memory is in use either way, and
    running it first frees it soonest. When nothing is reserved the head is
    admitted whatever it needs, so no record waits for ever: no running record
    would free memory, and the content other waiters hold only goes when they
    run or give up.
    """

    def __init__(self) -> None:
        self.limit: int | None = None
        self._reserved = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: deque[_Waiter] = deque()
        # What each waiter already holds, and their total, counted against
        # everyone else. stats() reads the total from another thread, so it
        # is kept as one int rather than summed over the dict.
        self._held_by: dict[_Waiter, int] = {}
        self._held = 0

    def _fits(self, nbytes: int, held: int) -> bool:
        if self.limit is None or self._reserved == 0:
            return True
        # The caller's own held bytes are part of *nbytes*, not in addition to it.
        return self._reserved + self._held - held + nbytes <= self.limit

    def _enqueue(self, waiter: _Waiter, held: int) -> None:
        position = len(self._waiters)
        if held:
            position = next((i for i, w in enumerate(self._waiters) if not self._held_by[w]), position)
        self._waiters.insert(position, waiter)
        self._held_by[waiter] = held
        self._held += held

    async def reserve(self, nbytes: int, timeout: float | None = None, *, held: int = 0) -> bool:
        """Reserve *nbytes*; False if *timeout* passed first.

        *held* is what the caller already holds and is part of *nbytes*: a
        record that reserves after its download. It counts against the budget
        while the caller waits, so waiting records cannot hide their buffers
        between the governor's samples.
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
        self._enqueue(waiter, held)
        try:
            while True:
                if self._waiters[0] is waiter and self._fits(nbytes, held):
                    self._reserved += nbytes
                    return True
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    return False
                await waiter.wait(_RECHECK_SECONDS if remaining is None else min(_RECHECK_SECONDS, remaining))
        finally:
            self._held -= self._held_by.pop(waiter, 0)
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
