"""The projection (``kg.project``): after any change to the graph, projecting the nodes the change names (and what they pull in) leaves kg equal to what a fresh projection of the
whole graph would write; the stats say how much was written; two projections of one connector do not interleave."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import lock_apps
from app.services.graph_db.postgres.kg.model import edge_ends
from app.services.graph_db.postgres.kg.project import Stats, decode, project

from .pg_support import new_org
from .test_kg_derivation import FOCUS, ctx, edge, held, load_world, prov
from .test_kg_read import _ask, _decide, flip

pytestmark = pytest.mark.integration

async def run(p, org: str, focus: set) -> Stats:
    async def go(tx) -> Stats:
        return await project(tx, org, focus)
    with ctx(org):
        return await p._q(go, None, write=True)


def key(coll: str, nid: str) -> tuple[str, str]:
    return (coll, nid)


def same(x: dict, org_x: str, y: dict, org_y: str) -> bool:
    """Two organisations' typed rows, equal when the organisation's own node (keyed by its id) is called the same."""
    def norm(h: dict, org: str) -> dict:
        def ren(i: str) -> str:
            return "ORG" if i == org else i
        return {"records": {ren(k): v for k, v in h["records"].items()}, "links": {(ren(a), k, ren(c)) for a, k, c in h["links"]},
                "grants": {(ren(a), ren(b), t): v for (a, b, t), v in h["grants"].items()}, "closure": {(ren(u), ren(pr), app): via for (u, pr, app), via in h["closure"].items()}}
    return norm(x, org_x) == norm(y, org_y)


async def everything(p, org: str) -> set:
    """Every node of the world, with the organisation's own."""
    return {*FOCUS, ("organizations", org)}


# Each change: what it does to the graph, and the nodes the change capture would name for it (the ends of an edge, or the node itself).
async def delete_node(p, org, coll, nid) -> set:
    """The change capture logs the node and the ends of every edge the delete takes with it."""
    async def edges(tx) -> list:
        return await tx.fetch("SELECT coll, from_coll, from_id, to_coll, to_id, doc FROM graph.edges WHERE org_id = $1 AND ((from_coll = $2 AND from_id = $3) OR (to_coll = $2 AND to_id = $3))", org, coll, nid)
    with ctx(org):
        rows = await p._q(edges, None)
        await p.delete_nodes([nid], coll)
    touched = {(coll, nid)}
    for r in rows:
        touched |= edge_ends(r["coll"], (r["from_coll"], r["from_id"]), (r["to_coll"], r["to_id"]), json.loads(r["doc"]) if isinstance(r["doc"], str) else dict(r["doc"]))
    return touched


async def delete_edge(p, org, frm, to, coll, **props) -> set:
    with ctx(org):
        await p.batch_delete_edges([edge(frm[1], frm[0], to[1], to[0], **props)], coll)
    return edge_ends(coll, frm, to, props)


async def add_edge(p, org, frm, to, coll, **props) -> set:
    with ctx(org):
        await p.batch_create_edges([edge(frm[1], frm[0], to[1], to[0], **props)], coll)
    return edge_ends(coll, frm, to, props)


async def update_node(p, org, coll, nid, **fields) -> set:
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": nid, **fields}], coll)
    return {(coll, nid)}


CHANGES = {
    "a record is deleted": lambda p, o: delete_node(p, o, "records", "f2"),
    "a folder is deleted": lambda p, o: delete_node(p, o, "records", "fold"),
    "the parent link of a record is removed": lambda p, o: delete_edge(p, o, ("records", "fold"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    "a grant is revoked": lambda p, o: delete_edge(p, o, ("users", "u1"), ("records", "f1"), "permission", type="USER"),
    "a user leaves the team": lambda p, o: delete_edge(p, o, ("users", "u1"), ("teams", "team1"), "permission", type="USER"),
    "the team is deleted": lambda p, o: delete_node(p, o, "teams", "team1"),
    "a record group is deleted": lambda p, o: delete_node(p, o, "recordGroups", "g2"),
    "the connector is deleted": lambda p, o: delete_node(p, o, "apps", "app1"),
    "the user is deleted": lambda p, o: delete_node(p, o, "users", "u1"),
    "a record is restored": lambda p, o: update_node(p, o, "records", "f1", isDeleted=False),
    "a record is renamed": lambda p, o: update_node(p, o, "records", "f1", recordName="Renamed"),
    "a record gets a second parent": lambda p, o: add_edge(p, o, ("recordGroups", "g2"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD"),
    "a granted record gets its first child": lambda p, o: add_edge(p, o, ("records", "f1"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD"),
    "a granted record loses its only child": lambda p, o: _first_child_then_gone(p, o),
    "a record gets a grant": lambda p, o: add_edge(p, o, ("users", "u1"), ("records", "f2"), "permission", type="USER", role="OWNER"),
}


async def _first_child_then_gone(p, org) -> set:
    await add_edge(p, org, ("records", "f1"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD")
    return await delete_edge(p, org, ("records", "f1"), ("records", "f2"), "nodeRelations", relationshipType="PARENT_CHILD")


@pytest.mark.parametrize("name", list(CHANGES))
async def test_projecting_a_change_equals_projecting_the_whole_graph_afresh(pg_db, name: str) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    await load_world(p, a)
    await run(p, a, await everything(p, a))
    touched = await CHANGES[name](p, a)
    await run(p, a, touched)
    await load_world(p, b)
    await CHANGES[name](p, b)
    await run(p, b, await everything(p, b))
    assert same(await held(p, a), a, await held(p, b), b), name


async def test_a_sequence_of_changes_each_projected_ends_where_a_fresh_projection_does(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    await load_world(p, a)
    await run(p, a, await everything(p, a))
    await load_world(p, b)
    for name in ("a record is renamed", "a record gets a second parent", "a grant is revoked", "a user leaves the team", "a folder is deleted", "a record is restored"):
        await run(p, a, await CHANGES[name](p, a))
        await CHANGES[name](p, b)
    await run(p, b, await everything(p, b))
    assert same(await held(p, a), a, await held(p, b), b)


async def test_the_stats_count_what_was_written_and_a_repeat_writes_no_difference(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await load_world(p, org)
    first = await run(p, org, await everything(p, org))
    assert first.records == 6 and first.links == 2 and first.grants == 3 and first.closure == 3 and first.apps == {"app1"} | set()
    again = await run(p, org, await everything(p, org))
    assert (again.links, again.grants, again.closure, again.records_deleted) == (0, 0, 0, 0)
    gone = await delete_edge(p, org, ("users", "u1"), ("records", "f1"), "permission", type="USER")
    assert (await run(p, org, gone)).grants == 1


async def test_a_connector_shared_across_connectors_marks_both_and_a_gone_connector_goes_off_the_readers(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await load_world(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app2", "name": "Other", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "x1", "recordName": "X", "connectorId": "app2", "orgId": org}], "records")
        await p.batch_create_edges([edge("fold", "records", "x1", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await run(p, org, {*FOCUS, ("apps", "app2"), ("records", "x1")})

    async def flags(tx) -> set:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        return {ids[r["app_sk"]] for r in await tx.fetch("SELECT app_sk FROM kg.projection_state WHERE org_id = $1 AND cross_app", org)}
    with ctx(org):
        assert await p._q(flags, None) == {"app1", "app2"}

    # The other connector goes with its record: the change is projected, and no row of it may be left (its record, its link under the folder, its grants), as in a
    # whole projection of the graph afresh. The cross-connector flags are sticky by design (written once, so that a pending change of one connector outdates the other).
    touched = await delete_node(p, org, "records", "x1") | await delete_node(p, org, "apps", "app2")
    await run(p, org, touched)
    with ctx(org):
        assert await p._q(flags, None) == {"app1", "app2"}
    fresh = new_org()
    await load_world(p, fresh)
    await run(p, fresh, await everything(p, fresh))
    assert same(await held(p, org), org, await held(p, fresh), fresh)


async def test_a_projection_of_one_connector_waits_for_another_holding_its_lock(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await load_world(p, org)
    held_open = asyncio.Event()
    release = asyncio.Event()

    async def holder(tx) -> None:
        await lock_apps(tx, org, {"app1"}, org_wide=False)
        held_open.set()
        await release.wait()

    async def holding() -> None:
        with ctx(org):
            await p._q(holder, None, write=True)
    task = asyncio.create_task(holding())
    await held_open.wait()
    other = asyncio.create_task(run(p, org, {("records", "f1")}))
    await asyncio.sleep(0.5)
    assert not other.done()                                  # the projection of the same connector waits for the lock
    release.set()
    await task
    await asyncio.wait_for(other, 20)


async def _three_connectors(p, db, org: str) -> None:
    """The world of ``load_world`` (connector app1) and two more connectors, app2 and app3, each with a record; all three copied, verified and served."""
    await load_world(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": a, "name": a, "type": "DRIVE", "orgId": org} for a in ("app2", "app3")], "apps")
        await p.batch_upsert_nodes([{"_key": f"x{n}", "recordName": f"X{n}", "connectorId": f"app{n}", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for n in (2, 3)], "records")
    assert await flip(db, org) == (3, 3)


async def _modes(p, org: str) -> dict[str, str]:
    async def go(tx) -> dict[str, str]:
        return {r["id"]: r["mode"] for r in await tx.fetch("""SELECT k.id, s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1""", org)}
    with ctx(org):
        return await p._q(go, None)


async def _refused_move(p, db, org: str, caplog, doc: dict) -> Stats:
    """f1 (app1's) changes to ``doc`` with an access rule the records writer refuses, in one batch with a rename of app3's x3."""
    await _three_connectors(p, db, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "f1", "recordName": "Moved", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "accessRule": "ADMIN", **doc}], "records")
        await p.batch_upsert_nodes([{"_key": "x3", "recordName": "Renamed", "connectorId": "app3", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    with caplog.at_level(logging.ERROR, logger="app.services.graph_db.postgres.kg.project"):
        st = await run(p, org, {("records", "f1"), ("records", "x3")})
    assert st.refused == {("records", "f1")} and any("rule column of records f1" in r.getMessage() for r in caplog.records)
    assert (await held(p, org))["records"]["x3"][4] == "Renamed"           # the rest of the batch is written
    return st


async def test_a_refused_record_that_moves_to_another_connector_leaves_no_row_under_the_one_it_left(pg_db, caplog) -> None:
    """f1 moves from app1 to app2 and its new access rule is refused. Its row is rewritten under app2 with the refused column empty, so app1 no longer holds (or serves) the row
    it had; app2, whose copy of f1 is not exact, goes off the readers; app1 and app3 stay served, and a drain of the log goes through."""
    p, org = prov(pg_db), new_org()
    await _refused_move(p, pg_db, org, caplog, {"connectorId": "app2"})
    f1 = (await held(p, org))["records"]["f1"]
    assert (f1[1], f1[4], f1[10]) == ("app2", "Moved", None)
    assert await _modes(p, org) == {"app1": "enforce", "app2": "legacy", "app3": "enforce"}
    await drain_org(pg_db, org)
    assert (await held(p, org))["records"]["f1"][1] == "app2"


async def test_a_refused_record_whose_connector_is_cleared_leaves_no_row_under_the_one_it_left(pg_db, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _refused_move(p, pg_db, org, caplog, {"connectorId": None})       # a stored document keeps the keys an upsert leaves out: the connector is cleared, not omitted
    f1 = (await held(p, org))["records"]["f1"]
    assert (f1[1], f1[4], f1[10]) == (None, "Moved", None)
    assert await _modes(p, org) == {"app1": "enforce", "app2": "enforce", "app3": "enforce"}


async def test_a_refused_record_that_stays_takes_only_its_own_connector_off_the_readers(pg_db, caplog) -> None:
    """f1 stays in app1 and is renamed; its access rule is refused. The row is written (renamed, the rule empty), app1 goes off the readers and the other two stay served. A copy
    of app1 then runs to its end and is not served: the verify finds the value kg cannot hold."""
    p, org = prov(pg_db), new_org()
    await _refused_move(p, pg_db, org, caplog, {"connectorId": "app1"})
    f1 = (await held(p, org))["records"]["f1"]
    assert (f1[1], f1[4], f1[10]) == ("app1", "Moved", None)
    assert await _modes(p, org) == {"app1": "legacy", "app2": "enforce", "app3": "enforce"}
    assert not (await bf.backfill(pg_db, org, "app1", page=2)).flipped


async def _granted(p, db, org: str, gtype: str, role: str) -> None:
    """Three connectors served, u1 linked to app2 and app3 but granted neither record; then, in one change that the drain projects in one unit, u1 is granted app2's x2 with
    ``gtype`` and ``role``, and app3's x3 as a reader."""
    await load_world(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": a, "name": a, "type": "DRIVE", "orgId": org} for a in ("app2", "app3")], "apps")
        await p.batch_upsert_nodes([{"_key": f"x{n}", "recordName": f"X{n}", "connectorId": f"app{n}", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for n in (2, 3)], "records")
        await p.batch_create_edges([edge("u1", "users", "app2", "apps"), edge("u1", "users", "app3", "apps")], "userAppRelation")
    assert await flip(db, org) == (3, 3)
    assert "x2" not in await _ask(p, org, "u1", ["x2"], await _access(p, org), "0")
    with ctx(org):
        await p.batch_create_edges([edge("u1", "users", "x2", "records", type=gtype, role=role), edge("u1", "users", "x3", "records", type="USER", role="READER")], "permission")
    await drain_org(db, org)
    assert await _log_rows(p, org) == 0                                                       # the drain went through to the end of the log
    assert (await held(p, org))["grants"][("u1", "x3", "USER")] == ("READER", "app3", True)    # and the other connector's grant of the same unit is written


async def _access(p, org: str) -> dict:
    with ctx(org):
        return await p.get_knowledge_hub_access_v3("u1", org)


async def _log_rows(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)
    with ctx(org):
        return await p._q(go, None)


WIDE = "".join(chr(0x4E00 + i) for i in range(100))      # 100 characters, 300 bytes


@pytest.mark.parametrize("role", ["R" * 64, "R" * 65, WIDE, "\U00020000" * 100_000], ids=["64", "65", "100-multibyte", "100000-astral"])
async def test_a_grant_with_a_role_of_any_length_is_copied_whole(pg_db, role) -> None:
    """The role is no key (kg_0012): a grant with a long custom role is written whole, its connector stays served, and the drain goes on."""
    p, org = prov(pg_db), new_org()
    await _granted(p, pg_db, org, "USER", role)
    assert (await held(p, org))["grants"][("u1", "x2", "USER")] == (role, "app2", True)
    assert await _modes(p, org) == {"app1": "enforce", "app2": "enforce", "app3": "enforce"}


@pytest.mark.parametrize("gtype", ["", "T" * 65, WIDE], ids=["empty", "65", "100-multibyte"])
async def test_a_grant_whose_type_the_writer_refuses_takes_only_its_connector_to_the_walk(pg_db, caplog, gtype) -> None:
    """The type is part of the grant's key and keeps its bound. A grant whose type is refused is left out and logged, the rest of the unit and of the drain is written, and
    the connector of the node it is on goes off the readers: the access check answers x2 with the walk, which sees the grant. A copy of that connector is not served while the
    type stays (the verify finds the grant missing), and is once the type is fixed."""
    p, org = prov(pg_db), new_org()
    with caplog.at_level(logging.ERROR, logger="app.services.graph_db.postgres.kg.project"):
        await _granted(p, pg_db, org, gtype, "READER")
    assert any(f"refuses the type ({len(gtype)} characters)" in r.getMessage() for r in caplog.records)
    assert not any(k[:2] == ("u1", "x2") for k in (await held(p, org))["grants"])
    assert await _modes(p, org) == {"app1": "enforce", "app2": "legacy", "app3": "enforce"}
    access = await _access(p, org)
    assert "x2" in (await _decide(pg_db, org, ["x2", "x3"], access)).walk
    assert {"x2", "x3"} <= set(await _ask(p, org, "u1", ["x2", "x3"], access, None))
    assert not (await bf.backfill(pg_db, org, "app2", page=2)).flipped
    with ctx(org):
        await p.batch_create_edges([edge("u1", "users", "x2", "records", type="USER", role="READER")], "permission")
    await drain_org(pg_db, org)
    assert (await bf.backfill(pg_db, org, "app2", page=2)).flipped


async def test_the_other_organisation_is_not_touched(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    await load_world(p, a)
    await load_world(p, b)
    await run(p, a, await everything(p, a))
    assert (await held(p, b))["records"] == {}


def test_the_nodes_a_change_log_row_names_decode_and_what_does_not_decode_is_skipped() -> None:
    rows = [{"ends": ["3:r1", "4:u:with:colons", "9:nope", "x:y", "3:", 7]}, {"ends": ["1:app"]}]
    assert decode(rows) == {("records", "r1"), ("users", "u:with:colons"), ("apps", "app")}
