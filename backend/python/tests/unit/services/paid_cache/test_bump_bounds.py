"""Bounds: the bump never slows a hit, and the work and bytes it adds are capped (the bench's gates, in-process)."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.paid_cache import bump
from app.services.paid_cache.bump import BumpEntry, BumpQueue
from app.services.paid_cache.cache import PaidResultCache, _encode
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import record_in_flight
from tests.support.versioned_store import VersionedStore

ORG, REC = "org-1", "rec-1"
OLD_MS = 2 * 3_600_000


def _path(n: int) -> str:
    return f"ocr_page/{n:064x}.json"


def _entry(n: int, size: int = 100) -> BumpEntry:
    return BumpEntry(ORG, REC, _path(n), b"x" * size, "e", 0, None)


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
    monkeypatch.setenv("PAID_CACHE_WRITE", "true")
    yield
    clear_run()


async def test_a_slow_store_does_not_slow_a_hit() -> None:
    now = int(time.time() * 1000)
    store = VersionedStore(stamp_ms=1, now_ms=now)
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store.write(ORG, REC, key.relative_path(), _encode(key, "text"), age_ms=OLD_MS)
    slow = store.bump_to
    upload_started, release = asyncio.Event(), asyncio.Event()

    async def held_bump(entry: Any, data: bytes, charge: Any = None, eligible: Any = None) -> tuple[str, str | None]:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        upload_started.set()
        await release.wait()                    # the upload is held until the hit has returned
        return await slow(entry, data, charge, eligible)

    store.bump_to = held_bump  # type: ignore[method-assign]
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    try:
        with record_in_flight(ORG, REC):
            begin_run(ORG, REC, "rev1", "parse")
            hit = asyncio.ensure_future(cache.get_or_compute(key, AsyncMock()))
            # The hit completes although the upload it queued has not: and the upload really started (a cache that never
            # queued a bump would pass a timing check, not this one).
            assert await asyncio.wait_for(upload_started.wait(), 10) is True
            assert await asyncio.wait_for(hit, 10) == "text"
            assert not release.is_set()
    finally:
        release.set()
    await asyncio.wait_for(cache.bumps.wait_idle(), 10)


def test_the_queue_is_bounded_in_entries_and_in_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bump, "QUEUE_MAX", 3)
    queue = BumpQueue(VersionedStore(), autostart=False)  # type: ignore[arg-type]
    assert [queue.offer(_entry(n)) for n in range(5)] == ["queued"] * 3 + ["dropped"] * 2
    monkeypatch.setattr(bump, "QUEUE_MAX", 100)
    monkeypatch.setattr(bump, "QUEUE_BYTES_MAX", 420)
    small = BumpQueue(VersionedStore(), autostart=False)  # type: ignore[arg-type]
    assert [small.offer(_entry(n, 100)) for n in range(3)] == ["queued", "queued", "dropped"]


async def test_bytes_re_uploaded_are_capped_per_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bump, "BYTES_PER_HOUR", 1000)
    store = VersionedStore(stamp_ms=1)
    queue = BumpQueue(store, autostart=False, sleep=AsyncMock(), budget=bump.ByteBudget())  # type: ignore[arg-type]
    outcomes = []
    for n in range(6):
        store.write(ORG, REC, _path(n), b'{"v":1,"value":"' + b"x" * 200 + b'"}', age_ms=OLD_MS)
        got = await store.get_versioned(ORG, REC, _path(n))
        assert got is not None
        outcomes.append(queue.offer(BumpEntry(ORG, REC, _path(n), got.data, got.etag or "", got.last_modified_ms or 0, None)))
    # Each entry is about 218 bytes and TOUCH_OVERHEAD more is reserved: three fit under 1000, the next three do not.
    assert outcomes == ["queued"] * 3 + ["over_budget"] * 3
    store.ops.clear()
    await queue.drain_once()
    assert store.ops.count("bump_put") == 3, "the uploads that were admitted did not all happen"
    assert 0 < queue._uploaded_last_hour(time.monotonic()) <= 1000


async def test_requests_per_batch_are_one_presign_and_one_write_each() -> None:
    store = VersionedStore(stamp_ms=1)
    queue = BumpQueue(store, autostart=False, sleep=AsyncMock())  # type: ignore[arg-type]
    for n in range(20):
        store.write(ORG, REC, _path(n), b'{"v":1,"value":"x"}', age_ms=OLD_MS)
        got = await store.get_versioned(ORG, REC, _path(n))
        assert got is not None
        queue.offer(BumpEntry(ORG, REC, _path(n), got.data, got.etag or "", got.last_modified_ms or 0, None))
    store.ops.clear()
    await queue.drain_once()
    assert store.ops.count("presign") == 1
    assert store.ops.count("bump_put") == 20
    # one marker read before, one after the writes: the cost of the guards is per batch, not per object
    assert store.ops.count("marker") == 2


async def test_a_batch_that_waited_too_long_is_dropped_whole() -> None:
    store = VersionedStore(stamp_ms=1)
    queue = BumpQueue(store, autostart=False, sleep=AsyncMock())  # type: ignore[arg-type]
    entries = [_entry(n) for n in range(3)]
    for e in entries:
        queue.offer(e)
        e.enqueued -= bump.ENTRY_MAX_AGE_S + 1
    await queue.drain_once()
    assert store.ops == []
