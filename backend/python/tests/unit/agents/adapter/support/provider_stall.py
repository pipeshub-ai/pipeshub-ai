"""A provider read that never returns until it is cancelled, for the client-disconnect tests.

A disconnect cancels every layer of a turn, and a layer that forwards each of those cancels to
the task reading the provider cuts httpcore's close short: the connection stays open.
`ProviderStall` makes that observable without a socket.
"""

from __future__ import annotations

import asyncio
from typing import Never


class ProviderStall:
    """Stands in for the task reading a provider stream. `read()` blocks until cancelled; its
    cleanup then awaits twice before it marks the connection `closed`, like httpcore's
    lock-protected close, so a second cancel delivered during the cleanup leaves it open."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.task: asyncio.Task[object] | None = None
        self.cancels = 0
        self.closed = False

    async def read(self) -> Never:
        self.task = asyncio.current_task()
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancels += 1
            try:
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                self.cancels += 1
                raise
            self.closed = True
            raise
        raise AssertionError("unreachable: the stall only ends by a cancel")

    async def settle(self, ticks: int = 50) -> None:
        """Run the loop until the reading task has finished, for at most `ticks` iterations."""
        for _ in range(ticks):
            if self.task is not None and self.task.done():
                return
            await asyncio.sleep(0)


async def pending_since(before: set[asyncio.Task[object]]) -> list[asyncio.Task[object]]:
    """Tasks created after `before` still pending one loop iteration from now (time for a task
    that was already cancelled to finish). They are cancelled and awaited, so a failing test does
    not leak "Task was destroyed but it is pending" into later ones."""
    await asyncio.sleep(0)
    leftover = [t for t in asyncio.all_tasks() - before if not t.done()]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)
    return leftover
