"""``RunReadySignal``: idle claimers hear that runs became claimable (NFR-48).

Node announces each dispatch record after the transaction that wrote it commits
(LAT1). A notice is a hint: the claim through Node stays the queue, and a lost
notice costs one poll interval, never the run.
"""

from __future__ import annotations

import asyncio
from typing import Protocol


class RunReadySignal(Protocol):
    @property
    def healthy(self) -> bool:
        """``True`` while notices can arrive; a claimer polls more often otherwise."""
        ...

    async def wait(self, timeout_s: float) -> bool:
        """``True`` once a run became claimable since the previous wait returned
        (notices in between coalesce into one); ``False`` after ``timeout_s``."""
        ...


class InMemoryRunReadySignal:
    """``RunReadySignal`` within one process; ``notify`` is what Node's publish does."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def healthy(self) -> bool:
        return True

    def notify(self) -> None:
        self._event.set()

    async def wait(self, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout_s)
        except TimeoutError:
            return False
        self._event.clear()
        return True


__all__ = ["InMemoryRunReadySignal", "RunReadySignal"]
