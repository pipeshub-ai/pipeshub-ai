"""The coalesced re-purge window in ``REDIS_MODE=postgres``: the same protocol on ``platform.kv_entries``.

Run on a database migrated with key-value access in shadow and in enforce mode (pg-runtime.yml): the ``state:`` prefix
must be enough for it. Two ``PgState`` objects stand for two processes.
"""

import asyncio
import statistics
import time

import pytest

from app.services.paid_cache import repurge_window
from app.services.paid_cache.repurge_window import (
    APPENDED,
    CLAIMED,
    CLOSED,
    FULL,
    SEAL_DONE,
    SEAL_HANDBACK,
    SEAL_LOST_EPOCH,
    SEAL_OK,
    PgWindowStore,
)
from app.services.pg_runtime import RuntimeDb
from app.services.pg_state import PgState
from tests.support.repurge_window import MemoryWindowStore

NOW = 1_700_000_000_000


@pytest.fixture
def stores(runtime) -> tuple[PgWindowStore, PgWindowStore]:
    return PgWindowStore(PgState(runtime)), PgWindowStore(PgState(RuntimeDb()))


async def _publish(store: PgWindowStore, org: str, window: int = 5, epoch: str = "e1", token: str = "t1") -> None:
    assert (await store.open_or_append(org, window, token, epoch, "seed", NOW)).state == CLAIMED
    assert await store.mark_published(org, window, token, epoch, NOW + 330_000, NOW)


async def test_the_postgres_store_answers_like_the_memory_store_over_the_same_operations(stores, uid) -> None:
    pg, memory, org = stores[0], MemoryWindowStore(), f"org-{uid}"
    ops = [
        ("open_or_append", (org, 1, "t1", "e1", "r1", 1000)), ("open_or_append", (org, 1, "t2", "e2", "r2", 1001)),
        ("mark_published", (org, 1, "t1", "e1", 9000, 1001)), ("open_or_append", (org, 1, "t2", "e2", "r2", 1002)),
        ("seal_read", (org, 1, "e1", 9500)), ("seal_read", (org, 1, "x", 9500)),
        ("open_or_append", (org, 1, "t3", "e3", "r3", 9600)), ("mark_done", (org, 1, "e1")), ("seal_read", (org, 1, "e1", 9700)),
    ]
    for name, args in ops:
        assert await getattr(pg, name)(*args) == await getattr(memory, name)(*args), (name, args)


async def test_two_processes_agree_on_one_claimant_and_a_seal_hands_back_while_its_epoch_is_claimed(stores, uid) -> None:
    a, b = stores
    org = f"org-{uid}"
    pairs = [(i, s) for i in range(20) for s in (a, b)]
    results = await asyncio.gather(*(s.open_or_append(org, 5, f"t{n:02d}", f"e{n}", f"r{n}", NOW) for n, (i, s) in enumerate(pairs)))
    assert sum(r.state == CLAIMED for r in results) == 1
    winner = next(n for n, r in enumerate(results) if r.state == CLAIMED)
    assert (await a.seal_read(org, 5, f"e{winner}", NOW + 1000)).state == SEAL_HANDBACK
    assert (await a.seal_read(org, 5, "other", NOW + 1000)).state == SEAL_LOST_EPOCH


async def test_a_late_append_racing_the_seal_is_either_in_the_seals_list_or_told_the_window_is_closed(stores, uid) -> None:
    """The round-1 hazard: an append that lands after the seal read the list, then the seal's cleanup erased it."""
    a, b = stores
    for round_ in range(8):
        org = f"org-{uid}-{round_}"
        await _publish(a, org)
        answers: dict[str, str] = {}

        async def append(i: int, store: PgWindowStore, sink: dict[str, str] = answers, where: str = org) -> None:
            sink[f"r{i}"] = (await store.open_or_append(where, 5, "zz", "e", f"r{i}", NOW)).state

        seal = asyncio.create_task(a.seal_read(org, 5, "e1", NOW + 400_000))
        await asyncio.gather(seal, *(append(i, a if i % 2 else b) for i in range(40)))
        read = seal.result()
        assert read.state == SEAL_OK
        placed = {rid for rid, st in answers.items() if st == APPENDED}
        assert placed <= set(read.ids), sorted(placed - set(read.ids))               # every acknowledged append is covered
        assert {st for rid, st in answers.items() if rid not in placed} <= {CLOSED}
        # later appends meet the tombstone, even after the pages are done
        await a.mark_done(org, 5, "e1")
        assert (await b.open_or_append(org, 5, "zz", "e", "late", NOW)).state == CLOSED
        assert (await a.seal_read(org, 5, "e1", NOW + 500_000)).state == SEAL_DONE


async def test_the_cap_is_the_sequence_never_a_row_count_and_a_full_window_answers_full(stores, uid, monkeypatch) -> None:
    pg = stores[0]
    org = f"org-{uid}"
    monkeypatch.setattr(repurge_window, "WINDOW_CAP", 3)
    await _publish(pg, org)
    assert [(await pg.open_or_append(org, 5, "zz", "e", f"r{i}", NOW)).state for i in range(5)] == [APPENDED] * 3 + [FULL] * 2
    read = await pg.seal_read(org, 5, "e1", NOW + 400_000)
    assert read.ids == ("r0", "r1", "r2")


async def _timed_append(pg: PgWindowStore, org: str, rid: str) -> float:
    t = time.perf_counter()
    assert (await pg.open_or_append(org, 5, "zz", "e", rid, NOW)).state == APPENDED
    return (time.perf_counter() - t) * 1000


async def test_an_append_into_a_window_of_50k_rows_stays_fast_under_the_lock(stores, uid) -> None:
    """R2-3: counting rows under the window lock cost 75 ms at 50k rows (RLS enforced). Nothing counts now.

    The append into a window of 50k rows is timed against appends into an empty window, alternately in the same run, and the two
    medians are compared: a runner that is slow slows both, a cost that grows with the window slows only the first (75 ms against 2 ms). A
    few stalled appends (one measured 46 ms against a 2 ms median) move the tail but not a median of 200, so the tail only has
    to stay below a generous bound.
    """
    pg = stores[0]
    org, small = f"org-{uid}", f"org-{uid}-small"
    await _publish(pg, org)
    await _publish(pg, small)
    base = f"state:rpw:{{{org}}}:5"
    async with (await pg._state.rt.db()).runtime_uow(write=True) as tx:
        await tx.execute(
            "INSERT INTO platform.kv_entries (key, value, expires_at, updated_at) "
            "SELECT $1 || ':ids:' || lpad(g::text, 10, '0'), convert_to('r' || g, 'UTF8'), now() + interval '1 hour', now() "
            "FROM generate_series(1, 50000) g", base,
        )
        await tx.execute(
            "INSERT INTO platform.kv_entries (key, value, expires_at, updated_at) VALUES ($1, convert_to('50000', 'UTF8'), now() + interval '1 hour', now())",
            base + ":n",
        )
    for i in range(20):                                                             # warm the connection and the plans
        await _timed_append(pg, small, f"warm{i}")
        await _timed_append(pg, org, f"warm{i}")
    empty, big = [], []
    for i in range(200):                                                            # alternating, so both see the same load
        empty.append(await _timed_append(pg, small, f"late{i}"))
        big.append(await _timed_append(pg, org, f"late{i}"))
    median, baseline, p99 = statistics.median(big), statistics.median(empty), sorted(big)[int(len(big) * 0.99) - 1]
    detail = f"median {median:.1f} ms against {baseline:.1f} ms in an empty window, p99 {p99:.1f} ms"
    assert median < 3 * baseline + 5, detail                                        # the cost does not follow the window's size
    assert p99 < 250, detail                                                        # a stalled runner, not a held lock


async def test_repair_sees_only_the_orgs_open_windows_and_a_done_window_leaves_the_set(stores, uid) -> None:
    pg = stores[0]
    org, other = f"org-{uid}", f"org-{uid}-other"
    await _publish(pg, org, window=5, epoch="e5")
    await _publish(pg, org, window=6, epoch="e6")
    await _publish(pg, other, window=5, epoch="eo")
    assert sorted(await pg.overdue(org, NOW + 10**7)) == [(5, "e5"), (6, "e6")]
    assert await pg.overdue(org, NOW) == []                                          # not due yet
    await pg.mark_done(org, 5, "e5")
    assert await pg.overdue(org, NOW + 10**7) == [(6, "e6")]


async def test_repair_ignores_an_open_row_older_than_the_ids_life_even_while_its_row_has_not_expired(stores, uid) -> None:
    """The prune bound of ``overdue``: such a row could only count a loss (its ids are gone), so it is not repaired."""
    pg = stores[0]
    org = f"org-{uid}"
    await _publish(pg, org, window=5, epoch="fresh")
    async with pg._state.locked(f"rpw:{{{org}}}:open:index") as tx:
        await tx.set(f"rpw:{{{org}}}:open:4:ancient", "1000", 3600.0)           # due long before the ids' life, row still live
    assert await pg.overdue(org, NOW + 10**7) == [(5, "fresh")]


async def test_the_seal_reads_the_frozen_rows_in_pages_and_outside_the_lock(stores, uid, monkeypatch) -> None:
    """QA-G32a-4: at 250k rows the seal held the window lock for 0.94 s and added 229 MiB. The lock now covers only the
    verdict and the ``sealed`` write; the rows are read in pages after it is released."""
    a, b = stores
    org = f"org-{uid}"
    monkeypatch.setattr(repurge_window, "SEAL_READ_CHUNK", 7)
    await _publish(a, org)
    for i in range(50):
        assert (await a.open_or_append(org, 5, "zz", "e", f"r{i:02d}", NOW)).state == APPENDED
    pages: list[int] = []
    during_read: list[str] = []
    real = a._state.read_rows

    async def spy(prefix: str, after: str | None, limit: int) -> list:
        rows = await real(prefix, after, limit)
        pages.append(len(rows))
        if not during_read:                      # a late append while the rows are being read: the lock must be free
            got = await asyncio.wait_for(b.open_or_append(org, 5, "zz", "e", "late", NOW), 5)
            during_read.append(got.state)
        return rows

    monkeypatch.setattr(a._state, "read_rows", spy)
    read = await a.seal_read(org, 5, "e1", NOW + 400_000)
    assert read.state == SEAL_OK and read.ids == tuple(f"r{i:02d}" for i in range(50))
    assert pages == [7] * 7 + [1]                # ceil(50 / 7) pages, not one read of 50
    assert during_read == [CLOSED]
