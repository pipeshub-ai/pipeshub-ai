"""Run a synchronous call in a thread without losing it to a cancellation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

from app.core.org_context import spawn_in_request

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")


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
