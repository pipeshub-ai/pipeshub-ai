"""A caller that shares another's in-flight work records the key, and a refresh never takes the object it replaces."""
from __future__ import annotations

import asyncio

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import MemoryResultStore, record_in_flight

ORG, REC = "org-1", "rec-1"
KEY = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "POLL_FIRST_S", 0.01)
    monkeypatch.setattr(cache_module, "POLL_MAX_S", 0.02)


async def test_a_follower_of_an_in_flight_computation_lists_the_key_in_its_own_manifest() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    scopes = {}

    async def caller(name: str) -> str:
        scopes[name] = begin_run(ORG, REC, f"rev-{name}", "parse")

        async def compute() -> str:
            await asyncio.sleep(0.05)
            return "paid once"

        return await cache.get_or_compute(KEY, compute)

    with record_in_flight(ORG, REC):
        try:
            results = await asyncio.gather(caller("leader"), caller("follower"))
        finally:
            clear_run()

    assert results == ["paid once", "paid once"]
    assert KEY.relative_path() in scopes["leader"].keys
    assert KEY.relative_path() in scopes["follower"].keys, "the follower's run used the result but does not list it"


async def test_a_refresh_caller_that_waits_for_a_holder_gets_the_replacement_not_the_old_result() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    first, second = PaidResultCache(store, locks), PaidResultCache(store, locks)

    async def seed() -> str:
        return "old"

    with record_in_flight(ORG, REC):
        assert await first.get_or_compute(KEY, seed) == "old"

        async def holder() -> str:
            begin_run(ORG, REC, "rev-a", "parse", refresh=True)

            async def compute() -> str:
                await asyncio.sleep(0.15)
                return "new"

            return await first.get_or_compute(KEY, compute)

        async def waiter() -> str:
            await asyncio.sleep(0.03)                                    # the holder has the lock by now
            begin_run(ORG, REC, "rev-b", "parse", refresh=True)

            async def compute() -> str:
                return "recomputed"

            return await second.get_or_compute(KEY, compute)

        try:
            results = await asyncio.gather(holder(), waiter())
        finally:
            clear_run()

    assert results[0] == "new"
    assert results[1] in ("new", "recomputed"), "a refresh caller was handed the result it exists to replace"
    assert results[1] != "old"


async def test_deleting_a_record_named_like_the_org_purge_route_touches_nothing() -> None:
    """``DELETE .../paid-results/org`` is the whole-org purge, so a record id ``org`` is refused before the store."""
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())

    async def paid() -> str:
        return "kept"

    with record_in_flight(ORG, REC):
        await cache.get_or_compute(KEY, paid)
    assert store.results(ORG, REC)

    await cache.purge_record(ORG, "org")
    await cache.delete_record(ORG, "org")

    assert store.results(ORG, REC), "a delete of record 'org' reached the org's other records"


async def test_a_retry_that_notes_fewer_keys_keeps_the_failed_attempts_references() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    first = RunScope(ORG, REC, "rev1", "parse")
    await first.note(store, "ocr_page/" + "a" * 64 + ".json", opens=True)
    first._unflushed = 5                                           # the attempt noted more keys, then failed
    first._keys.append("ocr_page/" + "b" * 64 + ".json")
    await first.flush(store)

    retry = RunScope(ORG, REC, "rev1", "parse")                    # the retry notes only one of them, then fails
    await retry.note(store, "ocr_page/" + "a" * 64 + ".json", opens=True)

    listed = json.loads(store.files[(ORG, REC, "manifests/rev1/parse.open.json")][0])["keys"]
    assert set(listed) == {"ocr_page/" + "a" * 64 + ".json", "ocr_page/" + "b" * 64 + ".json"}


async def test_a_refresh_run_does_not_join_a_normal_runs_in_flight_work() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())

    async def seed() -> str:
        return "old"

    with record_in_flight(ORG, REC):
        await cache.get_or_compute(KEY, seed)

        async def normal() -> str:
            begin_run(ORG, REC, "rev-n", "parse")

            async def compute() -> str:
                await asyncio.sleep(0.1)
                return "from the normal run"

            return await cache.get_or_compute(KEY, compute)

        async def refresh() -> str:
            await asyncio.sleep(0.02)
            begin_run(ORG, REC, "rev-r", "parse", refresh=True)

            async def compute() -> str:
                return "refreshed"

            return await cache.get_or_compute(KEY, compute)

        try:
            normal_result, refreshed = await asyncio.gather(normal(), refresh())
        finally:
            clear_run()

    assert normal_result == "old"                                  # a hit for the normal run
    assert refreshed == "refreshed", "the refresh run joined the normal run's flight and got its result"


async def test_the_lease_keeps_renewing_while_the_result_is_being_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """A write slower than the lease must not let another replica take the key and pay for it again."""
    monkeypatch.setattr(cache_module, "LOCK_TTL_MS", 300)
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    real_put = store.put
    stolen: list[object] = []

    async def slow_put(*a: object) -> bool:
        for _ in range(8):                                          # the write outlasts the 300 ms lease
            await asyncio.sleep(0.1)
            held = [n for n in locks._held if n.startswith("pc:lock:")]
            for name in held:                                       # another replica asks for the key
                taken = await locks.acquire(name, 300)
                stolen.extend([taken])
        return await real_put(*a)

    store.put = slow_put  # type: ignore[method-assign]

    async def compute() -> str:
        return "paid"

    with record_in_flight(ORG, REC):
        assert await cache.get_or_compute(KEY, compute) == "paid"

    assert stolen, "the write never ran"
    assert [t for t in stolen if t is not None] == [], "the lease lapsed during the write and another payer took it"
    assert len(store.results(ORG, REC)) == 1


def test_two_event_loop_threads_never_both_take_the_same_lock() -> None:
    """The fallback locks are shared by loops on several threads: one winner per name."""
    import sys
    import threading

    locks = InProcessLocks()
    names = [f"pc:lock:{{o:r}}:{i}" for i in range(4000)]
    wins: list[list[str]] = [[], []]
    barrier = threading.Barrier(2)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def run(slot: int) -> None:
        async def go() -> None:
            barrier.wait()
            for name in names:
                if await locks.acquire(name, 60_000) is not None:
                    wins[slot].append(name)

        asyncio.run(go())

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    assert len(wins[0]) + len(wins[1]) == len(names)
    assert not set(wins[0]) & set(wins[1]), "a lock was taken by both threads"


async def test_a_failed_read_of_the_earlier_manifest_does_not_replace_it() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    first = RunScope(ORG, REC, "rev1", "parse")
    await first.note(store, "ocr_page/" + "a" * 64 + ".json", opens=True)
    first._keys.append("ocr_page/" + "b" * 64 + ".json")
    first._unflushed = 5
    await first.flush(store)
    before = store.files[(ORG, REC, "manifests/rev1/parse.open.json")][0]

    async def failing_get(*_a: object) -> bytes:
        raise ConnectionError("storage unreachable")

    store.get = failing_get  # type: ignore[method-assign]
    retry = RunScope(ORG, REC, "rev1", "parse")
    with pytest.raises(ConnectionError):
        await retry.note(store, "ocr_page/" + "a" * 64 + ".json", opens=True)

    assert store.files[(ORG, REC, "manifests/rev1/parse.open.json")][0] == before
    assert len(json.loads(before)["keys"]) == 2
