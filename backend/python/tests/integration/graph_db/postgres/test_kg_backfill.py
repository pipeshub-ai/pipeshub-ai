"""Backfill, verify, promotion and the way back to the walk.

A backfill copies a connector (and the org's principals) into kg in batches that can be killed and resumed, drains the change log, verifies under the connector's lock and only then
flips it to ``shadow``; a promotion verifies again. The verify is tested by corrupting kg through the writers and expecting each kind of difference to be named. Turning the projection
off sends the connectors back to the walk, because the take-back deletes the only evidence that kg is behind.
"""
from __future__ import annotations

import pytest

from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import drainer, flush, modes
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from app.services.graph_db.postgres.kg.reader import load_sks
from app.services.graph_db.postgres.kg.verify import (
    Result,
    verify_app,
    verify_principals,
)
from app.services.graph_db.postgres.kg.writers import RECORD_COLS, KgWriter

from .pg_support import new_org
from .test_kg_derivation import ctx, prov
from .test_kg_projection import add_edge, delete_edge
from .test_kg_projection_races import OPS, world2

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")


async def run_in(db, org: str, fn, *, write: bool = False) -> object:
    with ctx(org):
        return await db.run(fn, write=write)


async def state(db, org: str, app_id: str | None) -> tuple[str | None, str, int | None]:
    async def go(tx) -> tuple:
        sk = 0 if app_id is None else (await load_sks(tx, org, {("apps", app_id)})).get(("apps", app_id))
        if sk is None:
            return (None, "legacy", None)
        cur, mode = await modes.state(tx, org, sk)
        row = await tx.fetchrow("SELECT code_epoch FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2", org, sk)
        return cur, mode, row["code_epoch"] if row else None
    return await run_in(db, org, go)


async def verify(db, org: str, app_id: str) -> Result:
    async def go(tx) -> Result:
        return await verify_app(tx, org, app_id) + await verify_principals(tx, org)
    return await run_in(db, org, go)


async def fresh_world(pg_db) -> tuple:
    """A graph that existed before the projection was on: its log is empty, so only the backfill can bring it into kg."""
    p, org = prov(pg_db), new_org()
    await world2(p, org)

    async def clear(tx) -> None:
        ids = [r["id"] for r in await tx.fetch("SELECT id FROM kg.graph_changes WHERE org_id = $1", org)]
        await KgWriter(tx).changes_delete(ids)
    await run_in(pg_db, org, clear, write=True)
    return p, org


async def test_a_backfill_copies_a_connector_and_flips_it_to_shadow_with_its_digest(pg_db) -> None:
    p, org = await fresh_world(pg_db)
    out = await bf.backfill(pg_db, org, "app1", page=3)
    assert out.flipped and out.pending == 0 and out.result.ok and out.batches > 2
    cursor, mode, epoch = await state(pg_db, org, "app1")
    assert (cursor, mode, epoch) == ("done", "shadow", CODE_EPOCH)
    assert (await state(pg_db, org, None))[0] == "done"                    # the principals were copied once, for the org
    res = await verify(pg_db, org, "app1")
    assert res.ok and res.digest == out.result.digest and res.expected > 0
    again = await bf.backfill(pg_db, org, "app1", page=3)                   # a second call finds nothing to copy and verifies again
    assert again.flipped and again.batches == 0 and again.result.digest == out.result.digest


async def test_a_backfill_that_is_cut_resumes_at_its_last_committed_batch(pg_db, monkeypatch) -> None:
    p, org = await fresh_world(pg_db)
    part = await bf.backfill(pg_db, org, "app1", page=2, max_batches=1)
    assert not part.flipped and part.result.missing                         # only some nodes are in kg: the flip is refused, nothing is promised
    assert (await state(pg_db, org, "app1"))[1] == "legacy"
    real, calls = bf.project, {"n": 0}

    async def dies(*a: object, **k: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("the process was killed")
        return await real(*a, **k)
    monkeypatch.setattr(bf, "project", dies)
    with pytest.raises(ConnectionError):
        await bf.backfill(pg_db, org, "app1", page=2)
    cursor_after_kill = (await state(pg_db, org, "app1"))[0]
    assert cursor_after_kill not in (None, "done")                          # the batches before the kill stayed
    monkeypatch.setattr(bf, "project", real)
    done = await bf.backfill(pg_db, org, "app1", page=2)
    assert done.flipped and (await state(pg_db, org, "app1"))[1] == "shadow"


async def test_writes_during_a_backfill_are_drained_before_the_verify(pg_db) -> None:
    p, org = await fresh_world(pg_db)
    for i, op in enumerate(OPS[5:14]):
        await bf.backfill_scope(pg_db, org, "app1", page=2, max_batches=1)   # a batch, then somebody writes
        await op(p, org)
        assert i >= 0
    out = await bf.backfill(pg_db, org, "app1", page=2)
    assert out.flipped, out.result.summary() if out.result else out
    assert (await verify(pg_db, org, "app1")).ok


def row(sk: int, rid: str, **kw: object) -> dict:
    """A whole kg.records row (the writer takes every column)."""
    base = dict.fromkeys(RECORD_COLS) | {"sk": sk, "coll": 3, "id": rid, "kind": 3, "deleted": False, "placeholder": False, "granted": False}
    return base | kw


async def corrupt(db, org: str, fn) -> None:
    async def go(tx) -> None:
        await fn(tx, KgWriter(tx))
    await run_in(db, org, go, write=True)


async def sk_of(db, org: str, key: tuple[str, str]) -> int:
    async def go(tx) -> int:
        return (await load_sks(tx, org, {key}))[key]
    return await run_in(db, org, go)


async def shadowed(pg_db) -> tuple:
    p, org = await fresh_world(pg_db)
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped
    return p, org


async def test_the_verify_names_a_record_kg_lost(pg_db) -> None:
    p, org = await shadowed(pg_db)
    sk = await sk_of(pg_db, org, ("records", "f2"))
    await corrupt(pg_db, org, lambda tx, w: w.records_delete([sk]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"r|{sk}") for m in res.missing)


async def test_the_verify_names_a_row_no_node_explains(pg_db) -> None:
    p, org = await shadowed(pg_db)
    app = await sk_of(pg_db, org, ("apps", "app1"))
    ghost = (await KgWriterNodes.make(pg_db, org, ("records", "ghost")))
    await corrupt(pg_db, org, lambda tx, w: w.records_upsert([row(ghost, "ghost", app_sk=app, name="ghost")]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"r|{ghost}") for m in res.extra)


class KgWriterNodes:
    @staticmethod
    async def make(db, org: str, key: tuple[str, str]) -> int:
        async def go(tx) -> int:
            return (await KgWriter(tx).nodes_upsert({key}))[key]
        return await run_in(db, org, go, write=True)


async def test_the_verify_names_a_row_that_differs(pg_db) -> None:
    p, org = await shadowed(pg_db)
    sk = await sk_of(pg_db, org, ("records", "f2"))
    app = await sk_of(pg_db, org, ("apps", "app1"))
    await corrupt(pg_db, org, lambda tx, w: w.records_upsert([row(sk, "f2", app_sk=app, name="tampered", name_key="tampered")]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"r|{sk}") for m in res.changed)


async def test_the_verify_names_a_record_whose_sk_is_not_the_one_node_keys_holds(pg_db) -> None:
    """``w_records_upsert`` takes any sk: nothing else ties a record row to node_keys."""
    p, org = await shadowed(pg_db)
    app = await sk_of(pg_db, org, ("apps", "app1"))
    old = await sk_of(pg_db, org, ("records", "f2"))
    other = await KgWriterNodes.make(pg_db, org, ("records", "unrelated-key"))

    async def swap(tx, w) -> None:
        await w.records_delete([old])
        await w.records_upsert([row(other, "f2", app_sk=app, name="f2 under another sk")])
    await corrupt(pg_db, org, swap)
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith((f"s|{other} records:f2", f"s|{other} 3:f2")) for m in res.changed), res.changed


async def test_the_verify_names_a_node_kg_has_no_key_for_and_a_connector_marked_cross_app_too_little_and_lets_a_sticky_mark_be(pg_db) -> None:
    """The mark is a superset: kg may keep a mark the graph no longer needs, never miss one."""
    p, org = await fresh_world(pg_db)
    assert (await bf.backfill(pg_db, org, "app2", page=4)).flipped
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # f2 came after the copy: no key
    res = await verify(pg_db, org, "app2")
    assert not res.ok and any(m.startswith("k|records:f2") for m in res.missing)
    await drain_org(pg_db, org)
    assert (await verify(pg_db, org, "app2")).ok
    await delete_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")
    await drain_org(pg_db, org)
    assert (await verify(pg_db, org, "app2")).ok                            # the mark stays and is not an error
    p, org = await fresh_world(pg_db)                                       # the connectors share a node already keyed: only the mark is missing
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped and (await bf.backfill(pg_db, org, "app2", page=4)).flipped
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")
    res = await verify(pg_db, org, "app2")
    assert not res.ok and any(m.startswith("x|") for m in res.missing) and any(m.startswith("r|") for m in res.changed)


async def test_a_flip_is_refused_with_a_change_waiting_and_a_connector_that_differs_goes_back_to_the_walk(pg_db) -> None:
    p, org = await shadowed(pg_db)
    await add_edge(p, org, ("recordGroups", "g2"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD")       # waits in the log
    out = await bf.backfill(pg_db, org, "app1", page=4, drain_rounds=0)
    assert not out.flipped and out.pending > 0
    assert (await state(pg_db, org, "app1"))[1] == "shadow"                 # the state is not touched by a refusal that is only about timing
    await drain_org(pg_db, org)
    sk = await sk_of(pg_db, org, ("records", "f2"))
    await corrupt(pg_db, org, lambda tx, w: w.records_delete([sk]))
    out = await bf.backfill(pg_db, org, "app1", page=4)
    assert not out.flipped and out.result.missing
    cursor, mode, _ = await state(pg_db, org, "app1")
    assert (cursor, mode) == (None, "legacy")                               # kg that readers might use differs: back to the walk, and the next backfill starts over
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped


async def test_promotion_needs_shadow_a_clean_verify_and_an_empty_log_and_demotion_is_always_allowed(pg_db) -> None:
    p, org = await fresh_world(pg_db)
    with pytest.raises(bf.Refused, match="not in shadow"):
        await bf.promote(pg_db, org, "app1")
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped
    await add_edge(p, org, ("recordGroups", "g2"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD")
    with pytest.raises(bf.Refused, match="waiting in the log"):
        await bf.promote(pg_db, org, "app1")
    await drain_org(pg_db, org)
    res = await bf.promote(pg_db, org, "app1")
    assert res.ok and (await state(pg_db, org, "app1"))[1] == "enforce"
    again = await bf.backfill(pg_db, org, "app1", page=4)                    # a second backfill of an enforced connector keeps it enforced
    assert again.flipped and (await state(pg_db, org, "app1"))[1] == "enforce"
    await bf.demote(pg_db, org, "app1")
    assert (await state(pg_db, org, "app1"))[:2] == (None, "legacy")


async def test_a_backfill_and_a_promotion_refuse_to_run_with_the_projection_off(pg_db, monkeypatch) -> None:
    p, org = await fresh_world(pg_db)
    monkeypatch.setenv("PG_KG_PROJECT", "0")
    with pytest.raises(bf.ProjectionOff):
        await bf.backfill(pg_db, org, "app1")
    with pytest.raises(bf.ProjectionOff):
        await bf.promote(pg_db, org, "app1")


async def test_turning_the_projection_off_sends_the_connectors_back_to_the_walk_and_a_new_backfill_starts_over(pg_db, monkeypatch) -> None:
    p, org = await shadowed(pg_db)
    await bf.promote(pg_db, org, "app1")
    monkeypatch.setenv("PG_KG_PROJECT", "0")
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "off1", "orgId": org, "recordName": "While off", "connectorId": "app1", "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    cursor, mode, _ = await state(pg_db, org, "app1")
    assert (cursor, mode) == (None, "legacy")                               # the unit took its own log rows back and demoted
    assert (await state(pg_db, org, None))[:2] == (None, "legacy")
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    out = await bf.backfill(pg_db, org, "app1", page=4)
    assert out.flipped and out.batches > 0                                  # from the first node, and it covers the write made while off
    async def has_it(tx) -> bool:
        return bool(await tx.fetchval("SELECT 1 FROM kg.node_keys WHERE org_id = $1 AND id = 'off1'", org))
    assert await run_in(pg_db, org, has_it)


async def test_the_sweep_of_other_roles_rows_also_sends_the_connectors_back(pg_db, monkeypatch) -> None:
    p, org = await shadowed(pg_db)
    monkeypatch.setenv("PG_KG_PROJECT", "0")
    monkeypatch.setattr(flush, "SWEEP_GAP_S", 0.0)
    monkeypatch.setattr(flush, "_last_sweep", {})

    async def others(tx) -> None:
        await tx.execute("/* pipeshub:test.others */ INSERT INTO kg.graph_changes (org_id, ends) VALUES ($1, ARRAY['3:f1'])", org)
    with monkeypatch.context() as m:
        m.setattr(flush, "_own_role", lambda _tx: False)
        await run_in(pg_db, org, others, write=True)
    async def no_graph(tx) -> None:
        await tx.fetchval("/* pipeshub:test.no_graph */ SELECT 1")
    await run_in(pg_db, org, no_graph, write=True)
    await flush.settle_sweeps()
    assert (await state(pg_db, org, "app1"))[:2] == (None, "legacy")


async def is_current(db, org: str, app_id: str) -> bool:
    async def go(tx) -> bool:
        return await drainer.is_current(tx, org, app_id)
    return await run_in(db, org, go)


async def test_a_connector_is_current_only_when_verified_and_nothing_waits_that_can_alter_it(pg_db) -> None:
    p, org = await fresh_world(pg_db)
    assert not await is_current(pg_db, org, "app1")                           # legacy
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped
    assert await is_current(pg_db, org, "app1")                               # shadow, log empty
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "x8", "orgId": org, "recordName": "Never copied", "connectorId": "app2", "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    assert not await is_current(pg_db, org, "app1")                           # a pending connector kg has never seen may link anything
    await drain_org(pg_db, org)
    assert (await bf.backfill(pg_db, org, "app2", page=4)).flipped
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "x9", "orgId": org, "recordName": "Other connector", "connectorId": "app2", "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    assert await is_current(pg_db, org, "app1")                               # a change of another connector that links nothing to it
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "f1", "orgId": org, "recordName": "Renamed", "connectorId": "app1", "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    assert not await is_current(pg_db, org, "app1")                           # its own change waits
    await drain_org(pg_db, org)
    assert await is_current(pg_db, org, "app1")

    async def poison(tx) -> None:
        await tx.execute("/* pipeshub:test.poison */ INSERT INTO kg.graph_changes (org_id, ends) VALUES ($1, ARRAY['nope'])", org)
    await run_in(pg_db, org, poison, write=True)
    assert not await is_current(pg_db, org, "app1")                           # a row that cannot be read may name anything


async def test_a_promotion_refuses_a_connector_whose_kg_differs_from_the_graph(pg_db) -> None:
    p, org = await shadowed(pg_db)
    sk = await sk_of(pg_db, org, ("records", "f2"))
    await corrupt(pg_db, org, lambda tx, w: w.records_delete([sk]))
    with pytest.raises(bf.Refused, match="differs from the graph"):
        await bf.promote(pg_db, org, "app1")
    assert (await state(pg_db, org, "app1"))[1] == "shadow"                  # a refusal changes nothing; the backfill is what sends it back


async def test_writers_running_while_a_backfill_copies_leave_kg_equal_to_the_graph(pg_db) -> None:
    import asyncio
    p, org = await fresh_world(pg_db)

    async def writer() -> None:
        for op in OPS[5:20]:
            await op(p, org)
            await asyncio.sleep(0.01)
    task = asyncio.create_task(writer())
    outcomes = []
    while True:
        outcomes.append(await bf.backfill(pg_db, org, "app1", page=2))
        if task.done() and outcomes[-1].flipped:
            break
        await asyncio.sleep(0.05)
    await task
    final = await bf.backfill(pg_db, org, "app1", page=2)
    assert final.flipped and (await verify(pg_db, org, "app1")).ok
