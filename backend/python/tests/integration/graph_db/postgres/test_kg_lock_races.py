"""Cross-connector locks under concurrency: writers of two connectors that restamp the same records, the order in which units take their locks, and a unit that is asked for more.

Every case ends with the same check: no stamp or access list differs from a fresh compile of the graph, and a projection of the same graph in another organisation holds the same.
"""
from __future__ import annotations

import asyncio
import contextlib
import random
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.postgres.kg import flush as flush_mod
from app.services.graph_db.postgres.kg import project as project_mod
from app.services.graph_db.postgres.kg import restamp
from app.services.graph_db.postgres.kg import stamp as stamp_mod
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import NeedLocks, lock_apps, try_lock_apps

from .kg_fresh import fresh_with_stamps
from .pg_support import new_org
from .test_kg_derivation import ctx, prov
from .test_kg_fences import marks
from .test_kg_projection import add_edge, delete_edge, update_node
from .test_kg_projection_races import world2
from .test_kg_stamps import _norm, dry_diffs, held_scopes, held_stamps

if TYPE_CHECKING:
    from app.core.db import Tx

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]       # a lock that is never released fails in two minutes, not at the runner's limit
RULES = ("OPEN", "STRICT", "RESTRICTED")


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")


async def crossed(p, org: str) -> None:
    """x1 (second connector) hangs under f2 (first): its path runs through the first connector, its class is its own."""
    await world2(p, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")
    await add_edge(p, org, ("records", "x1"), ("records", "f2"), "inheritPermissions")


async def agrees_with_a_fresh_compile(pg_db, p, org: str, why: object) -> None:
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == [], (why, app)
    other, want = await fresh_with_stamps(p, org)
    assert _norm(await held_stamps(p, org), org) == _norm(want, other), why
    assert _norm(await held_scopes(p, org), org) == _norm(await held_scopes(p, other), other), why


@pytest.mark.parametrize("first_commits_last", [False, True])
@pytest.mark.parametrize("rep", range(3))
async def test_two_writers_of_two_connectors_that_restamp_one_record_each_see_the_others_commit(pg_db, monkeypatch, first_commits_last: bool, rep: int) -> None:
    """The inline flush projects in the writer's own unit, before the graph write is visible to anyone else. A barrier makes both writers finish their graph write before either
    projects; without the locks both restamp x1 from a view that lacks the other's change and the later commit wins. With them the second unit waits for the first and reads on."""
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    p, org = prov(pg_db), new_org()
    await crossed(p, org)
    await drain_org(pg_db, org)
    arrived, gate = 0, asyncio.Event()
    real_project, real_set = flush_mod.project, stamp_mod.KgWriter.stamps_set

    async def both_have_written(*a: object, **k: object) -> object:
        nonlocal arrived
        arrived += 1
        if arrived >= 2:
            gate.set()
        with contextlib.suppress(asyncio.TimeoutError):      # the unit that is not first never arrives while the first holds a lock it needs: it does not wait for it
            await asyncio.wait_for(gate.wait(), 3)
        return await real_project(*a, **k)
    first = True

    async def stamps_set_one_at_a_time(self, rows: list[dict]) -> int:
        nonlocal first
        slow, first = first and first_commits_last, False
        if slow:
            await asyncio.sleep(0.4)
        return await real_set(self, rows)
    monkeypatch.setattr(flush_mod, "project", both_have_written)
    monkeypatch.setattr(stamp_mod.KgWriter, "stamps_set", stamps_set_one_at_a_time)
    await asyncio.gather(delete_edge(p, org, ("recordGroups", "g1"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD"),      # moves x1 (path) from the first connector
                          add_edge(p, org, ("users", "u1"), ("records", "x1"), "permission", type="USER", role="READER"))      # and changes its class from the second
    monkeypatch.setattr(flush_mod, "project", real_project)
    monkeypatch.setattr(stamp_mod.KgWriter, "stamps_set", real_set)
    await drain_org(pg_db, org)
    await agrees_with_a_fresh_compile(pg_db, p, org, (first_commits_last, rep))


@pytest.mark.parametrize("seed", range(12))
async def test_two_connectors_writing_at_once_and_drained_at_once_leave_stamps_of_a_fresh_compile(pg_db, seed: int) -> None:
    rng = random.Random(seed)
    p, org = prov(pg_db), new_org()
    await crossed(p, org)
    await drain_org(pg_db, org)
    for _ in range(4):
        await asyncio.gather(update_node(p, org, "records", "f2", accessRule=rng.choice(RULES)), update_node(p, org, "records", "x1", accessRule=rng.choice(RULES)))
        await asyncio.gather(drain_org(pg_db, org, page=1), drain_org(pg_db, org, page=1))       # two drainers, one change each, both reaching the other's connector
    await drain_org(pg_db, org)
    await agrees_with_a_fresh_compile(pg_db, p, org, seed)


@pytest.mark.parametrize("seed", range(6))
async def test_drainers_and_the_restamp_worker_at_once_leave_no_mark_and_stamps_of_a_fresh_compile(pg_db, monkeypatch, seed: int) -> None:
    monkeypatch.setenv("PG_KG_INLINE_MAX", "1")                                                    # nearly every change hides what it reaches and queues a job
    rng = random.Random(100 + seed)
    p, org = prov(pg_db), new_org()
    await crossed(p, org)
    await drain_org(pg_db, org)
    for _ in range(4):
        await asyncio.gather(update_node(p, org, "records", "f2", accessRule=rng.choice(RULES)), update_node(p, org, "recordGroups", "g1", accessRule=rng.choice(RULES)))
        await asyncio.gather(drain_org(pg_db, org, page=1), drain_org(pg_db, org, page=1), restamp.run_jobs(pg_db, org, "w1", batch=2), restamp.run_jobs(pg_db, org, "w2", batch=2))
    await drain_org(pg_db, org)
    while await restamp.run_jobs(pg_db, org, "w1", batch=50):
        pass
    assert await marks(p, org) == 0
    await agrees_with_a_fresh_compile(pg_db, p, org, seed)


async def test_units_that_take_the_same_connectors_in_every_order_never_deadlock(pg_db) -> None:
    org = new_org()
    apps = ["a1", "a2", "a3", "a4"]

    async def unit(rng: random.Random) -> None:
        want = set(rng.sample(apps, rng.randint(1, 4)))

        async def go(tx: Tx) -> None:
            await lock_apps(tx, org, want, org_wide=rng.random() < 0.2)
            await asyncio.sleep(0.001)
        with ctx(org):
            await pg_db.run(go, write=True)
    async def deadlocks(tx: Tx) -> int:
        return await tx.fetchval("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")      # the unit retries a deadlock, so only the server's count shows one
    with ctx(org):
        before = await pg_db.run(deadlocks)
    for seed in range(8):
        rng = random.Random(seed)
        await asyncio.gather(*[unit(random.Random(rng.random())) for _ in range(10)])
    await asyncio.sleep(1.5)                                                                           # the server publishes its statistics about once a second
    with ctx(org):
        assert await pg_db.run(deadlocks) == before


async def test_a_record_found_gone_after_the_locks_were_checked_is_taken_again_under_the_whole_organisation(pg_db, monkeypatch) -> None:
    """Between the check of the locks and the read of the graph the record can go, and another connector can write its key before its scope is deleted."""
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    real_lock, real_derive, wide, first = project_mod.lock_apps, project_mod.derive, [], [True]

    async def spying(tx: Tx, o: str, apps: set[str], *, org_wide: bool) -> None:
        wide.append(org_wide)
        await real_lock(tx, o, apps, org_wide=org_wide)

    def goes_once(every: set, snap: object) -> object:
        d = real_derive(every, snap)
        if first.pop() if first else False:
            d.gone.add(snap.sk[("records", "x1")])
        return d

    monkeypatch.setattr(project_mod, "lock_apps", spying)
    monkeypatch.setattr(project_mod, "derive", goes_once)

    async def unit(tx: Tx) -> None:
        await project_mod.project(tx, org, {("records", "x1")}, lock_for={"app2"}, expand_neighbours=False, stamps=False)
    with ctx(org):
        await pg_db.run(unit, write=True)
    assert wide == [False, True]


async def test_a_unit_that_is_asked_for_more_locks_gives_up_the_ones_it_held(pg_db) -> None:
    org = new_org()

    async def asks(tx: Tx) -> None:
        await lock_apps(tx, org, {"a2"}, org_wide=False)
        raise NeedLocks({"a1"})

    async def takes_all(tx: Tx) -> bool:
        return await try_lock_apps(tx, org, {"a1", "a2"}, org_wide=True)
    with ctx(org):
        with pytest.raises(NeedLocks):
            await pg_db.run(asks, write=True)
        assert await pg_db.run(takes_all, write=True)                                              # nothing is left held, not even the org key
