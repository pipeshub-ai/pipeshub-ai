"""The install's ceiling on live sandboxes (C2.19), enforced where every sandbox is made: the broker.

Admission bounds runs per org and per actor; this bounds sandboxes across them all. ``live`` counts every
sandbox of the broker's namespace whichever broker process made it, so a second broker sees the first one's.
Creates this process has admitted but the backend may not list yet count too; one already listed counts twice
until it is recorded, which can only make the ceiling stricter for that moment, never looser.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from harness_core.sandbox.errors import BrokerUnavailable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable


class SandboxCap:
    """``limit`` None is no ceiling."""

    def __init__(self, limit: int | None, live: Callable[[], Awaitable[int]]) -> None:
        if limit is not None and limit <= 0:
            raise ValueError("a sandbox ceiling is a positive number; None is none")
        self.limit = limit
        self._live = live
        self._inflight = 0
        self._lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def admit(self) -> AsyncGenerator[None]:
        """One more live sandbox, or ``BrokerUnavailable``: capacity, which a run waits out (QA31F)."""
        if self.limit is None:
            yield
            return
        async with self._lock:
            live = await self._live() + self._inflight
            if live >= self.limit:
                raise BrokerUnavailable(f"sandbox capacity reached: {live} of {self.limit} live")
            self._inflight += 1
        try:
            yield
        finally:
            self._inflight -= 1


__all__ = ["SandboxCap"]
