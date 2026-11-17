"""Run one task per image or batch, and leave nothing running when one of them raises."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

from app.core.org_context import spawn_in_request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_T = TypeVar("_T")


async def _run(item: Awaitable[_T]) -> _T:
    return await item


async def to_thread_settled(fn: Callable[[], _T]) -> _T:
    """``asyncio.to_thread`` that waits for its thread before a cancellation goes on.

    A cancelled ``to_thread`` cannot stop the synchronous SDK call already running in its thread; the call would still send,
    after the caller was told the work was cancelled and with nobody left to count the request. The thread is kept running
    to its end (bounded by the SDK's own timeout), and only then is the cancellation raised, so the request is counted by the
    wire scope of the attempt and no request leaves after the hand-back.
    """
    task = spawn_in_request(_in_thread(fn), name="image_embedding_thread")
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            await asyncio.wait({task})
        raise


async def _in_thread(fn: Callable[[], _T]) -> _T:
    return await asyncio.to_thread(fn)


async def gather_or_cancel(awaitables: list[Awaitable[_T]]) -> list[_T]:
    """``asyncio.gather`` that cancels and awaits the other tasks when one raises, before the exception propagates.

    A bare ``gather`` leaves its other children running (or waiting for a semaphore), so they would go on to make paid
    requests after the caller already holds the error, and their results would be thrown away.
    """
    tasks = [spawn_in_request(_run(item), name="image_embedding") for item in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
