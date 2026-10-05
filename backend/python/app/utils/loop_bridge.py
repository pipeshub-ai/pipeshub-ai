"""Running a coroutine on the event loop that owns the client it uses.

The indexing consumers process messages on a worker-thread loop while the
broker clients (XACK, Kafka commit, the producer) belong to the main loop.
asyncio clients cannot be shared between loops: a second loop that awaits one
fails with "Future attached to a different loop". So the operation is handed to
the loop that owns the client instead.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def run_on_loop(
    loop: asyncio.AbstractEventLoop | None,
    coro: Coroutine[Any, Any, T],
    timeout: float | None = None,
) -> T:
    """Await ``coro`` on ``loop``, or directly when ``loop`` is None or already running here.

    Shielded: a caller that times out or is cancelled (record timeout, lease
    loss, shutdown) must not cancel the operation already in flight on the
    owning loop. redis-py tears down a connection whose command was cancelled
    mid-read, so under a mass cancellation each of those cancels became a
    reconnect against a pool that was already starved. Left to run, the
    operation is bounded by the client's own socket timeout; every caller is
    idempotent or already tolerates a late duplicate.
    """
    current_loop = asyncio.get_running_loop()
    if loop is None or loop is current_loop:
        return await coro
    if not loop.is_running():
        _close(coro)
        raise RuntimeError("The event loop that owns this client is not running")
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except BaseException:
        _close(coro)
        raise
    wrapped = asyncio.wrap_future(future)
    try:
        return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout)
    except BaseException:
        wrapped.add_done_callback(_consume_orphaned_result)
        raise


def _close(coro: object) -> None:
    # Only coroutines have close(); other awaitables are left alone.
    close = getattr(coro, "close", None)
    if close is not None:
        close()


def _consume_orphaned_result(fut: "asyncio.Future[Any]") -> None:
    """Retrieve the outcome of an operation its caller stopped waiting for,
    so asyncio does not log it as an unretrieved exception."""
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        logger.debug("Detached cross-loop operation failed after its caller gave up: %r", exc)
