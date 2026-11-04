"""The drain and the flush against the capture triggers: after a write unit, whatever its changes, projecting what the triggers logged (in the writer's own transaction, or later by the
drain) leaves kg equal to a whole projection of the graph. A projection that gives up leaves the log; rows the drain cannot read are left aside; a process killed in the middle of a page
leaves the log and kg as they were."""
from __future__ import annotations

import asyncio
import os
import random
import sys

import pytest

from app.services.graph_db.postgres.kg import drainer, flush
from app.services.graph_db.postgres.kg.drainer import (
    drain_or_retry,
    drain_org,
    lag,
    purge_quarantined,
    quarantined,
)

from .kg_fresh import differences
from .pg_support import new_org
from .test_kg_derivation import ctx, held, prov
from .test_kg_projection_races import OPS, world2

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")


async def log_rows(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)
    with ctx(org):
        return await p._q(go, None)


@pytest.mark.parametrize("seed", range(60))
async def test_a_drain_after_random_writes_leaves_kg_equal_to_a_whole_projection(pg_db, seed: int) -> None:
    """The writes go through the provider and are logged by the triggers (no list of touched nodes is made up); the drain, after each write or only at the end, projects them."""
    rng = random.Random(seed)
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    ops = [rng.choice(OPS) for _ in range(rng.randint(2, 6))]
    each = seed % 2 == 0
    if each:
        await drain_org(pg_db, org)
    for op in ops:
        await op(p, org)
        if each:
            await drain_org(pg_db, org)
    await drain_org(pg_db, org)
    assert await log_rows(p, org) == 0
    assert not await differences(p, org), seed


async def test_the_inline_flush_projects_the_writers_own_changes_before_its_commit(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    assert await log_rows(p, org) == 0                       # taken in the writer's own transaction
    assert not await differences(p, org)
    for op in OPS[:12]:
        await op(p, org)
    assert await log_rows(p, org) == 0 and not await differences(p, org)


async def test_a_failed_flush_does_not_fail_the_write_and_leaves_the_log_for_the_drain(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    monkeypatch.delenv("PG_KG_STRICT")
    p, org = prov(pg_db), new_org()

    async def boom(*_a, **_k) -> None:
        raise ValueError("projection broke")
    real = flush.project
    monkeypatch.setattr(flush, "project", boom)
    await world2(p, org)                                    # the writes commit
    assert await log_rows(p, org) > 0
    monkeypatch.setattr(flush, "project", real)
    await drain_org(pg_db, org)
    assert await log_rows(p, org) == 0 and not await differences(p, org)


async def test_a_projection_that_gives_up_leaves_the_log_and_the_drain_retries_it_later(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    before = await log_rows(p, org)
    assert before > 0

    async def gives_up(*_a, **_k) -> None:
        raise RuntimeError("the projection kept reaching connectors it had not locked")
    real = drainer.project
    monkeypatch.setattr(drainer, "project", gives_up)
    with pytest.raises(RuntimeError):
        await drain_org(pg_db, org)
    assert await drain_or_retry(pg_db, org) == 0              # the safe pass counts it and does not raise
    assert await log_rows(p, org) == before                   # never acknowledged as done
    monkeypatch.setattr(drainer, "project", real)
    assert await drain_or_retry(pg_db, org) > 0
    assert await log_rows(p, org) == 0 and not await differences(p, org)


async def test_rows_the_drain_cannot_read_are_left_aside_counted_and_removed_only_by_the_operator(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)

    async def poison(tx) -> None:
        await tx.execute("/* pipeshub:test.poison */ INSERT INTO kg.graph_changes (org_id, ends, txid) VALUES ($1, ARRAY['x'], '1'::xid8), ($1, ARRAY['9:a'], '1'::xid8), "
                         "($1, ARRAY['3:' || repeat('a', 300)], '1'::xid8), ($1, ARRAY['3:r1', 'nope'], '1'::xid8)", org)
    with ctx(org):
        await p._q(poison, None, write=True)
    await drain_org(pg_db, org)
    assert await lag(pg_db, org) == 0                          # the readable rows were projected
    assert not await differences(p, org)

    async def bad(tx) -> list:
        return await quarantined(tx, org)
    with ctx(org):
        assert len(await p._q(bad, None)) == 4
    assert await purge_quarantined(pg_db, org) == 4
    assert await log_rows(p, org) == 0


async def test_a_process_killed_in_the_middle_of_a_page_leaves_the_log_and_kg_as_they_were(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    before = await log_rows(p, org)
    snapshot = await held(p, org)
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "tests.integration.graph_db.postgres.kg_child", org, env={**os.environ, "PG_KG_PROJECT": "1"},
                                                stdout=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(), 60)
    assert proc.returncode == -9, out                           # SIGKILL after the projection's writes and before the commit
    assert await log_rows(p, org) == before and await held(p, org) == snapshot
    await drain_org(pg_db, org)
    assert await log_rows(p, org) == 0 and not await differences(p, org)


async def test_the_gauges_report_the_log_and_the_unreadable_rows(pg_db) -> None:
    from app.services.graph_db.postgres.kg import legacy
    p, org = prov(pg_db), new_org()
    await world2(p, org)

    async def pub(tx) -> None:
        await legacy.publish(tx, org)
    with ctx(org):
        await p._q(pub, None)
    assert await drainer.lag(pg_db, org) > 0


async def test_an_over_long_node_id_sets_aside_only_its_own_row_and_the_other_nodes_are_projected(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": k, "orgId": org, "recordName": k[:5], "connectorId": "app1", "recordType": "FILE", "origin": "CONNECTOR"} for k in ("n1", "N" * 300, "n2")],
                                   "records")
    await drain_org(pg_db, org)

    async def bad(tx) -> list:
        return await quarantined(tx, org)
    with ctx(org):
        assert len(await p._q(bad, None)) == 1                          # the row of the 300-character key, not the row of its neighbours
    got = await held(p, org)
    assert "n1" in got["records"] and "n2" in got["records"]
    assert await lag(pg_db, org) == 0


@pytest.mark.parametrize("round_", range(4))
async def test_two_drainers_at_once_apply_every_row_once_and_lose_none(pg_db, round_: int) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    for op in OPS[:10]:
        await op(p, org)
    results = await asyncio.gather(drain_org(pg_db, org, page=3), drain_org(pg_db, org, page=5), drain_org(pg_db, org, page=3), return_exceptions=True)
    assert not [r for r in results if isinstance(r, BaseException)], results
    await drain_org(pg_db, org)
    assert await log_rows(p, org) == 0
    assert not await differences(p, org)
