"""Stored results are written only when ``PAID_CACHE_WRITE`` is on (off by default for the release that ships the cache).

A record a consumer of the previous release deletes purges nothing, so a result written for it by a new pod would
stay for ever. With writes off a new pod stores no object, no manifest and takes no lock: nothing to orphan.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache, writes_enabled
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import MemoryResultStore, record_in_flight

ORG, REC = "org-1", "rec-1"


def _key(n: int = 0) -> CacheKey:
    return CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"page{n}")


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryResultStore, InProcessLocks, PaidResultCache]:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    yield store, locks, cache
    clear_run()


def test_writes_are_off_unless_the_setting_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAID_CACHE_WRITE", raising=False)
    assert writes_enabled() is False
    for on in ("true", "1", "yes", "ON"):
        monkeypatch.setenv("PAID_CACHE_WRITE", on)
        assert writes_enabled() is True
    for off in ("false", "0", "", "off", "junk"):
        monkeypatch.setenv("PAID_CACHE_WRITE", off)
        assert writes_enabled() is False


async def test_with_writes_off_every_call_is_made_and_nothing_is_stored(
    world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN401
) -> None:
    store, locks, cache = world
    monkeypatch.delenv("PAID_CACHE_WRITE", raising=False)
    paid = AsyncMock(return_value="text")

    with record_in_flight(ORG, REC):
        scope = begin_run(ORG, REC, "rev1", "parse")
        for _ in range(2):
            assert await cache.get_or_compute(_key(), paid) == "text"
        await access.complete_run(scope, MagicMock(), sweep=True)
        await access.abandon_run(scope, MagicMock())

    assert paid.await_count == 2                      # as before the cache existed
    assert store.files == {}                          # no result, no manifest
    assert locks._held == {}                          # no lock taken


async def test_with_writes_off_a_result_that_is_already_stored_is_still_read(
    world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN401
) -> None:
    store, _locks, cache = world
    with record_in_flight(ORG, REC):
        await cache.get_or_compute(_key(), AsyncMock(return_value="stored while writes were on"))
    monkeypatch.delenv("PAID_CACHE_WRITE", raising=False)
    paid = AsyncMock(return_value="paid again")

    with record_in_flight(ORG, REC):
        assert await cache.get_or_compute(_key(), paid) == "stored while writes were on"
        begin_run(ORG, REC, "rev2", "parse", refresh=True)
        assert await cache.get_or_compute(_key(), paid) == "paid again"      # a refresh bypasses the read

    assert paid.await_count == 1


async def test_with_writes_on_results_and_manifests_are_stored_and_purged(world: Any) -> None:  # noqa: ANN401
    store, _locks, cache = world
    paid = AsyncMock(return_value="text")
    with record_in_flight(ORG, REC):
        scope = begin_run(ORG, REC, "rev1", "parse")
        assert await cache.get_or_compute(_key(), paid) == "text"
        assert await cache.get_or_compute(_key(), paid) == "text"
        await access.complete_run(scope, MagicMock())
    assert paid.await_count == 1 and store.results(ORG, REC) and "manifests/rev1/parse.json" in store.paths(ORG, REC)

    await cache.purge_record(ORG, REC)
    assert store.paths(ORG, REC) == []


async def test_a_record_deleted_by_a_consumer_of_the_previous_release_leaves_no_orphan(
    world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN401
) -> None:
    """Mixed rollout: a new pod runs the record's paid stage while the delete is handled by an old consumer, which
    purges nothing. With writes off there is nothing to leave behind."""
    store, _locks, cache = world
    monkeypatch.delenv("PAID_CACHE_WRITE", raising=False)
    with record_in_flight(ORG, REC):
        scope = begin_run(ORG, REC, "rev1", "parse")
        for n in range(5):
            await cache.get_or_compute(_key(n), AsyncMock(return_value="text"))
        await access.complete_run(scope, MagicMock(), sweep=True)

    def old_consumer_deletes_the_record() -> None:
        return None                                    # the previous release has no paid-result purge

    old_consumer_deletes_the_record()
    assert store.paths(ORG, REC) == []                 # 0 orphaned objects
