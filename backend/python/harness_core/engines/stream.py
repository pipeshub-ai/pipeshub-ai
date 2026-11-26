"""Building blocks for ``EngineSession.run_turn``: per-turn sequence numbers and a bounded buffer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.engines.events import EngineEvent, EngineEventMeta, TurnCompleted

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DEFAULT_BUFFER = 256


class TurnSeq:
    """Hands out ``EngineEventMeta`` for one turn, ``seq`` from 0 with no gaps."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._next = 0

    def next(self) -> EngineEventMeta:
        meta = EngineEventMeta(turn_id=self.turn_id, seq=self._next)
        self._next += 1
        return meta

    @property
    def issued(self) -> int:
        return self._next


class _Closed:
    pass


_CLOSED = _Closed()


class EventBuffer:
    """Producer → consumer buffer for one turn. ``put`` waits when full (backpressure)."""

    def __init__(self, maxsize: int = DEFAULT_BUFFER) -> None:
        self._queue: asyncio.Queue[EngineEvent | _Closed] = asyncio.Queue(maxsize=maxsize)

    @property
    def maxsize(self) -> int:
        return self._queue.maxsize

    def qsize(self) -> int:
        return self._queue.qsize()

    async def put(self, event: EngineEvent) -> None:
        await self._queue.put(event)

    async def close(self) -> None:
        await self._queue.put(_CLOSED)

    async def drain(self) -> AsyncIterator[EngineEvent]:
        """Yield events until a ``TurnCompleted`` (inclusive) or ``close``."""
        while True:
            item = await self._queue.get()
            if isinstance(item, _Closed):
                return
            yield item
            if isinstance(item, TurnCompleted):
                return


__all__ = ["DEFAULT_BUFFER", "EventBuffer", "TurnSeq"]
