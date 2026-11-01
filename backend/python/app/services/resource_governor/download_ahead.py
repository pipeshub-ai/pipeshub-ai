"""How many heavy records may hold downloaded bytes while they wait for a parse permit.

The index pools admit about twice as many records as the parse pools, so
without a bound every record past the parse slots downloaded its file and sat
on it for as long as the parses ahead of it took, minutes for Docling. A
record takes a slot here before it downloads and gives it back once it holds
its parse permit (from then on the parse gate and the memory budget account
for the bytes), so at most ``limit`` records hold content without one.
"""
from __future__ import annotations

import asyncio

from app.utils.env_config import env_int

# Heavy records only: their files are the large ones, and a heavy parse runs
# for seconds to minutes, so a couple of downloads ahead keep the parse slots
# fed. A light record parses faster than it downloads. 0 turns the bound off.
HEAVY_DOWNLOAD_AHEAD = env_int("INDEX_HEAVY_DOWNLOAD_AHEAD", 2)


class DownloadAhead:
    """A first come, first served counter of records downloading or holding content unparsed."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self._semaphore = asyncio.Semaphore(self.limit) if self.limit else None
        self._in_use = 0

    async def acquire(self, timeout: float | None = None) -> bool:
        """Take a slot; False if *timeout* passed first. Always True when the bound is off."""
        if self._semaphore is None:
            return True
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout)
        except TimeoutError:
            return False
        self._in_use += 1
        return True

    def release(self) -> None:
        if self._semaphore is None:
            return
        self._in_use -= 1
        self._semaphore.release()

    def stats(self) -> dict[str, int]:
        return {"limit": self.limit, "in_use": self._in_use}
