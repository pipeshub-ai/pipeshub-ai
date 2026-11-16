"""Run one task per image or batch, and leave nothing running when one of them raises."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable

_T = TypeVar("_T")


async def gather_or_cancel(awaitables: list[Awaitable[_T]]) -> list[_T]:
    """``asyncio.gather`` that cancels and awaits the other tasks when one raises, before the exception propagates.

    A bare ``gather`` leaves its other children running (or waiting for a semaphore), so they would go on to make paid
    requests after the caller already holds the error, and their results would be thrown away.
    """
    tasks = [asyncio.ensure_future(item) for item in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
