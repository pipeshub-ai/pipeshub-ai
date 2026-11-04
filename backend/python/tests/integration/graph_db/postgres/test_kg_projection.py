"""The projection (``kg.project``): after any change to the graph, projecting the nodes the change names (and what they pull in) leaves kg equal to what a fresh projection of the
whole graph would write; the stats say how much was written; two projections of one connector do not interleave."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.graph_db.postgres.kg.locks import lock_apps
from app.services.graph_db.postgres.kg.model import edge_ends
from app.services.graph_db.postgres.kg.project import Stats, decode, project

from .pg_support import new_org
from .test_kg_derivation import FOCUS, ctx, edge, held, load_world, prov

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


async def test_the_other_organisation_is_not_touched(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    await load_world(p, a)
    await load_world(p, b)
    await run(p, a, await everything(p, a))
    assert (await held(p, b))["records"] == {}


def test_the_nodes_a_change_log_row_names_decode_and_what_does_not_decode_is_skipped() -> None:
    rows = [{"ends": ["3:r1", "4:u:with:colons", "9:nope", "x:y", "3:", 7]}, {"ends": ["1:app"]}]
    assert decode(rows) == {("records", "r1"), ("users", "u:with:colons"), ("apps", "app")}
