"""The projection's pieces, put together by hand: a small world is written to the graph through the provider, read back by ``kg.reader``, derived by
``kg.model`` and written by ``kg.writers``; what the typed tables then hold is compared with what the test says they must hold, written out from the
world and not from the code under test."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg import reader
from app.services.graph_db.postgres.kg.model import (
    LINK_GROUP,
    LINK_HIERARCHY,
    UAR,
    derive,
)
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

pytestmark = pytest.mark.integration


def prov(pg_db) -> PostgresGraphProvider:
    return PostgresGraphProvider(logging.getLogger("kg-derivation"), pg_db)


def ctx(org: str) -> AbstractContextManager:
    return bind(OrgContext(org, None, "job", "job:kg-test", "t"))


def edge(frm: str, fc: str, to: str, tc: str, **props: object) -> dict:
    return {"from_id": frm, "from_collection": fc, "to_id": to, "to_collection": tc, **props}


async def load_world(p: PostgresGraphProvider, org: str) -> None:
    """An app with a record group, a folder with two files in it (one of them also in a second group), a user in a team and the organisation, and the grants."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "Shared", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"},
                                    {"_key": "g2", "groupName": "Other", "connectorId": "app1", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([
            {"_key": "fold", "recordName": "Folder", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "externalRecordId": "x-fold",
             "mimeType": "application/vnd.folder", "createdAtTimestamp": 5, "updatedAtTimestamp": 7},
            {"_key": "f1", "recordName": "One.TXT", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "virtualRecordId": "vr1",
             "sourceLastModifiedTimestamp": 40, "sourceCreatedAtTimestamp": 30, "sizeInBytes": 12, "isDeleted": True},
            {"_key": "f2", "recordName": "Two", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "UPLOAD", "isPlaceholder": True},
        ], "records")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_upsert_nodes([{"_key": "team1", "orgId": org}], "teams")
        await p.batch_upsert_nodes([{"_key": org, "name": "Org"}], "organizations")
        await p.batch_create_edges([edge("fold", "records", "f1", "records", relationshipType="PARENT_CHILD"),
                                    edge("fold", "records", "f2", "records", relationshipType="ATTACHMENT"),
                                    edge("g1", "recordGroups", "f2", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("f1", "records", "g1", "recordGroups"), edge("f1", "records", "g2", "recordGroups"), edge("fold", "records", "g1", "recordGroups"),
                                    edge("u1", "users", org, "organizations")], "belongsTo")
        await p.batch_create_edges([edge("u1", "users", "f1", "records", type="USER", role="READER"),
                                    edge("u1", "users", "team1", "teams", type="USER"),
                                    edge("team1", "teams", "g1", "recordGroups", type="TEAM", role="WRITER")], "permission")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")


FOCUS = {("apps", "app1"), ("recordGroups", "g1"), ("recordGroups", "g2"), ("records", "fold"), ("records", "f1"), ("records", "f2"),
         ("users", "u1"), ("teams", "team1")}


async def project(p: PostgresGraphProvider, org: str, focus: set) -> None:
    """The write of one projection: allocate the keys, read the graph around ``focus``, derive, write the typed rows."""
    async def go(tx) -> None:
        w = KgWriter(tx)
        snap = await reader.snapshot(tx, org, focus, w.nodes_upsert)
        d = derive(focus, snap)
        await w.records_upsert([{"sk": sk, **row} for sk, row in sorted(d.records.items())])
        await w.records_delete(sorted(d.gone))
        await w.links_set(sorted(d.links))
        await w.grants_set([(pr, nd, g, role, app, leaf) for (pr, nd, g), (role, app, leaf) in sorted(d.grants.items())])
        await w.closure_set([(u, pr, app, via) for (u, pr, app), via in sorted(d.closure.items())])
    with ctx(org):
        await p._q(go, None, write=True)


async def held(p: PostgresGraphProvider, org: str) -> dict:
    """What the typed tables hold for ``org``, with the surrogate keys turned back into node ids."""
    async def go(tx) -> dict:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        recs = {r["id"]: (r["kind"], ids.get(r["app_sk"]), ids.get(r["group_sk"]), ids.get(r["parent_sk"]), r["name"], r["name_key"], r["updated_ms"], r["created_ms"],
                          r["size_bytes"], r["vrid"], r["rule"], r["model"], r["origin"], r["deleted"], r["placeholder"], r["granted"])
                for r in await tx.fetch("SELECT * FROM kg.records WHERE org_id = $1", org)}
        links = {(ids[r["parent_sk"]], r["kind"], ids[r["child_sk"]]) for r in await tx.fetch("SELECT * FROM kg.links WHERE org_id = $1", org)}
        grants = {(ids[r["principal_sk"]], ids[r["node_sk"]], r["gtype"]): (r["role"], ids.get(r["app_sk"]), r["leaf"])
                  for r in await tx.fetch("SELECT * FROM kg.scope_members WHERE org_id = $1", org)}
        closure = {(ids[r["user_sk"]], ids[r["principal_sk"]], ids.get(r["app_sk"], "-")): r["via"] for r in await tx.fetch("SELECT * FROM kg.principal_closure WHERE org_id = $1", org)}
        return {"records": recs, "links": links, "grants": grants, "closure": closure}
    with ctx(org):
        return await p._q(go, None)


EXPECTED = {
    "records": {
        # kind, app, group, parent, name, name_key, updated, created, size, vrid, rule, model, origin, deleted, placeholder, granted
        "app1": (1, "app1", None, None, "Drive", "drive", 0, 0, None, None, None, None, None, False, False, False),
        "g1": (2, "app1", None, None, "Shared", "shared", 0, 0, None, None, "OPEN", "RECORD_LEVEL", None, False, False, True),
        "g2": (2, "app1", None, None, "Other", "other", 0, 0, None, None, None, None, None, False, False, False),
        "fold": (3, "app1", "g1", None, "Folder", "folder", 7, 5, None, None, None, None, "CONNECTOR", False, False, False),
        "f1": (3, "app1", "g1", "fold", "One.TXT", "one.txt", 40, 30, 12, "vr1", None, None, "CONNECTOR", True, False, True),
        "f2": (3, "app1", None, "g1", "Two", "two", 0, 0, None, None, None, None, "UPLOAD", False, True, False),
    },
    # f1 is also in g2 (its second group); f2 has the folder as a second hierarchy parent (an attachment) after the group
    "links": {("g2", LINK_GROUP, "f1"), ("fold", LINK_HIERARCHY, "f2")},
    # (principal, node, type) -> (role, the node's app, the node is a leaf record); a membership of a team is a closure row below, not a grant on a node
    "grants": {("u1", "f1", "USER"): ("READER", "app1", True), ("team1", "g1", "TEAM"): ("WRITER", "app1", False), ("u1", "app1", UAR): (None, "app1", False)},
    "closure": {("u1", "u1", "-"): "self", ("u1", "team1", "-"): "membership", ("u1", "org", "-"): "org"},
}


def expected_for(org: str) -> dict:
    """The organisation node of the world is keyed by the organisation's own id."""
    return {"records": dict(EXPECTED["records"]), "links": set(EXPECTED["links"]), "grants": dict(EXPECTED["grants"]),
            "closure": {(u, org if pr == "org" else pr, app): via for (u, pr, app), via in EXPECTED["closure"].items()}}


async def test_a_world_is_projected_into_the_typed_tables_as_it_is_described(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await load_world(p, org)
    await project(p, org, FOCUS)
    got, want = await held(p, org), expected_for(org)
    assert got["records"] == want["records"]
    assert got["links"] == want["links"]
    assert got["grants"] == want["grants"]
    assert got["closure"] == want["closure"]


async def test_projecting_again_changes_nothing_and_another_organisations_rows_are_not_touched(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    for org in (a, b):
        await load_world(p, org)
    await project(p, a, FOCUS)
    first = await held(p, a)
    assert await held(p, b) == {"records": {}, "links": set(), "grants": {}, "closure": {}}        # organisation b has the same ids and nothing projected
    await project(p, a, FOCUS)
    assert await held(p, a) == first
    await project(p, b, FOCUS)
    assert (await held(p, b))["records"] == expected_for(b)["records"] and await held(p, a) == first


async def test_a_node_that_is_gone_loses_its_row(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await load_world(p, org)
    await project(p, org, FOCUS)
    with ctx(org):
        await p.delete_nodes(["f2"], "records")
    await project(p, org, {("records", "f2")})
    got = await held(p, org)
    assert "f2" not in got["records"] and set(got["records"]) == set(EXPECTED["records"]) - {"f2"}
