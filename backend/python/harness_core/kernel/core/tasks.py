"""Waiting on tasks the caller owns."""

from __future__ import annotations

import asyncio
from typing import Any


async def wait_owned(
    tasks: set[asyncio.Task[Any]], *, return_when: str = asyncio.ALL_COMPLETED,
) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
    """`asyncio.wait` for tasks the caller owns.

    `asyncio.wait` leaves its tasks running when the caller is cancelled. Here each task is
    cancelled too, once, and the caller waits for it to finish its cleanup before the cancel is
    re-raised. The wait is shielded because a disconnect cancels every layer of a turn: a second
    cancel reaching a task's cleanup stops httpx from closing the provider connection. So if the
    caller is cancelled again, it returns before the tasks finish; they still finish on their
    single cancel.
    """
    try:
        return await asyncio.wait(tasks, return_when=return_when)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        raise
