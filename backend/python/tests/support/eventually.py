"""Waiting in an async test for work on another thread, without blocking the test's event loop."""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

T = TypeVar("T")


async def until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    """True once ``predicate()`` holds, or False when ``timeout`` seconds pass first.

    The timeout is only a safety net: it ends a test whose condition never comes true,
    so it can be generous without slowing a passing run, which returns on the first poll
    that sees the condition.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def done_without_waiting(coro: Coroutine[object, None, T]) -> T:
    """Run ``coro`` up to its first suspension, where it must already be done: "never waits" with no wall-clock bound to miss."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    raise AssertionError("it waited")
