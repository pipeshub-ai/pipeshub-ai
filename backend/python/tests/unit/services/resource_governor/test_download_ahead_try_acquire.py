"""``DownloadAhead.try_acquire``: a place only when one is free and nobody is queued; it never waits."""
from __future__ import annotations

import asyncio

from app.services.resource_governor.download_ahead import DownloadAhead
from tests.support.eventually import done_without_waiting


async def test_it_takes_a_free_place_and_refuses_a_full_pool_without_waiting() -> None:
    ahead = DownloadAhead(1)
    assert done_without_waiting(ahead.try_acquire()) is True
    assert done_without_waiting(ahead.try_acquire()) is False  # full: refused at once, not queued
    assert ahead.waiting == 0 and ahead.stats()["in_use"] == 1
    ahead.release()


async def test_it_never_jumps_ahead_of_a_queued_download() -> None:
    ahead = DownloadAhead(1)
    assert await ahead.acquire(0.5)
    queued = asyncio.create_task(ahead.acquire(10.0))
    await asyncio.sleep(0.05)
    assert ahead.waiting == 1
    ahead.release()  # the place is handed to the queued download, which has not run yet
    assert done_without_waiting(ahead.try_acquire()) is False  # a mover does not take it
    assert await queued is True  # handed the place: not left to its own deadline
    assert ahead.stats()["in_use"] == 1
    ahead.release()


async def test_with_the_bound_off_it_always_succeeds() -> None:
    assert await DownloadAhead(0).try_acquire() is True
