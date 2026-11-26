"""In the cache: which reuses queue a version bump (``bump.py``), and which never do."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.paid_cache.bump import BUMP_AFTER_MS
from app.services.paid_cache.cache import PaidResultCache, _encode
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import record_in_flight
from tests.support.versioned_store import VersionedStore

ORG, REC = "org-1", "rec-1"
MINUTE = 60_000


def _key() -> CacheKey:
    return CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")


def _world(age_ms: int) -> tuple[VersionedStore, PaidResultCache]:
    now = int(time.time() * 1000)
    store = VersionedStore(stamp_ms=1, now_ms=now)
    store.write(ORG, REC, _key().relative_path(), _encode(_key(), "text"), age_ms=age_ms)
    # `_lead` is the cache's own path; the store it wraps is the versioned fake.
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    cache.bumps._autostart = False
    return store, cache


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
    monkeypatch.setenv("PAID_CACHE_WRITE", "true")
    yield
    clear_run()


async def _reuse(cache: PaidResultCache) -> None:
    with record_in_flight(ORG, REC):
        begin_run(ORG, REC, "rev1", "parse")
        paid = AsyncMock(return_value="paid again")
        assert await cache.get_or_compute(_key(), paid) == "text"
        paid.assert_not_called()


async def test_a_reuse_of_an_old_object_queues_a_bump_that_changes_its_version() -> None:
    store, cache = _world(age_ms=BUMP_AFTER_MS + 5 * MINUTE)
    before = store.files[(ORG, REC, _key().relative_path())][1]

    await _reuse(cache)
    await cache.bumps.drain_once()

    after = store.files[(ORG, REC, _key().relative_path())]
    assert after[1] != before and b"touchedMs" in after[0]


async def test_a_reuse_of_a_young_object_queues_nothing() -> None:
    store, cache = _world(age_ms=MINUTE)
    await _reuse(cache)
    assert len(cache.bumps._queue) == 0


async def test_with_writes_off_a_reuse_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_CACHE_WRITE", "off")
    store, cache = _world(age_ms=BUMP_AFTER_MS + 5 * MINUTE)
    before = store.files[(ORG, REC, _key().relative_path())][1]
    with record_in_flight(ORG, REC):
        begin_run(ORG, REC, "rev1", "parse")
        assert await cache.get_or_compute(_key(), AsyncMock()) == "text"
    assert len(cache.bumps._queue) == 0
    assert store.files[(ORG, REC, _key().relative_path())][1] == before


async def test_a_store_that_names_no_etag_is_never_bumped() -> None:
    """An older storage service sends no ETag or write time with a download: nothing to condition on."""
    from app.services.paid_cache.store import VersionedBytes

    store, cache = _world(age_ms=BUMP_AFTER_MS + 5 * MINUTE)
    original = store.get_versioned

    async def bare(o: str, r: str, p: str) -> VersionedBytes | None:
        got = await original(o, r, p)
        return None if got is None else VersionedBytes(got.data, None, None)

    store.get_versioned = bare  # type: ignore[method-assign]
    await _reuse(cache)
    assert len(cache.bumps._queue) == 0


async def test_a_store_without_bump_support_is_read_the_old_way() -> None:
    """A fake or an older client with no ``supports_bump``: the hit reads with ``get`` and queues nothing."""
    store, cache = _world(age_ms=BUMP_AFTER_MS + 5 * MINUTE)
    store.supports_bump = False
    await _reuse(cache)
    assert len(cache.bumps._queue) == 0
