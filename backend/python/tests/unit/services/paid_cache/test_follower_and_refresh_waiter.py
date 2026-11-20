"""A caller that shares another's in-flight work records the key, and a refresh never takes the object it replaces."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache import locks as locks_module
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


async def test_a_run_past_its_own_stage_bound_stores_nothing_even_when_the_process_bound_is_larger() -> None:
    """A conversion thread that outlives its 504 keeps calling the cache: the bound is the run's (the client's budget)."""
    import time

    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    scope = begin_run(ORG, REC, "rev1", "parse", stage_deadline_s=30.0)
    scope.started_ms -= 31_000                                     # the stage began 31 s ago; the process bound is 2400+
    assert time.time() > 0

    async def paid() -> str:
        return "late"

    try:
        with record_in_flight(ORG, REC):
            assert await cache.get_or_compute(KEY, paid) == "late"
    finally:
        clear_run()
    assert store.paths(ORG, REC) == []


async def test_two_runs_of_one_revision_finish_without_unlisting_each_others_keys() -> None:
    """An orphaned parse and its retry share the manifest path: the later finisher keeps the earlier one's keys."""
    import json

    from app.services.paid_cache.run import RunScope

    def keys(*names: str) -> list[str]:
        return [f"ocr_page/{n * 64}.json" for n in names]

    for order in ((0, 1), (1, 0)):
        store = MemoryResultStore()
        orphan, retry = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
        for run, names in ((orphan, "ab"), (retry, "bc")):
            for key in keys(*names):
                await run.note(store, key, opens=True)
        for index in order:
            await (orphan, retry)[index].finish(store)
        final = json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])["keys"]
        assert set(final) == set(keys("a", "b", "c")), order


async def test_an_orphans_later_flush_keeps_the_keys_its_retry_listed() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    orphan, retry = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
    await orphan.note(store, "ocr_page/" + "a" * 64 + ".json", opens=True)
    await retry.note(store, "ocr_page/" + "b" * 64 + ".json", opens=True)
    orphan._keys.append("ocr_page/" + "c" * 64 + ".json")          # the orphan pays one more page
    orphan._unflushed += 1
    await orphan.flush(store)
    listed = json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]
    assert {k[10] for k in listed} == {"a", "b", "c"}


async def test_a_key_only_the_open_manifest_lists_survives_the_other_runs_finish() -> None:
    """The orphan flushes a key after the retry's last note; the orphan is then past its deadline and cannot write it back."""
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    orphan, retry = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
    a, b, c = (f"ocr_page/{n * 64}.json" for n in "abc")
    await retry.note(store, b, opens=True)
    await orphan.note(store, a, opens=True)
    orphan._keys.append(c)                                          # paid after the retry's last note
    orphan._unflushed += 1
    await orphan.flush(store)
    orphan.started_ms -= int((locks_module.stage_deadline_s() + 10) * 1000)     # past its deadline: no more writes

    await retry.finish(store)

    final = json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])["keys"]
    assert set(final) == {a, b, c}
    assert (ORG, REC, "manifests/r1/parse.open.json") not in store.files


async def test_a_flush_that_lands_between_another_runs_read_and_write_is_not_dropped() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    real_replace = store.replace

    async def slow_replace(*args: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.05)                                   # the window between a run's read and its write
        await real_replace(*args)

    store.replace = slow_replace  # type: ignore[method-assign]
    orphan, retry = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
    a, b = (f"ocr_page/{n * 64}.json" for n in "ab")
    await orphan.note(store, a, opens=True)                        # the orphan's A is already in the open manifest
    c = f"ocr_page/{'c' * 64}.json"
    orphan._keys.append(c)                                         # a new, unflushed key: both runs now have a flush to make
    orphan._seen.add(c)
    orphan._unflushed = 1
    retry._keys.append(b)
    retry._seen.add(b)
    retry._unflushed = 1
    retry._opened = True

    await asyncio.gather(retry.flush(store), orphan.flush(store))   # two writers of one open manifest at once
    assert set(json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]) == {a, b, c}   # before finish() merges again
    await retry.finish(store)

    final = json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])["keys"]
    assert set(final) == {a, b, c}


async def test_two_replicas_writing_one_manifest_through_the_shared_lock_keep_each_others_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry is another request, served by another replica: only the shared lock backend serializes them (the
    in-process lock is bypassed here, as two processes would not share it)."""
    import json

    from app.services.paid_cache import run as run_module
    from app.services.paid_cache.run import RunScope

    monkeypatch.setattr(run_module, "_path_lock", lambda *_a: asyncio.Lock())     # a lock per call: no in-process help
    store, shared_locks = MemoryResultStore(), InProcessLocks()                    # one store, one lock backend (Redis)
    replica_a, replica_b = PaidResultCache(store, shared_locks), PaidResultCache(store, shared_locks)
    real_replace = store.replace

    async def slow_replace(*args: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.05)                                                  # the width of a read-and-write
        await real_replace(*args)

    store.replace = slow_replace  # type: ignore[method-assign]
    a, b = (f"ocr_page/{n * 64}.json" for n in "ab")
    first, second = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
    first.manifest_guard, second.manifest_guard = replica_a.manifest_lock, replica_b.manifest_lock
    first._keys, first._seen, first._unflushed = [a], {a}, 1
    second._keys, second._seen, second._unflushed = [b], {b}, 1

    await asyncio.gather(first._write_open(store), second._write_open(store))
    await first.finish(store)

    final = json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])["keys"]
    assert set(final) == {a, b}


async def test_without_the_shared_lock_two_replicas_do_lose_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the same interleaving with no guard drops a key, so the test above is not vacuous."""
    import json

    from app.services.paid_cache import run as run_module
    from app.services.paid_cache.run import RunScope

    monkeypatch.setattr(run_module, "_path_lock", lambda *_a: asyncio.Lock())
    store = MemoryResultStore()
    real_replace = store.replace

    async def slow_replace(*args: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.05)
        await real_replace(*args)

    store.replace = slow_replace  # type: ignore[method-assign]
    a, b = (f"ocr_page/{n * 64}.json" for n in "ab")
    first, second = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
    first._keys, first._seen, first._unflushed = [a], {a}, 1
    second._keys, second._seen, second._unflushed = [b], {b}, 1
    await asyncio.gather(first._write_open(store), second._write_open(store))
    listed = json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]
    assert len(listed) == 1


async def test_the_manifest_lease_is_renewed_through_a_section_longer_than_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read-merge-replace slower than the lease keeps the lock: the other replica waits instead of writing beside it."""
    import json

    from app.services.paid_cache import run as run_module
    from app.services.paid_cache.run import RunScope

    monkeypatch.setattr(cache_module, "LOCK_TTL_MS", 300)
    monkeypatch.setattr(run_module, "_path_lock", lambda *_a: asyncio.Lock())
    store, shared_locks = MemoryResultStore(), InProcessLocks()
    replica_a, replica_b = PaidResultCache(store, shared_locks), PaidResultCache(store, shared_locks)
    real_replace = store.replace

    async def slow_replace(*args: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.8)                                   # more than twice the lease
        await real_replace(*args)

    store.replace = slow_replace  # type: ignore[method-assign]
    a, b = (f"ocr_page/{n * 64}.json" for n in "ab")
    first, second = RunScope(ORG, REC, "r1", "parse"), RunScope(ORG, REC, "r1", "parse")
    first.manifest_guard, second.manifest_guard = replica_a.manifest_lock, replica_b.manifest_lock
    first._keys, first._seen, first._unflushed = [a], {a}, 1
    second._keys, second._seen, second._unflushed = [b], {b}, 1

    await asyncio.gather(first._write_open(store), second._write_open(store))

    listed = json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]
    assert set(listed) == {a, b}
    assert shared_locks._held == {}                                # both released


async def test_a_writer_that_never_gets_the_manifest_lock_does_not_write_beside_the_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.paid_cache.locks import manifest_lock_name
    from app.services.paid_cache.run import RunScope

    monkeypatch.setattr(locks_module, "WRITE_DEADLINE_S", 0.2)
    monkeypatch.setattr(cache_module, "MANIFEST_LOCK_POLL_S", 0.01)
    store, shared_locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, shared_locks)
    path = "manifests/r1/parse.open.json"
    holder = await shared_locks.acquire(manifest_lock_name(ORG, REC, path), 60_000)      # another replica's live lock
    assert holder is not None
    calls: list[str] = []
    real_replace, real_delete = store.replace, store.delete_files

    async def replace(*args: Any) -> None:  # noqa: ANN401
        calls.append("replace")
        await real_replace(*args)

    async def delete_files(*args: Any) -> None:  # noqa: ANN401
        calls.append("delete")
        await real_delete(*args)

    store.replace, store.delete_files = replace, delete_files  # type: ignore[method-assign]
    run = RunScope(ORG, REC, "r1", "parse")
    run.manifest_guard = cache.manifest_lock
    run._keys, run._seen, run._unflushed = ["ocr_page/" + "a" * 64 + ".json"], {"ocr_page/" + "a" * 64 + ".json"}, 1

    with pytest.raises(TimeoutError):
        await run._write_open(store)
    with pytest.raises(TimeoutError):
        await run.finish(store)

    assert calls == []                                             # neither replace nor delete ran
    assert await shared_locks.owns(manifest_lock_name(ORG, REC, path), holder)           # the holder's lock is untouched


async def test_concurrent_claims_of_one_refresh_token_grant_exactly_one() -> None:
    shared = InProcessLocks()
    replicas = [PaidResultCache(MemoryResultStore(), shared) for _ in range(2)]
    for n in range(20):
        results = await asyncio.gather(*(replicas[i % 2].claim_refresh(ORG, REC, f"tok-{n}") for i in range(4)))
        assert "unclaimed" not in results                            # a real claim answered every time
        winners = [r for r in results if r is not None]
        assert len(winners) == 1 and results.count(None) == 3        # one lease token and three refusals, for each token


async def test_the_refresh_is_marked_purged_only_after_the_purge_returned() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    store.files[(ORG, REC, "ocr_page/" + "a" * 64 + ".json")] = (b"{}", 1)
    async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
        assert must_purge is True
        assert not await locks.is_marked(cache_module.refresh_name(ORG, REC, "evt"))     # not before the purge ran
        await cache.purge_for_refresh(ORG, REC)
        assert store.paths(ORG, REC) == []
        assert not await locks.is_marked(cache_module.refresh_name(ORG, REC, "evt"))     # nor before the block ends
    assert await locks.is_marked(cache_module.refresh_name(ORG, REC, "evt"))             # written once the purge returned
    async with cache.refresh_purge(ORG, REC, "evt") as redelivery:     # so a redelivery reuses
        assert redelivery is False


async def test_a_holder_whose_lease_lapsed_before_its_cutoff_sends_no_delete() -> None:
    """The lease is checked again after the cutoff and before the DELETE: a holder that lost it removes nothing."""
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    store.files[(ORG, REC, "ocr_page/" + "a" * 64 + ".json")] = (b"{}", 1)
    real_cutoff = store.purge_cutoff

    async def slow_cutoff(org: str, rec: str) -> int:
        cutoff = await real_cutoff(org, rec)
        locks.expire_all()                                   # the lease lapses while the cutoff request is out
        return cutoff

    store.purge_cutoff = slow_cutoff  # type: ignore[method-assign]

    with pytest.raises(cache_module.RefreshPurgeInProgress):
        async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
            assert must_purge is True
            await cache.purge_for_refresh(ORG, REC)

    assert "purge" not in store.ops                          # no DELETE was sent
    assert store.paths(ORG, REC) != []


async def test_the_marker_is_written_only_while_the_lease_is_still_held_in_one_atomic_step() -> None:
    """A lease that lapses between the end of the purge and the marker leaves no marker."""
    locks = InProcessLocks()
    cache = PaidResultCache(MemoryResultStore(), locks)

    with pytest.raises(cache_module.RefreshPurgeInProgress):
        async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
            assert must_purge is True
            locks.expire_all()                               # lapses after the purge, before the marker
    async with cache.refresh_purge(ORG, REC, "evt") as again:
        assert again is True                                 # no marker: the redelivery purges


async def test_a_purge_that_fails_leaves_no_marker_so_the_redelivery_purges() -> None:
    cache = PaidResultCache(MemoryResultStore(), InProcessLocks())
    with pytest.raises(ConnectionError):
        async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
            assert must_purge is True
            raise ConnectionError("storage unreachable")                # the purge failed
    async with cache.refresh_purge(ORG, REC, "evt") as again:
        assert again is True


async def test_a_delivery_that_overlaps_a_purge_waits_for_its_marker_and_never_reuses_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.01)
    locks = InProcessLocks()
    cache = PaidResultCache(MemoryResultStore(), locks)
    order: list[str] = []

    holding, contended = asyncio.Event(), asyncio.Event()
    lease = cache_module.refresh_name(ORG, REC, "evt") + ":lease"
    real_acquire = locks.acquire

    async def acquire(name: str, ttl_ms: int) -> str | None:
        token = await real_acquire(name, ttl_ms)
        if name == lease and token is None:
            contended.set()                                             # the duplicate found the lease held
        return token

    locks.acquire = acquire  # type: ignore[method-assign]

    async def holder() -> None:
        async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
            assert must_purge is True
            holding.set()
            await contended.wait()                                      # the purge runs until the duplicate is waiting
            order.append("purged")

    async def overlapping() -> None:
        await holding.wait()
        async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
            order.append(f"reuse={not must_purge}")
            assert must_purge is False

    await asyncio.wait_for(asyncio.gather(holder(), overlapping()), timeout=10)

    assert order == ["purged", "reuse=True"]                            # reuse only after the purge returned


async def test_a_redelivery_while_a_dead_holders_lease_is_live_is_handed_back_then_purges_once_it_lapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holder died before purging: no marker, a live lease. The redelivery must not reuse the pre-refresh objects."""
    from app.services.paid_cache.cache import RefreshPurgeInProgress

    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.1)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.01)
    locks = InProcessLocks()
    cache = PaidResultCache(MemoryResultStore(), locks)
    # A dead holder: a lease token with no heartbeat (nothing renews it) and no marker.
    assert await locks.acquire(cache_module.refresh_name(ORG, REC, "evt") + ":lease", 60_000) is not None

    with pytest.raises(RefreshPurgeInProgress):                         # handed back for a later delivery, no reuse
        async with cache.refresh_purge(ORG, REC, "evt"):
            raise AssertionError("a delivery that did not see the marker ran")

    locks.expire_all()                                                  # the dead holder's lease lapses
    async with cache.refresh_purge(ORG, REC, "evt") as redelivery:
        assert redelivery is True                                       # no marker: this delivery purges


async def test_the_refresh_token_of_a_redelivered_event_reaches_the_parse_request() -> None:
    """The index run's token is the event id, inherited by the run the parse starts from, so the parsing service sees
    the same token on a redelivery and reuses the pages its earlier attempt paid for."""
    from app.services.paid_cache.access import start_run

    with record_in_flight(ORG, REC):
        begin_run(ORG, REC, None, "index", refresh=True, refresh_token="evt-77")
        run = start_run("rev1", "index")
        try:
            assert run is not None and run.refresh and run.refresh_token == "evt-77"
        finally:
            clear_run()


async def test_a_retry_of_a_refresh_does_not_reuse_a_stale_object_written_after_the_purge() -> None:
    """The first attempt failed before any page; an object of the same key landed after the purge (an orphan of an
    older run). The retry is still a refresh: that object is not in its attempt's manifest, so it is bypassed."""
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    with record_in_flight(ORG, REC):
        begin_run(ORG, REC, "rev1", "index", refresh=True)
        clear_run()
        stale_writer = PaidResultCache(store, InProcessLocks())
        await stale_writer.get_or_compute(KEY, AsyncMock(return_value="STALE"))          # lands after the purge
        begin_run(ORG, REC, "rev1", "index", refresh=True, reuse_prior_attempt=True)
        paid = AsyncMock(return_value="fresh")
        assert await cache.get_or_compute(KEY, paid) == "fresh"
        clear_run()
    assert paid.await_count == 1                                     # the refresh was not silently dropped


async def test_a_second_completed_run_does_not_keep_the_first_runs_objects_of_another_model() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    first = RunScope(ORG, REC, "r1", "parse", clock=lambda: 1_000.0)
    await first.note(store, "ocr_page/" + "a" * 64 + ".json", opens=True)
    await first.finish(store)
    second = RunScope(ORG, REC, "r1", "parse", clock=lambda: 2_000.0)                  # started after the first completed
    await second.note(store, "ocr_page/" + "b" * 64 + ".json", opens=True)
    await second.finish(store)
    final = json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])["keys"]
    assert final == ["ocr_page/" + "b" * 64 + ".json"]               # the replaced model's object is no longer listed


async def test_concurrent_flushes_of_one_manifest_in_one_process_keep_every_key_only_because_of_the_path_lock() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    real_replace = store.replace

    async def slow_replace(*args: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.05)
        await real_replace(*args)

    store.replace = slow_replace  # type: ignore[method-assign]
    runs = [RunScope(ORG, REC, "r1", "parse") for _ in range(4)]
    keys = [f"ocr_page/{n * 64}.json" for n in "abcd"]
    for run, key in zip(runs, keys):
        run._keys, run._seen, run._unflushed = [key], {key}, 1
    await asyncio.gather(*(run._write_open(store) for run in runs))
    assert set(json.loads(store.files[(ORG, REC, "manifests/r1/parse.open.json")][0])["keys"]) == set(keys)


async def test_concurrent_finishes_of_one_manifest_in_one_process_keep_every_key() -> None:
    import json

    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    real_replace = store.replace

    async def slow_replace(*args: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.05)
        await real_replace(*args)

    store.replace = slow_replace  # type: ignore[method-assign]
    runs = [RunScope(ORG, REC, "r1", "parse") for _ in range(3)]
    keys = [f"ocr_page/{n * 64}.json" for n in "abc"]
    for run, key in zip(runs, keys):
        run._keys, run._seen = [key], {key}
    await asyncio.gather(*(run.finish(store) for run in runs))
    assert set(json.loads(store.files[(ORG, REC, "manifests/r1/parse.json")][0])["keys"]) == set(keys)


async def test_the_cache_and_the_run_helpers_wire_the_manifest_guard_into_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from app.services.paid_cache import access
    from app.services.paid_cache.run import RunScope

    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)

    with record_in_flight(ORG, REC):
        noted = begin_run(ORG, REC, "r1", "parse")
        await cache.get_or_compute(KEY, AsyncMock(return_value="x"))
        assert noted.manifest_guard == cache.manifest_lock           # the cache's own note wires it
        clear_run()
    completed, abandoned = RunScope(ORG, REC, "r2", "parse"), RunScope(ORG, REC, "r3", "parse")
    for run in (completed, abandoned):
        run._keys, run._seen, run._unflushed = ["ocr_page/" + "e" * 64 + ".json"], {"ocr_page/" + "e" * 64 + ".json"}, 1
        assert run.manifest_guard is None
    await access.complete_run(completed, MagicMock())
    abandoned._opened = True
    await access.abandon_run(abandoned, MagicMock())
    assert completed.manifest_guard == cache.manifest_lock and abandoned.manifest_guard == cache.manifest_lock
