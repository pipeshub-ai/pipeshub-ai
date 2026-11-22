"""``REDIS_MODE=postgres``: leader keys, capacity leases, retry counters and counters in PostgreSQL, seen by every process.

Two ``PgState`` objects stand for two processes (each its own pool); the same cases as the Redis lease and retry contracts run here."""

import asyncio

import pytest

from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.services.pg_runtime import RuntimeDb
from app.services.pg_state import PgLeaderLock, PgLeaseManager, PgRetryTracker, PgState
from app.services.redis.connection_provider_factory import uses_pg_state


@pytest.fixture
def two(runtime) -> tuple[PgState, PgState]:
    other = RuntimeDb()
    return PgState(runtime), PgState(other)


async def test_a_leader_key_has_one_owner_across_processes_until_it_expires(
    two, uid
) -> None:
    a, b = two
    key = f"leader:{uid}"
    assert await a.lock_acquire(key, "a", 2) is True
    assert await b.lock_acquire(key, "b", 2) is False
    assert (
        await a.lock_acquire(key, "a", 2) is True
    )  # the holder's re-acquire is a renewal
    assert await b.lock_renew(key, "b", 2) is False
    await asyncio.sleep(2.3)
    assert (
        await b.lock_acquire(key, "b", 2) is True
    )  # expired: the other process takes over
    assert await a.lock_renew(key, "a", 2) is False
    await a.lock_release(key, "a")  # not the owner: no effect
    assert await a.lock_acquire(key, "a", 2) is False
    await b.lock_release(key, "b")
    assert await a.lock_acquire(key, "a", 2) is True


async def test_racing_processes_elect_exactly_one_leader(two, uid) -> None:
    a, b = two
    key = f"race:{uid}"
    won = await asyncio.gather(
        *(
            s.lock_acquire(key, f"o{i}-{n}", 30)
            for i in range(10)
            for n, s in enumerate((a, b))
        )
    )
    assert sum(won) == 1


async def test_the_existing_leader_lock_class_uses_the_database_in_postgres_mode(
    runtime, uid, monkeypatch
) -> None:
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    monkeypatch.delenv("REDIS_MODE", raising=False)
    assert uses_pg_state()
    import logging

    log = logging.getLogger("t")
    one = VectorMembershipBackfillLeaderLock(
        log, None, "p1", ttl_seconds=30, key=f"k{uid}"
    )  # type: ignore[arg-type]
    two_ = VectorMembershipBackfillLeaderLock(
        log, None, "p2", ttl_seconds=30, key=f"k{uid}"
    )  # type: ignore[arg-type]
    assert await one.try_acquire() is True and await two_.try_acquire() is False
    assert await one.refresh() is True and await two_.refresh() is False
    await one.release()
    assert await two_.try_acquire() is True
    await two_.release()


async def test_pg_leader_lock_protocol(runtime, uid) -> None:
    a, b = (
        PgLeaderLock("a", 30, f"x{uid}", PgState(runtime)),
        PgLeaderLock("b", 30, f"x{uid}", PgState(RuntimeDb())),
    )
    assert await a.try_acquire() and not await b.try_acquire() and await a.refresh()
    await a.release()
    assert await b.try_acquire()
    await b.close()


async def test_counters_count_up_keep_their_first_expiry_and_start_over_after_it(
    two, uid
) -> None:
    a, b = two
    key = f"c{uid}"
    assert [await a.incr(key, 2), await b.incr(key, 60), await a.incr(key, 60)] == [
        1,
        2,
        3,
    ]
    assert await b.get_int(key) == 3
    await asyncio.sleep(2.3)  # the expiry was fixed by the first call (2 s)
    assert await a.get_int(key) == 0
    assert await a.incr(key, 60) == 1
    assert (
        await a.incr(f"n{uid}") == 1 and await b.incr(f"n{uid}") == 2
    )  # no ttl: the ACL-version counter shape


async def test_flags_expire(two, uid) -> None:
    a, b = two
    await a.set_flag(f"f{uid}", "dropping", 1)
    assert await b.get_flag(f"f{uid}") == "dropping"
    await asyncio.sleep(1.3)
    assert await b.get_flag(f"f{uid}") is None


async def test_capacity_never_exceeds_the_limit_across_processes(two, uid) -> None:
    a, b = PgLeaseManager(two[0]), PgLeaseManager(two[1])
    pool = f"cap{uid}"
    held, peak = 0, 0
    lock = asyncio.Lock()

    async def worker(mgr: PgLeaseManager, i: int) -> None:
        nonlocal held, peak
        owner = f"w{i}"
        for _ in range(200):
            if await mgr.try_acquire(pool, owner, 3, 30):
                async with lock:
                    held += 1
                    peak = max(peak, held)
                await asyncio.sleep(0.05)
                async with lock:
                    held -= 1
                await mgr.release(pool, owner)
                return
            await asyncio.sleep(0.02)
        raise AssertionError("never got a slot")

    await asyncio.gather(*(worker(a if i % 2 else b, i) for i in range(14)))
    assert 1 < peak <= 3


async def test_lease_contract_acquire_release_renew(two, uid) -> None:
    a, b = PgLeaseManager(two[0]), PgLeaseManager(two[1])
    pool = f"p{uid}"
    assert await a.try_acquire(pool, "o1", 1, 30) is True
    assert (
        await a.try_acquire(pool, "o1", 1, 30) is True
    )  # idempotent for the same owner
    assert (
        await b.try_acquire(pool, "o2", 1, 30) is False
    )  # the one slot is taken, in another process
    assert (
        await a.renew(pool, "o1", 30) is True
        and await a.renew(pool, "nobody", 30) is False
    )
    await a.release(pool, "o1")
    assert await b.try_acquire(pool, "o2", 1, 30) is True
    assert await b.renew_many([(pool, "o2"), (pool, "gone")], 30) == {
        (pool, "o2"): True,
        (pool, "gone"): False,
    }


async def test_a_lease_of_a_dead_worker_frees_its_slot(two, uid) -> None:
    a, b = PgLeaseManager(two[0]), PgLeaseManager(two[1])
    pool = f"d{uid}"
    assert await a.try_acquire(pool, "dead", 1, 1) is True  # never renewed
    assert await b.try_acquire(pool, "live", 1, 30) is False
    await asyncio.sleep(1.3)
    assert await b.try_acquire(pool, "live", 1, 30) is True


async def test_retry_tracker_matches_the_redis_tracker_contract(runtime, uid) -> None:
    t = PgRetryTracker(PgState(runtime), ttl_seconds=60)
    mid = f"m{uid}"
    assert await t.get_count(mid) == 0 and await t.has_pending_retries([mid]) is False
    assert await t.increment_and_check(mid, 3) == (1, False)
    assert await t.increment_and_check(mid, 3) == (2, False)
    assert await t.increment_and_check(mid, 3) == (3, True)
    assert await t.has_pending_retries([mid, "other"]) is True
    assert (
        await t.record_delivery(mid) == 1 and await t.record_delivery(mid) == 2
    )  # its own counter
    assert await t.record_handback(mid) == 1 and await t.get_handbacks(mid) == 1
    await t.clear(mid)
    assert (
        await t.get_count(mid) == 0
        and await t.get_handbacks(mid) == 0
        and await t.record_delivery(mid) == 1
    )
    ids = [f"b{i}{uid}" for i in range(4)]
    for i in ids:
        await t.increment_and_check(i, 9)
        await t.record_handback(i)
    assert await t.clear_batch(ids) == 4 and await t.clear_batch([]) == 0
    assert (
        await t.has_pending_retries(ids) is False and await t.get_handbacks(ids[0]) == 0
    )


async def test_one_listen_connection_serves_every_subscriber(runtime, uid) -> None:
    from app.services.pg_runtime import ListenHub, PgListener, listen_dsn

    dsn = listen_dsn(runtime.cfg)
    assert dsn
    seen: dict[str, list[str]] = {"a": [], "b": [], "c": []}
    ls = [
        PgListener(dsn, "pipeshub_kv_changed", seen["a"].append),
        PgListener(dsn, "pipeshub_kv_changed", seen["b"].append),
        PgListener(dsn, "pipeshub_queue", seen["c"].append),
    ]
    for lst in ls:
        lst.start()
    await asyncio.sleep(0.5)
    db = await runtime.db()
    async with db.runtime_uow(write=True) as tx:
        await tx.execute(
            "/* pipeshub:test.n1 */ SELECT pg_notify('pipeshub_kv_changed', $1)",
            f"k{uid}",
        )
        await tx.execute(
            "/* pipeshub:test.n2 */ SELECT pg_notify('pipeshub_queue', $1)", f"t{uid}"
        )
    for _ in range(40):
        if f"k{uid}" in seen["a"] and f"k{uid}" in seen["b"] and f"t{uid}" in seen["c"]:
            break
        await asyncio.sleep(0.1)
    assert (
        f"k{uid}" in seen["a"]
        and f"k{uid}" in seen["b"]
        and f"t{uid}" in seen["c"]
        and f"t{uid}" not in seen["a"]
    )
    async with db.runtime_uow() as tx:
        n = await tx.fetchval(
            "/* pipeshub:test.conns */ SELECT count(*) FROM pg_stat_activity WHERE application_name = 'pipeshub-listen'"
        )
    assert n == 1  # three listeners, two channels, one connection
    assert ListenHub.for_dsn(dsn).connections == 1
    for lst in ls:
        await lst.stop()


async def test_capacity_pools_leave_no_row_behind(two, runtime, uid) -> None:
    mgr = PgLeaseManager(two[0])
    pool = f"nomark{uid}"
    assert await mgr.try_acquire(pool, "o1", 1, 30) is True
    await mgr.release(pool, "o1")
    db = await runtime.db()
    async with db.runtime_uow() as tx:
        left = await tx.fetchval(
            "/* pipeshub:test.poolrows */ SELECT count(*) FROM platform.kv_entries WHERE key LIKE $1",
            f"state:%{pool}%",
        )
    assert left == 0
