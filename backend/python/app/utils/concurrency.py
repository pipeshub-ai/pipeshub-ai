"""Bounded-concurrency helpers for the indexing pipeline."""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Awaitable, List, TypeVar, cast

if TYPE_CHECKING:
    from app.services.llm_gateway.gateway import SlotHealth
    from app.services.llm_gateway.limiter import CrossLoopSemaphore

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

# Graph queries one fan-out (one query per record or per relation pair) runs at
# once. Each concurrent query can open a new database connection, and a burst
# wider than the server's accept backlog has its connects dropped until
# aiohttp's 30s sock_connect fails them together ("Connection timeout to host").
GRAPH_FANOUT_CONCURRENCY = 16

TABLE_ROW_BATCH_SIZE = 50


def max_table_rows_for_llm() -> int:
    """Table rows per record that get an LLM-written description.

    Running total across the record's tables; rows past it get deterministic
    "column: value" text. Every table parser reads the cap from here.
    """
    return max(0, int(os.getenv("MAX_TABLE_ROWS_FOR_LLM", "1000")))

def _indexing_llm_semaphore() -> "CrossLoopSemaphore":
    """The LLM gateway's cap: one per process, shared by every event loop in it.

    A semaphore per loop multiplied the budget by the number of loops (the server
    loop and the worker loop).
    """
    # Imported here: the gateway sizes its cap from this module.
    from app.services.llm_gateway.gateway import get_llm_gateway

    return get_llm_gateway().limiter


def indexing_llm_stats() -> "SlotHealth":
    """The /health ``indexing_llm`` block: use of the cap, wait and hold time, rate-limit retries."""
    from app.services.llm_gateway.gateway import get_llm_gateway

    return get_llm_gateway().slot_stats()


def note_indexing_llm_rate_limit() -> None:
    from app.services.llm_gateway.gateway import get_llm_gateway

    get_llm_gateway().note_rate_limit_retry()


def indexing_llm_slots_remaining() -> int:
    """How many indexing LLM calls can start without waiting, process-wide.

    Used to skip the multi-turn extractor when the process is already saturated.
    """
    sem = _indexing_llm_semaphore()
    return max(0, sem.limit - sem.in_use)


@asynccontextmanager
async def indexing_llm_slot() -> AsyncGenerator[None, None]:
    """Hold a permit of the process-wide indexing LLM cap (the LLM gateway's).

    For a caller that makes its model call itself; ``LLMGateway.invoke`` takes the
    same permit and adds the breaker, the hang ceiling and rate-limit retries.
    """
    async with _indexing_llm_semaphore().slot():
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
