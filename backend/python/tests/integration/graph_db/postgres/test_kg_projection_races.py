"""The projection under the review of the first cut: what a change reaches in other connectors is locked before it is written, an app that goes and comes back, an app whose
organisation changes and a record that moves to another connector leave kg as a fresh projection would, and a seeded run of random changes ends where a fresh projection does."""
from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.postgres.kg import project as project_mod
from app.services.graph_db.postgres.kg.locks import lock_apps

from .pg_support import new_org
from .test_kg_derivation import FOCUS, ctx, edge, held, load_world, prov
from .test_kg_projection import (
    add_edge,
    delete_edge,
    delete_node,
    run,
    same,
    update_node,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]       # a regression that blocks fails in a minute, not at the runner's limit


async def world2(p, org: str) -> set:
    """The world, a second connector with a record and a group, and a source account with a badge group for the user in the first one."""
    await load_world(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app2", "name": "Other", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g3", "groupName": "Third", "connectorId": "app2", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": "x1", "recordName": "X", "connectorId": "app2", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_upsert_nodes([{"_key": "acct", "orgId": org}, {"_key": "grp", "orgId": org}], "users")
        await p.batch_upsert_nodes([{"_key": "grp", "orgId": org}], "groups")
        await p.batch_create_edges([edge("u1", "users", "acct", "users", connectorId="app1"), edge("u1", "users", "acct", "users", connectorId="app2")], "authenticatedAs")
        await p.batch_create_edges([edge("acct", "users", "grp", "groups", type="USER")], "permission")
    return {*FOCUS, ("apps", "app2"), ("recordGroups", "g3"), ("records", "x1"), ("users", "acct"), ("groups", "grp")}


async def everything2(p, org: str) -> set:
    return {*await world2_keys(), ("organizations", org)}


async def world2_keys() -> set:
    return {*FOCUS, ("apps", "app2"), ("recordGroups", "g3"), ("records", "x1"), ("users", "acct"), ("groups", "grp")}


async def agrees(pg_db, *changes) -> None:
    """Apply ``changes`` one by one to an organisation that is projected after each, and to another that is projected once at the end: the typed rows are equal."""
    p, a, b = prov(pg_db), new_org(), new_org()
    await world2(p, a)
    await run(p, a, await everything2(p, a))
    await world2(p, b)
    for change in changes:
        await run(p, a, await change(p, a))
        await change(p, b)
    await run(p, b, await everything2(p, b))
    assert same(await held(p, a), a, await held(p, b), b)


def move(coll: str, nid: str, app: str) -> Callable:
    return lambda p, o: update_node(p, o, coll, nid, connectorId=app)


async def test_a_unit_that_reaches_into_another_connector_locks_it_and_waits_for_the_unit_holding_it(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await run(p, org, await everything2(p, org))
    touched = await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")   # a parent in the first connector, a child in the second
    paused, release = asyncio.Event(), asyncio.Event()
    real = project_mod.current

    async def slow(tx, o, d) -> tuple:
        if not paused.is_set():
            paused.set()
            await asyncio.wait_for(release.wait(), 30)
        return await real(tx, o, d)
    project_mod.current = slow
    try:
        first = asyncio.create_task(asyncio.wait_for(run(p, org, touched), 30))
        await asyncio.wait_for(paused.wait(), 15)
        second = asyncio.create_task(run(p, org, {("records", "f2")}))      # the parent's own connector: the first unit rewrites the parent's flags, so it holds this lock too
        await asyncio.sleep(0.5)
        assert not second.done()
        release.set()
        await asyncio.wait_for(first, 20)
        await asyncio.wait_for(second, 20)
    finally:
        project_mod.current = real
    fresh, b = prov(pg_db), new_org()
    await world2(fresh, b)
    await add_edge(fresh, b, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")
    await run(fresh, b, await everything2(fresh, b))
    assert same(await held(p, org), org, await held(fresh, b), b)


async def test_an_empty_set_of_connectors_to_lock_takes_the_whole_organisation(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    opened, release = asyncio.Event(), asyncio.Event()

    async def holder(tx) -> None:
        await lock_apps(tx, org, set(), org_wide=False)
        opened.set()
        await asyncio.wait_for(release.wait(), 30)

    async def other(tx) -> None:
        await lock_apps(tx, org, {"app1"}, org_wide=False)

    async def hold() -> None:
        with ctx(org):
            await p._q(holder, None, write=True)

    async def take() -> None:
        with ctx(org):
            await p._q(other, None, write=True)
    task = asyncio.create_task(hold())
    await asyncio.wait_for(opened.wait(), 15)
    waiting = asyncio.create_task(take())
    await asyncio.sleep(0.5)
    assert not waiting.done()                       # the shared key of a connector's unit waits behind an exclusive one
    release.set()
    await task
    await asyncio.wait_for(waiting, 20)


async def test_a_connector_that_is_deleted_and_added_again_gives_its_records_their_app_back(pg_db) -> None:
    async def readd(p, o) -> set:
        return await update_node(p, o, "apps", "app1", name="Drive", type="DRIVE", orgId=o)
    await agrees(pg_db, lambda p, o: delete_node(p, o, "apps", "app1"), readd)


async def test_a_connector_that_is_deleted_drops_the_links_through_its_accounts(pg_db) -> None:
    await agrees(pg_db, lambda p, o: delete_node(p, o, "apps", "app1"))


async def test_a_connector_that_changes_organisation_drops_the_links_through_its_accounts(pg_db) -> None:
    await agrees(pg_db, lambda p, o: update_node(p, o, "apps", "app1", orgId="someone-else"))
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await run(p, org, await everything2(p, org))
    await update_node(p, org, "apps", "app1", orgId="someone-else")
    await run(p, org, {("apps", "app1")})
    assert not [k for k in (await held(p, org))["closure"] if k[2] == "app1"]


async def test_a_record_that_moves_to_another_connector_re_derives_its_children_and_marks_the_connector(pg_db) -> None:
    await agrees(pg_db, move("records", "fold", "app2"))
    await agrees(pg_db, move("recordGroups", "g1", "app2"))
    await agrees(pg_db, move("records", "x1", "app1"), move("records", "x1", "app2"))


async def test_the_parents_a_parent_gains_or_drops_when_it_is_re_derived_are_re_derived_too(pg_db) -> None:
    """gp -> p -> c with a grant on gp: both edges go with nothing projecting p, then c is projected: gp has lost its child, and its grant's `leaf` flag says so, as in a fresh projection."""
    p, a, b = prov(pg_db), new_org(), new_org()
    for o in (a, b):
        await load_world(p, o)
        with ctx(o):
            await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": o, "recordType": "FILE", "origin": "CONNECTOR"} for k in ("gp", "pa", "ch")], "records")
        await add_edge(p, o, ("records", "gp"), ("records", "pa"), "nodeRelations", relationshipType="PARENT_CHILD")
        await add_edge(p, o, ("records", "pa"), ("records", "ch"), "nodeRelations", relationshipType="PARENT_CHILD")
        await add_edge(p, o, ("users", "u1"), ("records", "gp"), "permission", type="USER", role="READER")
    await run(p, a, {*FOCUS, ("organizations", a), ("records", "gp"), ("records", "pa"), ("records", "ch")})
    for o in (a, b):
        await delete_edge(p, o, ("records", "gp"), ("records", "pa"), "nodeRelations", relationshipType="PARENT_CHILD")
        await delete_edge(p, o, ("records", "pa"), ("records", "ch"), "nodeRelations", relationshipType="PARENT_CHILD")
    await run(p, a, {("records", "ch")})
    await run(p, b, {*FOCUS, ("organizations", b), ("records", "gp"), ("records", "pa"), ("records", "ch")})

    def grants(h: dict) -> dict:
        return {k: v for k, v in h["grants"].items() if k[1] == "gp"}
    got = grants(await held(p, a))
    assert got and got == grants(await held(p, b))


OPS = (
    lambda p, o: delete_node(p, o, "apps", "app1"),
    lambda p, o: update_node(p, o, "apps", "app1", name="Drive", type="DRIVE", orgId=o),
    lambda p, o: delete_node(p, o, "apps", "app2"),
    lambda p, o: update_node(p, o, "apps", "app2", name="Other", type="DRIVE", orgId=o),
    lambda p, o: update_node(p, o, "apps", "app1", orgId="someone-else"),
    move("records", "f1", "app2"), move("records", "f1", "app1"), move("records", "fold", "app2"), move("records", "fold", "app1"),
    move("recordGroups", "g2", "app2"), move("recordGroups", "g2", "app1"), move("records", "x1", "app1"), move("records", "x1", "app2"),
    lambda p, o: add_edge(p, o, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    lambda p, o: delete_edge(p, o, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    lambda p, o: add_edge(p, o, ("recordGroups", "g3"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    lambda p, o: delete_edge(p, o, ("recordGroups", "g3"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    lambda p, o: delete_edge(p, o, ("records", "fold"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    lambda p, o: add_edge(p, o, ("records", "fold"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD"),
    lambda p, o: delete_node(p, o, "records", "fold"),
    lambda p, o: update_node(p, o, "records", "fold", recordName="Folder", connectorId="app1", orgId=o, recordType="FILE", origin="CONNECTOR"),
    lambda p, o: delete_edge(p, o, ("users", "u1"), ("records", "f1"), "permission", type="USER"),
    lambda p, o: add_edge(p, o, ("users", "u1"), ("records", "f1"), "permission", type="USER", role="READER"),
    lambda p, o: delete_edge(p, o, ("users", "u1"), ("users", "acct"), "authenticatedAs", connectorId="app1"),
    lambda p, o: add_edge(p, o, ("users", "u1"), ("users", "acct"), "authenticatedAs", connectorId="app1"),
    lambda p, o: delete_node(p, o, "recordGroups", "g1"),
    lambda p, o: update_node(p, o, "recordGroups", "g1", groupName="Shared", connectorId="app1", orgId=o),
)


async def everything(p, org: str) -> set:
    return {*FOCUS, ("organizations", org)}


@pytest.mark.parametrize("seed", range(100))
async def test_random_changes_each_projected_end_where_a_fresh_projection_does(pg_db, seed: int) -> None:
    rng = random.Random(seed)
    await agrees(pg_db, *[rng.choice(OPS) for _ in range(rng.randint(2, 6))])
