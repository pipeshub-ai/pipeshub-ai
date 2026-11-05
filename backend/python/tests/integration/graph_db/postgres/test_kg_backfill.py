"""Backfill, verify, promotion and the way back to the walk.

A backfill copies a connector (and the org's principals) into kg in batches that can be killed and resumed, drains the change log, verifies under the connector's lock and only then
flips it to ``shadow``; a promotion verifies again. The verify is tested by corrupting kg through the writers and expecting each kind of difference to be named. Turning the projection
off sends the connectors back to the walk, because the take-back deletes the only evidence that kg is behind.
"""
from __future__ import annotations

import pytest

from app.core.org_context import spawn_detached
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


# ---- a take-back in another process while a flip or a promotion verifies ----------------------------------------------------------------------------------------------------------
async def interleave(p, org: str) -> None:
    """What a process whose projection is off does while the flip runs: a change commits (its log row is written), and the sweep tries to take the row back and demote."""
    await OPS[5](p, org)                                   # a record moves into app2
    await flush._sweep(p._db, org)


@pytest.mark.parametrize("where", ["after_verify", "after_state_read"])
@pytest.mark.parametrize("which", ["promote", "flip"])
async def test_a_take_back_cannot_slip_between_a_verify_and_the_write_that_trusts_it(pg_db, monkeypatch, which: str, where: str) -> None:
    """The flip holds the connector's lock from before its verify to its write: a take-back that would delete a change's log row and demote must leave the row (so the flip sees a
    change is waiting) or wait; it must never demote between the verify and the write and be overwritten by ``shadow`` or ``enforce`` while the log is empty."""
    p, org = await fresh_world(pg_db)
    if which == "promote":
        assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped
    real_verify, real_state, hit = bf.verify_app, modes.state, {"verified": False, "fired": False}

    async def verify_then(tx, o, a) -> Result:
        res = await real_verify(tx, o, a)
        hit["verified"] = True
        if where == "after_verify" and not hit["fired"]:
            hit["fired"] = True
            await spawn_detached(interleave(p, org))         # another process: its own context, its own units
        return res

    async def state_then(tx, o, sk) -> tuple:
        got = await real_state(tx, o, sk)
        if where == "after_state_read" and hit["verified"] and not hit["fired"]:
            hit["fired"] = True
            await spawn_detached(interleave(p, org))
        return got
    monkeypatch.setattr(bf, "verify_app", verify_then)
    monkeypatch.setattr(modes, "state", state_then)
    try:
        if which == "promote":
            if where == "after_verify":          # the change is in the log when the flip counts it: refused
                with pytest.raises(bf.Refused):
                    await bf.promote(pg_db, org, "app1")
            else:                                # it commits after the count: the state may be written, with the change still in the log (not current until drained)
                await bf.promote(pg_db, org, "app1")
        else:
            out = await bf.backfill(pg_db, org, "app1", page=4)
            assert out.flipped is (where == "after_state_read")
    finally:
        monkeypatch.undo()
        monkeypatch.setenv("PG_KG_PROJECT", "1")
    assert hit["fired"]
    cursor, mode, _ = await state(pg_db, org, "app1")
    waiting = await run_in(pg_db, org, lambda tx: tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org))
    res = await verify(pg_db, org, "app1")
    assert mode == "legacy" or res.ok or waiting, f"{mode} with {waiting} rows in the log while kg differs from the graph: {res.summary()}"
    assert waiting or mode == "legacy"                      # either the change is still visible in the log, or the connector went back to the walk


# ---- T1: the verify names an extra grant and an extra closure row; an unreadable row makes the connector not current --------------------------------------------------------------
async def test_the_verify_names_a_grant_no_node_explains_and_a_closure_row_no_user_explains(pg_db) -> None:
    p, org = await shadowed(pg_db)
    app = await sk_of(pg_db, org, ("apps", "app1"))
    user = await sk_of(pg_db, org, ("users", "u1"))
    gone = await KgWriterNodes.make(pg_db, org, ("records", "never-a-node"))
    ghost_user = await KgWriterNodes.make(pg_db, org, ("users", "never-a-user"))
    await corrupt(pg_db, org, lambda tx, w: w.grants_set([(user, gone, "USER", "READER", app, False)]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"g|{user}|{gone}|USER") for m in res.extra), res.extra
    await corrupt(pg_db, org, lambda tx, w: w.grants_delete([(user, gone, "USER")]))
    assert (await verify(pg_db, org, "app1")).ok
    await corrupt(pg_db, org, lambda tx, w: w.closure_set([(ghost_user, user, 0, "membership")]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"c|{ghost_user}|{user}|0") for m in res.extra), res.extra


async def test_a_row_of_the_log_that_cannot_be_read_makes_the_connector_not_current_even_when_nothing_else_does(pg_db) -> None:
    """The row is logged under a connector that is known and links nothing, so the poison check is the only thing that can refuse."""
    p, org = await fresh_world(pg_db)
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped and (await bf.backfill(pg_db, org, "app2", page=4)).flipped

    async def log_row(tx, ends: str) -> None:
        await tx.execute("/* pipeshub:test.row */ INSERT INTO kg.graph_changes (org_id, app_id, ends) VALUES ($1, 'app2', ARRAY[$2])", org, ends)
    await run_in(pg_db, org, lambda tx: log_row(tx, "3:x1"), write=True)
    assert await is_current(pg_db, org, "app1")                               # a readable change of another known connector that links nothing
    await run_in(pg_db, org, lambda tx: log_row(tx, "nope"), write=True)
    assert not await is_current(pg_db, org, "app1")


# ---- F2: the reader's question ------------------------------------------------------------------------------------------------------------------------------------------------------
async def serves(db, org: str, app_id: str) -> bool:
    return await run_in(db, org, lambda tx: drainer.serves(tx, org, app_id))


async def set_state(db, org: str, app_id: str | None, **kw: object) -> None:
    async def go(tx) -> None:
        sk = 0 if app_id is None else (await load_sks(tx, org, {("apps", app_id)}))[("apps", app_id)]
        await KgWriter(tx).projection_set(sk, **kw)
    await run_in(db, org, go, write=True)


async def test_kg_answers_for_a_connector_only_in_enforce_at_this_code_verified_with_the_principals_copied_and_current(pg_db) -> None:
    import datetime as dt
    p, org = await fresh_world(pg_db)
    assert not await serves(pg_db, org, "app1")                                 # legacy
    assert (await bf.backfill(pg_db, org, "app1", page=4)).flipped
    assert await is_current(pg_db, org, "app1") and not await serves(pg_db, org, "app1")     # shadow only compares
    await bf.promote(pg_db, org, "app1")
    assert await serves(pg_db, org, "app1")
    now = dt.datetime.now(dt.timezone.utc)
    await set_state(pg_db, org, "app1", epoch=CODE_EPOCH - 1, mode="enforce", digest=None, cursor="done", verified_at=now)
    assert not await serves(pg_db, org, "app1")                                 # verified by older code
    await set_state(pg_db, org, "app1", epoch=CODE_EPOCH, mode="enforce", digest=None, cursor="done", verified_at=None)
    assert not await serves(pg_db, org, "app1")                                 # never verified
    await set_state(pg_db, org, "app1", epoch=CODE_EPOCH, mode="enforce", digest=None, cursor="done", verified_at=now)
    assert await serves(pg_db, org, "app1")
    await set_state(pg_db, org, None, epoch=0, mode="legacy", digest=None, cursor="Z|nothing", verified_at=None)
    assert not await serves(pg_db, org, "app1")                                 # the principals' copy is not finished
    await set_state(pg_db, org, None, epoch=0, mode="legacy", digest=None, cursor="done", verified_at=None)
    assert await serves(pg_db, org, "app1")
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "f1", "orgId": org, "recordName": "Renamed", "connectorId": "app1", "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    assert not await serves(pg_db, org, "app1")                                 # a change waits


# ---- F3: what the verify also names ----------------------------------------------------------------------------------------------------------------------------------------------
async def test_the_verify_names_a_link_whose_child_is_gone_and_a_grant_on_a_gone_node_that_has_no_connector(pg_db) -> None:
    p, org = await shadowed(pg_db)
    fold = await sk_of(pg_db, org, ("records", "fold"))
    user = await sk_of(pg_db, org, ("users", "u1"))
    child = await KgWriterNodes.make(pg_db, org, ("records", "deleted-child"))
    node = await KgWriterNodes.make(pg_db, org, ("records", "deleted-node"))
    await corrupt(pg_db, org, lambda tx, w: w.links_set([(fold, 1, child)]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"l|{fold}|1|{child}") for m in res.extra), res.extra
    await corrupt(pg_db, org, lambda tx, w: w.links_delete([(fold, 1, child)]))
    await corrupt(pg_db, org, lambda tx, w: w.grants_set([(user, node, "USER", "READER", None, False)]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"g|{user}|{node}|USER") for m in res.extra), res.extra
    await corrupt(pg_db, org, lambda tx, w: w.grants_delete([(user, node, "USER")]))
    assert (await verify(pg_db, org, "app1")).ok


async def test_a_demotion_made_while_a_backfill_is_ending_is_not_undone_by_its_flip(pg_db, monkeypatch) -> None:
    """The demotion clears the copy cursor; a flip that finds the cursor gone verifies, but does not make a copy of it that somebody has just taken away."""
    p, org = await fresh_world(pg_db)
    real = bf.drain_org

    async def demote_then_drain(db, o, **k) -> object:
        await bf.demote(db, o, "app1")
        return await real(db, o, **k)
    monkeypatch.setattr(bf, "drain_org", demote_then_drain)
    out = await bf.backfill(pg_db, org, "app1", page=4)
    assert not out.flipped and out.result.ok
    assert (await state(pg_db, org, "app1"))[:2] == (None, "legacy")


async def test_the_verify_names_a_stale_stamp_and_a_backfill_repairs_it(pg_db) -> None:
    p, org = await shadowed(pg_db)
    async def stamped(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1 AND (class_id IS NOT NULL OR legacy_reason IS NOT NULL)", org)
    assert await run_in(pg_db, org, stamped) > 0                              # the backfill restamped the connector (the copy itself writes none)
    sk = await sk_of(pg_db, org, ("records", "fold"))
    await corrupt(pg_db, org, lambda tx, w: w.stamps_set([{"sk": sk, "coll": 3, "id": "fold", "kind": 3, "path": None, "class_id": None, "self_lit": False, "seed": False, "own_grant": False,
                                                          "under_deleted": False, "legacy_reason": 3, "leaf": True}]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith("s|fold") for m in res.changed), res.changed
    again = await bf.backfill(pg_db, org, "app1", page=4)                          # the backfill restamps the connector whole: the stale stamp is repaired
    assert again.flipped and (await verify(pg_db, org, "app1")).ok
