"""``DownloadAhead.try_acquire``: a place only when one is free and nobody is queued; it never waits."""
from __future__ import annotations

import asyncio

from app.services.resource_governor.download_ahead import DownloadAhead


async def test_it_takes_a_free_place_and_refuses_a_full_pool_without_waiting() -> None:
    ahead = DownloadAhead(1)
    assert await asyncio.wait_for(ahead.try_acquire(), 0.5) is True
    assert await asyncio.wait_for(ahead.try_acquire(), 0.5) is False  # full: refused at once, not queued
    assert ahead.waiting == 0 and ahead.stats()["in_use"] == 1
    ahead.release()


async def test_it_never_jumps_ahead_of_a_queued_download() -> None:
    ahead = DownloadAhead(1)
    assert await ahead.acquire(0.5)
    queued = asyncio.create_task(ahead.acquire(2.0))
    await asyncio.sleep(0.05)
    assert ahead.waiting == 1
    ahead.release()  # the place is handed to the queued download, which has not run yet
    assert await asyncio.wait_for(ahead.try_acquire(), 0.5) is False  # a mover does not take it
    assert await asyncio.wait_for(queued, 1.0) is True
    assert ahead.stats()["in_use"] == 1
    ahead.release()


async def test_with_the_bound_off_it_always_succeeds() -> None:
    assert await DownloadAhead(0).try_acquire() is True
