"""Bounded-concurrency helpers for the indexing pipeline."""

import asyncio
import os
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Awaitable, List, TypeVar, cast
from weakref import WeakKeyDictionary

from app.utils.worker_scaling import scaled

T = TypeVar("T")

# Process-wide ceiling on in-flight indexing LLM calls. Fan-out is three levels deep
# (records x tables x row batches), so without this the provider sees hundreds of
# concurrent requests. This is the knob to turn down when a provider starts 429ing.
MAX_CONCURRENT_INDEXING_LLM_CALLS = max(
    1, int(os.getenv("MAX_CONCURRENT_INDEXING_LLM_CALLS", "24"))
)

# Per-level caps. These bound task *creation* and keep tail latency sane: a document
# finishes its tables a few at a time rather than leaving all of them 2% done.
MAX_CONCURRENT_TABLES = 15
MAX_CONCURRENT_ROW_BATCHES = 8
MAX_CONCURRENT_PAGE_BUILDS = 4

TABLE_ROW_BATCH_SIZE = 50

_sem_by_loop: "WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    WeakKeyDictionary()
)
_sem_lock = threading.Lock()


def _indexing_llm_semaphore() -> asyncio.Semaphore:
    """Return the semaphore bound to the running loop, creating it on first use.

    This process runs several event loops: the uvicorn loop plus the Kafka and Redis
    indexing consumers, which each call ``asyncio.new_event_loop()`` in a worker thread.
    ``asyncio.Semaphore`` binds to a loop on first await and then raises if awaited from
    another, so a single module-level instance would crash. The double-checked init is
    guarded by a threading lock because those loops live in different threads.
    """
    loop = asyncio.get_running_loop()
    sem = _sem_by_loop.get(loop)
    if sem is None:
        with _sem_lock:
            sem = _sem_by_loop.get(loop)
            if sem is None:
                sem = asyncio.Semaphore(scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS))
                _sem_by_loop[loop] = sem
    return sem


class _SlotStats:
    """Process-wide totals for the indexing LLM slots, summed over every loop.

    Wait time against hold time is what tells our own slot budget apart from
    a provider limit: slots saturated with long waits and steady call times
    mean the budget binds; rising call times or rate-limit retries mean the
    provider does.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.in_use = 0
        self.waiting = 0
        self.acquired = 0
        self.wait_s = 0.0
        self.hold_s = 0.0
        self.rate_limit_retries = 0

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            acquired = self.acquired
            # hold_s only grows on release, so in-flight calls must not dilute its mean.
            released = acquired - self.in_use
            return {
                "slots_per_loop": scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS),
                "loops": len(_sem_by_loop),
                "in_use": self.in_use,
                "waiting": self.waiting,
                "acquired": acquired,
                "rate_limit_retries": self.rate_limit_retries,
                "wait_s_total": round(self.wait_s, 1),
                "hold_s_total": round(self.hold_s, 1),
                "wait_s_mean": round(self.wait_s / acquired, 3) if acquired else 0.0,
                "hold_s_mean": round(self.hold_s / released, 3) if released else 0.0,
            }


_slot_stats = _SlotStats()


def indexing_llm_stats() -> dict[str, float | int]:
    return _slot_stats.snapshot()


def note_indexing_llm_rate_limit() -> None:
    with _slot_stats._lock:
        _slot_stats.rate_limit_retries += 1


@asynccontextmanager
async def indexing_llm_slot() -> AsyncGenerator[None, None]:
    """Hold a slot in the process-wide indexing LLM budget."""
    stats = _slot_stats
    sem = _indexing_llm_semaphore()
    with stats._lock:
        stats.waiting += 1
    asked = time.perf_counter()
    try:
        await sem.acquire()
    finally:
        with stats._lock:
            stats.waiting -= 1
    granted = time.perf_counter()
    with stats._lock:
        stats.in_use += 1
        stats.acquired += 1
        stats.wait_s += granted - asked
    try:
        yield
    finally:
        sem.release()
        with stats._lock:
            stats.in_use -= 1
            stats.hold_s += time.perf_counter() - granted


async def gather_with_concurrency(
    limit: int,
    *awaitables: Awaitable[T],
    return_exceptions: bool = False,
) -> List[T]:
    """``asyncio.gather`` with at most *limit* awaitables running at once.

    Results are in argument order, matching ``asyncio.gather``.
    """
    if not awaitables:
        return []

    semaphore = asyncio.Semaphore(max(1, limit))

    async def _run(awaitable: Awaitable[T]) -> T:
        async with semaphore:
            return await awaitable

    results = await asyncio.gather(
        *(_run(a) for a in awaitables), return_exceptions=return_exceptions
    )
    # With return_exceptions=True the caller is expected to narrow the entries itself.
    return cast(List[T], results)
