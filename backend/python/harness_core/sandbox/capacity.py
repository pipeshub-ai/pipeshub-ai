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
    """``limit`` None is no ceiling. A callable is read at each admission: a ceiling sized from the daemon starts at
    its minimum and rises once the daemon has been read."""

    def __init__(self, limit: int | Callable[[], int] | None, live: Callable[[], Awaitable[int]]) -> None:
        if isinstance(limit, int) and limit <= 0:
            raise ValueError("a sandbox ceiling is a positive number; None is none")
        self._limit = limit
        self._live = live
        self._inflight = 0
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int | None:
        return self._limit() if callable(self._limit) else self._limit

    @contextlib.asynccontextmanager
    async def admit(self) -> AsyncGenerator[None]:
        """One more live sandbox, or ``BrokerUnavailable``: capacity, which a run waits out (QA31F)."""
        limit = self.limit
        if limit is None:
            yield
            return
        async with self._lock:
            live = await self._live() + self._inflight
            if live >= limit:
                raise BrokerUnavailable(f"sandbox capacity reached: {live} of {limit} live")
            self._inflight += 1
        try:
            yield
        finally:
            self._inflight -= 1


__all__ = ["SandboxCap"]
