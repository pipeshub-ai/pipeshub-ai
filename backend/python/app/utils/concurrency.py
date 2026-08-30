"""Bounded-concurrency helpers for the indexing pipeline."""

import asyncio
import os
import threading
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


@asynccontextmanager
async def indexing_llm_slot() -> AsyncGenerator[None, None]:
    """Hold a slot in the process-wide indexing LLM budget."""
    async with _indexing_llm_semaphore():
        yield


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
