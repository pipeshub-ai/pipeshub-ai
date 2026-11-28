"""Backfill, verify, promotion and the way back to the walk.

A backfill copies a connector (and the org's principals) into kg in batches that can be killed and resumed, drains the change log, verifies under the connector's lock and only then
flips it to ``shadow``; a promotion verifies again. The verify is tested by corrupting kg through the writers and expecting each kind of difference to be named. Turning the projection
off sends the connectors back to the walk, because the take-back deletes the only evidence that kg is behind.
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import drainer, modes, reader
from app.services.graph_db.postgres.kg import verify as verify_mod
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import lock_apps
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from app.services.graph_db.postgres.kg.reader import load_sks
from app.services.graph_db.postgres.kg.verify import (
    Result,
    verify_app,
    verify_principals,
)
from app.services.graph_db.postgres.kg.writers import RECORD_COLS, KgWriter

from .pg_support import new_org
from .test_kg_derivation import ctx, edge, prov
from .test_kg_projection import add_edge, delete_edge
from .test_kg_projection_races import OPS, world2
from .test_kg_read import _admin

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")


async def analyze_requests(org: str) -> list[int]:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        return [r["app_sk"] for r in await con.fetch("SELECT app_sk FROM kg.analyze_requests WHERE org_id = $1", org)]
    finally:
        await con.close()


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


async def test_the_principals_verify_reads_no_record_the_users_have_a_permission_on(pg_db, monkeypatch) -> None:
    """The closure is all the principals' verify compares, and no record goes into it: a page of users of a large org has permissions on hundreds of thousands of records, and
    reading each one's document and children outlasted the statement budget. u1 has a permission on f1 and its source account acct one on x1, so the old snapshot read both."""
    p, org = await fresh_world(pg_db)
    with ctx(org):      # a source account's own permission: its record is no more part of the closure than the user's
        await p.batch_create_edges([edge("acct", "users", "x1", "records", type="USER", role="READER")], "permission")
    assert (await bf.backfill(pg_db, org, "app1", page=3)).flipped
    monkeypatch.setattr(verify_mod, "PAGE", 1)        # a page of one user: u1's source account is read as its source account, not as a user of the page
    asked: set = set()
    for name in ("load_nodes", "load_has_children", "load_sks"):
        real = getattr(reader, name)

        async def spy(tx, o, keys, *a, _real=real, **k) -> object:
            asked.update(keys)
            return await _real(tx, o, keys, *a, **k)
        monkeypatch.setattr(reader, name, spy)

    async def go(tx) -> Result:
        return await verify_principals(tx, org)
    res = await run_in(pg_db, org, go)
    assert res.ok and res.expected > 0
    assert ("users", "u1") in asked and not {k for k in asked if k[0] in ("records", "recordGroups")}


async def test_the_principals_verify_still_names_each_closure_row_kg_lacks_or_the_graph_gained(pg_db) -> None:
    """The narrower read hides nothing the closure holds: a membership, the organisation, a source account and its badge group, each taken out of kg, are named missing; a
    membership the graph gained after the copy is named missing too."""
    p, org = await fresh_world(pg_db)
    assert (await bf.backfill(pg_db, org, "app1", page=3)).flipped

    async def principals() -> Result:
        return await run_in(pg_db, org, lambda tx: verify_principals(tx, org))
    assert (await principals()).ok
    u1, acct, app1 = [await sk_of(pg_db, org, k) for k in (("users", "u1"), ("users", "acct"), ("apps", "app1"))]
    rows = [(u1, await sk_of(pg_db, org, ("teams", "team1")), 0, "membership"), (u1, await sk_of(pg_db, org, ("organizations", org)), 0, "org"),
            (u1, acct, app1, "link"), (u1, await sk_of(pg_db, org, ("groups", "grp")), app1, "link_badge")]
    for u, q, a, via in rows:
        await corrupt(pg_db, org, lambda tx, w, k=(u, q, a): w.closure_delete([k]))
        res = await principals()
        assert not res.ok and f"c|{u}|{q}|{a} {via}" in res.missing, (via, res.missing)
        await corrupt(pg_db, org, lambda tx, w, r=(u, q, a, via): w.closure_set([r]))
        assert (await principals()).ok, via
    with ctx(org):
        await p.batch_create_edges([edge("u1", "users", "grp", "groups", type="USER")], "permission")      # not drained: kg is behind the graph
    res = await principals()
    assert not res.ok and any(m.startswith(f"c|{u1}|{rows[3][1]}|0 ") for m in res.missing), res.missing


async def test_a_connector_copied_after_its_neighbour_takes_the_neighbours_parent_out_of_leaf(pg_db) -> None:
    """f2 (first connector) is the parent of x1 (second). The first connector is copied and stamped while kg holds no row for x1, so f2 is stamped a leaf; copying the second
    connector must restamp f2, which its own restamp does not cover."""
    p, org = await fresh_world(pg_db)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")

    async def clear(tx) -> None:
        await KgWriter(tx).changes_delete([r["id"] for r in await tx.fetch("SELECT id FROM kg.graph_changes WHERE org_id = $1", org)])
    await run_in(pg_db, org, clear, write=True)

    async def leaf(tx) -> bool:
        return (await tx.fetchrow("SELECT r.leaf FROM kg.records r JOIN kg.node_keys n ON n.org_id = r.org_id AND n.sk = r.sk WHERE r.org_id = $1 AND n.id = 'f2'", org))["leaf"]
    await bf.backfill(pg_db, org, "app1", page=3)
    assert await run_in(pg_db, org, leaf) is True                          # kg holds no child of f2 yet
    await bf.backfill(pg_db, org, "app2", page=3)
    assert await run_in(pg_db, org, leaf) is False


async def test_a_connector_copied_after_its_neighbour_gives_the_neighbours_child_its_path(pg_db) -> None:
    """The mirror: x1 (second connector) hangs under f2 (first). The second connector is copied and stamped while kg holds no row for f2, so x1 has no path; copying the first
    must restamp x1, which its own restamp does not cover."""
    p, org = await fresh_world(pg_db)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")

    async def clear(tx) -> None:
        await KgWriter(tx).changes_delete([r["id"] for r in await tx.fetch("SELECT id FROM kg.graph_changes WHERE org_id = $1", org)])
    await run_in(pg_db, org, clear, write=True)

    async def has_path(tx) -> bool:
        return (await tx.fetchrow("SELECT r.path IS NOT NULL AS p FROM kg.records r JOIN kg.node_keys n ON n.org_id = r.org_id AND n.sk = r.sk WHERE r.org_id = $1 AND n.id = 'x1'", org))["p"]
    await bf.backfill(pg_db, org, "app2", page=3)
    assert await run_in(pg_db, org, has_path) is False                     # kg holds no row for f2 yet
    await bf.backfill(pg_db, org, "app1", page=3)
    assert await run_in(pg_db, org, has_path) is True
    assert (await verify(pg_db, org, "app2")).ok and (await verify(pg_db, org, "app1")).ok


async def test_a_backfill_that_is_cut_resumes_at_its_last_committed_batch(pg_db, monkeypatch) -> None:
    p, org = await fresh_world(pg_db)
    part = await bf.backfill(pg_db, org, "app1", page=2, max_batches=1)
    assert not part.flipped and part.reason == "unfinished"                 # only some nodes are in kg: the copy stops at its budget, nothing is verified or promised
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


async def _fetched(tx) -> int:
    """Rows of graph.nodes this transaction has read so far."""
    return await tx.fetchval("SELECT coalesce(sum(seq_tup_read + coalesce(idx_tup_fetch, 0)), 0) FROM pg_stat_xact_user_tables WHERE schemaname = 'graph' AND relname LIKE 'nodes%'")


@pytest.mark.pg_table_lock           # changes the planner statistics or storage settings of shared tables
async def test_a_copy_page_of_records_reads_the_page_not_the_records_after_its_cursor(pg_db) -> None:
    """3,000 records of another connector sort between the cursor and app1's next records: a page of 10 of app1's records reads about 10 rows, fresh and analysed (the role's own statement cannot seek
    the connector's index under row-level security, and read every record after the cursor). The page is app1's next records in byte order."""
    p, org = await fresh_world(pg_db)
    rec = {"connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": f"m{i:02d}", "recordName": f"M{i}", **rec} for i in range(30)], "records")
    await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1::text, 'records', 'm09x' || g, jsonb_build_object('_key', 'm09x' || g, 'orgId', $1::text,
          'connectorId', 'app9', 'recordName', 'N' || g) FROM generate_series(1, 3000) g""", org)
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE graph.nodes")

        async def go(tx) -> tuple[list, int]:
            before = await _fetched(tx)
            page = await bf.app_nodes(tx, org, "app1", ("records", "m09"), 10)
            return page, await _fetched(tx) - before
        page, read = await run_in(pg_db, org, go)
        assert page == [("records", f"m{i:02d}") for i in range(10, 20)]
        assert read <= 20, f"{state}: {read} rows of graph.nodes read for a page of 10"


async def test_a_copy_whose_cursor_older_code_wrote_in_the_records_copies_them_again_from_the_first(pg_db) -> None:
    """Older code paged a connector's records in the database's collation, where '-z' sorts after 'M' (the dash is ignored) and in byte order before it. A copy left at
    'records|M' by that code must not go on after 'M' in byte order (it would never copy '-z'): it copies the records again from the first and ends verified."""
    p, org = await fresh_world(pg_db)
    rec = {"connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, **rec} for k in ("-z", "B", "M", "q")], "records")

    async def clear(tx) -> None:
        await KgWriter(tx).changes_delete([r["id"] for r in await tx.fetch("SELECT id FROM kg.graph_changes WHERE org_id = $1", org)])
    await run_in(pg_db, org, clear, write=True)
    await bf.backfill_scope(pg_db, org, None)
    await bf.backfill_scope(pg_db, org, "app1", page=2, max_batches=2)      # the app and its two groups: no record copied yet

    async def older(tx) -> None:
        await KgWriter(tx).projection_set((await load_sks(tx, org, {("apps", "app1")}))[("apps", "app1")], epoch=0, mode="legacy", digest=None, cursor="records|M", verified_at=None)
    await run_in(pg_db, org, older, write=True)
    out = await bf.backfill(pg_db, org, "app1", page=2)
    assert out.flipped and out.result.ok, out.result.summary() if out.result else out.reason


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


async def test_a_restart_removes_a_row_no_node_explains_with_its_access_list(pg_db) -> None:
    p, org = await shadowed(pg_db)
    app = await sk_of(pg_db, org, ("apps", "app1"))
    ghost = await KgWriterNodes.make(pg_db, org, ("records", "ghost"))

    async def plant(tx, w) -> None:
        await w.records_upsert([row(ghost, "ghost", app_sk=app, name="ghost")])
        await w.scopes_set([(ghost, 3, app, True, False, False, False)])
    await corrupt(pg_db, org, plant)

    async def held(tx) -> tuple[int, int]:
        return (await tx.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1 AND sk = $2", org, ghost), await tx.fetchval("SELECT count(*) FROM kg.scopes WHERE org_id = $1 AND sk = $2", org, ghost))
    assert await run_in(pg_db, org, held) == (1, 1)
    await bf.backfill(pg_db, org, "app1", page=4, restart=True)
    assert await run_in(pg_db, org, held) == (0, 0)


async def test_an_access_list_whose_record_row_is_gone_fails_only_its_own_connector_stays_hidden_and_a_restart_removes_it(pg_db) -> None:
    from app.services.graph_db.postgres.kg import restamp
    p, org = await shadowed(pg_db)
    assert (await bf.backfill(pg_db, org, "app2", page=4)).flipped
    app = await sk_of(pg_db, org, ("apps", "app1"))
    orphan = await KgWriterNodes.make(pg_db, org, ("records", "orphan"))
    await corrupt(pg_db, org, lambda tx, w: w.scopes_set([(orphan, 3, app, False, False, False, False)]))        # what an older process leaves when it deletes the record alone

    async def visible(tx) -> set[int]:
        return {r["sk"] for r in await tx.fetch("SELECT s.sk FROM kg.scopes s WHERE s.org_id = $1 AND NOT (" + restamp.HIDDEN_SCOPE + ")", org)}
    assert orphan not in await run_in(pg_db, org, visible) and await run_in(pg_db, org, visible)             # the orphan is not counted; the others are
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith(f"sc|{orphan}") for m in res.extra)
    other = await verify(pg_db, org, "app2")
    assert other.ok and not any(m.startswith("sc|") for m in other.extra)                                   # another connector is not held up by it
    assert (await bf.backfill(pg_db, org, "app1", page=4, restart=True)).flipped                            # the restart removes it, and the connector verifies again
    assert (await verify(pg_db, org, "app1")).ok

    async def held(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.scopes WHERE org_id = $1 AND sk = $2", org, orphan)
    assert await run_in(pg_db, org, held) == 0


async def test_the_restart_keeps_an_orphan_scope_another_connector_takes_over_meanwhile(pg_db) -> None:
    """An orphan has no record row, so the connector that takes its key over locks only itself: the restart waits for it and then sees no orphan."""
    p, org = await shadowed(pg_db)
    assert (await bf.backfill(pg_db, org, "app2", page=4)).flipped
    app1, app2 = await sk_of(pg_db, org, ("apps", "app1")), await sk_of(pg_db, org, ("apps", "app2"))
    orphan = await KgWriterNodes.make(pg_db, org, ("records", "orphan"))
    await corrupt(pg_db, org, lambda tx, w: w.scopes_set([(orphan, 3, app1, False, False, False, False)]))
    written, commit = asyncio.Event(), asyncio.Event()

    async def takes_over(tx) -> None:
        await lock_apps(tx, org, {"app2"}, org_wide=False)
        w = KgWriter(tx)
        await w.records_upsert([row(orphan, "orphan", app_sk=app2)])
        await w.scopes_set([(orphan, 3, app2, False, False, False, False)])
        written.set()
        await commit.wait()

    writer = asyncio.create_task(run_in(pg_db, org, takes_over, write=True))
    await asyncio.wait_for(written.wait(), 30)
    restart = asyncio.create_task(bf.backfill_scope(pg_db, org, "app1", restart=True, max_batches=0))
    await asyncio.sleep(0.5)
    assert not restart.done()
    commit.set()
    await writer
    await restart

    async def scope_app(tx) -> int | None:
        return await tx.fetchval("SELECT app_sk FROM kg.scopes WHERE org_id = $1 AND sk = $2", org, orphan)
    assert await run_in(pg_db, org, scope_app) == app2


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
    assert await analyze_requests(org) == []                                 # a backfill alone asks nothing: the connector is not served yet
    res = await bf.promote(pg_db, org, "app1")
    assert res.ok and (await state(pg_db, org, "app1"))[1] == "enforce"
    sks = await run_in(pg_db, org, lambda tx: load_sks(tx, org, {("apps", "app1")}))
    assert await analyze_requests(org) == [sks[("apps", "app1")]]            # the first load asks the worker to analyze kg (kg.analyze_requests)
    again = await bf.backfill(pg_db, org, "app1", page=4)                    # a second backfill of an enforced connector keeps it enforced
    assert again.flipped and (await state(pg_db, org, "app1"))[1] == "enforce"
    await bf.demote(pg_db, org, "app1")
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

    await _admin("INSERT INTO kg.graph_changes (org_id, ends) VALUES ($1, ARRAY['nope'])", org)      # no service can log it (kg_0022): the administrator plants it
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

    log_row = "INSERT INTO kg.graph_changes (org_id, app_id, ends) VALUES ($1, 'app2', ARRAY[$2])"
    await _admin(log_row, org, "3:x1")
    assert await is_current(pg_db, org, "app1")                               # a readable change of another known connector that links nothing
    await _admin(log_row, org, "nope")
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


@pytest.mark.parametrize("last", [True, False], ids=["finishing_batch", "copying_batch"])
async def test_a_restart_that_demotes_the_copy_while_a_batch_runs_is_not_overwritten_by_its_cursor(pg_db, monkeypatch, last: bool) -> None:
    """A batch reads the copy cursor before it takes the connector's lock. A restart that demoted the copy in between (its reset holds the org key exclusively) wins: the batch
    writes no cursor over it (not "done" with this code's epoch, which the listing would read from, nor a cursor past the start), and the copy starts again from the first node."""
    p, org = await fresh_world(pg_db)
    real, seen, demoted = bf.app_nodes, [], []

    async def nodes(tx, o, app, after, limit) -> list:
        got = await real(tx, o, app, after, limit)
        seen.append(after)
        if not demoted and after is not None and (not got if last else bool(got)):
            demoted.append(len(seen))
            await _admin("""UPDATE kg.projection_state s SET backfill_cursor = NULL, mode = 'legacy', code_epoch = 0, verified_at = NULL FROM kg.node_keys k
                WHERE k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1 AND s.org_id = $1 AND k.id = 'app1'""", org)
        return got
    monkeypatch.setattr(bf, "app_nodes", nodes)
    await bf.backfill_scope(pg_db, org, None)
    await bf.backfill_scope(pg_db, org, "app1", page=3)
    assert demoted, "the batch the restart was meant to race never ran"
    assert None in seen[demoted[0]:], f"the copy did not start again after the restart: {seen}"      # on main: the batch wrote its cursor over the demotion
    assert await state(pg_db, org, "app1") == ("done", "legacy", CODE_EPOCH)


async def test_a_finishing_batch_does_not_clear_an_inexact_mark_set_after_it_read_the_copy_state(pg_db, monkeypatch) -> None:
    """Another copy marks the copy inexact at the same cursor between the finishing batch's read and its lock: the batch writes nothing over it, and the copy ends
    not listed from (epoch 0), never at this code's epoch with the mark cleared."""
    p, org = await fresh_world(pg_db)
    real, marked = bf.app_nodes, []

    async def nodes(tx, o: str, app: str, after: tuple | None, limit: int) -> list:
        got = await real(tx, o, app, after, limit)
        if not got and after is not None and not marked:
            marked.append(after)
            await _admin("""UPDATE kg.projection_state s SET verify_digest = $2 FROM kg.node_keys k
                WHERE k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1 AND s.org_id = $1 AND k.id = 'app1'""", org, bf.INEXACT)
        return got
    monkeypatch.setattr(bf, "app_nodes", nodes)
    await bf.backfill_scope(pg_db, org, None)
    await bf.backfill_scope(pg_db, org, "app1", page=3)
    assert marked, "the finishing batch never ran"
    assert await state(pg_db, org, "app1") == ("done", "legacy", 0)      # on main: ("done", "legacy", CODE_EPOCH), the mark cleared


@pytest.mark.parametrize("calls", ["one_call", "a_batch_per_call"])
async def test_a_copy_that_meets_a_value_kg_refuses_goes_on_past_it_and_ends_not_listed_from(pg_db, calls: str) -> None:
    """The projection takes a connector off the readers when it writes a row without a value kg refuses, and clears its copy cursor with it. A copy that meets such a
    record after earlier batches have committed goes on past it and ends, not listed from (epoch 0), in one call or a batch per call as the bootstrap copies; it never
    starts again from the first node (where it would meet the same record every time)."""
    p, org = await fresh_world(pg_db)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "zz-refused", "recordName": "Z", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR",
                                     "accessRule": "INHERITED"}], "records")
    await bf.backfill_scope(pg_db, org, None)

    async def copy() -> int:
        if calls == "one_call":
            return await bf.backfill_scope(pg_db, org, "app1", page=2)
        n = 0
        for _ in range(50):
            n += await bf.backfill_scope(pg_db, org, "app1", page=2, max_batches=1)
            if (await state(pg_db, org, "app1"))[0] == "done":
                return n
        raise AssertionError("the copy did not end in 50 calls")
    batches = await asyncio.wait_for(copy(), timeout=60)      # on main the copy starts again from the first node at the refused record, forever
    assert batches >= 3
    assert await state(pg_db, org, "app1") == ("done", "legacy", 0)


async def test_the_verify_names_a_stale_stamp_and_a_backfill_repairs_it(pg_db) -> None:
    p, org = await shadowed(pg_db)
    async def stamped(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1 AND (class_id IS NOT NULL OR legacy_reason IS NOT NULL)", org)
    assert await run_in(pg_db, org, stamped) > 0                              # the backfill restamped the connector (the copy itself writes none)
    sk = await sk_of(pg_db, org, ("records", "fold"))
    await corrupt(pg_db, org, lambda tx, w: w.stamps_set([{"sk": sk, "coll": 3, "id": "fold", "kind": 3, "path": None, "class_id": None, "self_lit": False, "seed": False, "own_grant": False,
                                                          "under_deleted": False, "legacy_reason": 3, "leaf": True, "r13": False}]))
    res = await verify(pg_db, org, "app1")
    assert not res.ok and any(m.startswith("s|fold") for m in res.changed), res.changed
    again = await bf.backfill(pg_db, org, "app1", page=4)                          # the backfill restamps the connector whole: the stale stamp is repaired
    assert again.flipped and (await verify(pg_db, org, "app1")).ok
