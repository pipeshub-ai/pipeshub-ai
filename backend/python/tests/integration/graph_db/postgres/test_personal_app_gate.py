"""A personal, app-level connector's records are visible to its connecting user only, on PostgreSQL, against a real database.

A personal connector (Slack individual and the like) writes records that carry no grants and inherit the app. The only thing that opens them is
the connecting user's ``userAppRelation`` to the app. So another member of the org, an org admin, a teammate and a user of another org must see
none of them, whichever read asks: the batch access check, the id filters, the gate reads and the knowledge hub node read.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.core import org_context
from app.core.org_context import OrgContext, bind

from .test_access import World

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = pytest.mark.integration

PERS, OTHER = "slack-personal", "drive-team"
MINE = ["r-app", "r-group", "r-child"]            # directly under the app, in a group, under a record
VRIDS = {"r-app": "v-app", "r-group": "v-group", "r-child": "v-child"}
ALL_NODES = [*MINE, "g1", PERS]


def _org() -> str:
    return uuid.uuid4().hex[:24]


@contextlib.contextmanager
def other_org(org: str | None = None) -> Iterator[None]:
    token = org_context._ctx.set(None)
    try:
        with bind(OrgContext(org or _org(), None, "job", "job:test", "t")):
            yield
    finally:
        org_context._ctx.reset(token)


@pytest.fixture
async def w(pg_provider, pg_db, org_ctx) -> AsyncIterator[World]:
    org = _org()
    with org_ctx(org):
        world = World(pg_provider, org)
        await build(world)
        yield world
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def build(w: World) -> None:
    await w.nodes("organizations", {"_key": "org", "name": "Org", "accountType": "enterprise", "isActive": True})
    for key, extra in (("A", {}), ("B", {}), ("C", {"role": "ADMIN", "orgRole": "ADMIN"})):         # C is an org admin: the user node says so
        await w.nodes("users", {"_key": key, "userId": f"uid-{key}", "email": f"{key}@x.io", "fullName": key, "isActive": True, **extra})
        await w.edge("belongsTo", ("users", key), ("organizations", "org"), entityType="ORGANIZATION")
    await w.nodes("teams", {"_key": "team", "name": "Everyone"})
    for key in "ABC":
        await w.edge("permission", ("users", key), ("teams", "team"), type="USER", role="READER")
    # the personal app: app-level, no grants on it or its records; only A is connected to it
    await w.nodes("apps", {"_key": PERS, "name": "Slack", "type": "SLACK", "scope": "personal", "permissionModel": "APP_LEVEL", "isActive": True},
                  {"_key": OTHER, "name": "Drive", "type": "DRIVE", "scope": "team", "permissionModel": "RECORD_GROUP_LEVEL", "isActive": True})
    await w.edge("userAppRelation", ("users", "A"), ("apps", PERS), syncState="COMPLETED", lastSyncUpdate=1)
    await w.nodes("recordGroups", {"_key": "g1", "groupName": "channel", "connectorId": PERS, "accessRule": "OPEN"})
    for key in MINE:
        await w.nodes("records", {"_key": key, "recordName": key, "connectorId": PERS, "virtualRecordId": VRIDS[key], "recordType": "FILE",
                                  "indexingStatus": "COMPLETED", "origin": "CONNECTOR", "mimeType": "text/plain"})
    for parent, child in ((("apps", PERS), ("records", "r-app")), (("apps", PERS), ("recordGroups", "g1")), (("recordGroups", "g1"), ("records", "r-group")),
                          (("records", "r-app"), ("records", "r-child"))):
        await w.edge("nodeRelations", parent, child, relationshipType="PARENT_CHILD")
    await w.edge("belongsTo", ("recordGroups", "g1"), ("apps", PERS))
    await w.edge("belongsTo", ("records", "r-group"), ("recordGroups", "g1"))
    # another app every member of the org may use: an organization-wide grant on the app and on one of its records, and a team edge on the app
    await w.nodes("recordGroups", {"_key": "g2", "groupName": "shared", "connectorId": OTHER, "accessRule": "OPEN"})
    await w.nodes("records", {"_key": "o1", "recordName": "o1", "connectorId": OTHER, "virtualRecordId": "v-o1", "recordType": "FILE", "indexingStatus": "COMPLETED"})
    await w.edge("nodeRelations", ("apps", OTHER), ("records", "o1"), relationshipType="PARENT_CHILD")
    await w.edge("permission", ("organizations", "org"), ("apps", OTHER), type="ORG", role="READER")
    await w.edge("permission", ("organizations", "org"), ("records", "o1"), type="ORG", role="READER")
    await w.edge("permission", ("teams", "team"), ("apps", OTHER), type="TEAM", role="READER")


async def view(w: World, key: str) -> dict[str, Any]:
    """What one user reaches of the personal app, asked every way the graph provider answers it."""
    p, uid, org = w.p, f"uid-{key}", w.org
    nodes = await p.check_access(key, org, node_ids=ALL_NODES, virtual_record_ids=list(VRIDS.values()))
    scoped = await p.check_access(key, org, virtual_record_ids=list(VRIDS.values()), connector_ids=frozenset({PERS}))
    elsewhere = await p.check_access(key, org, virtual_record_ids=list(VRIDS.values()), connector_ids=frozenset({OTHER}))
    v3 = await p.get_knowledge_hub_access_v3(key, org)
    return {
        "filter_ids": await p.filter_accessible_record_ids(MINE, uid, org),
        "nodes": nodes.node_ids,
        "vrids": {v for v, r in nodes.records_by_vrid.items() if r},
        "vrids_scoped_to_the_app": {v for v, r in scoped.records_by_vrid.items() if r},
        "vrids_scoped_elsewhere": {v for v, r in elsewhere.records_by_vrid.items() if r},
        "filter_vrids": set(await p.filter_accessible_virtual_record_ids(list(VRIDS.values()), uid, org)),
        "filter_vrids_scoped": set(await p.filter_accessible_virtual_record_ids(list(VRIDS.values()), uid, org, scope_connector_ids=frozenset({PERS}))),
        "gated": PERS in v3["gated_app_ids"],
        "gated_apps": PERS in {a["id"] for a in await p.get_gated_apps(key, org)},
        "user_apps": PERS in await p.get_user_app_ids(key),
        "connector_types": "SLACK" in await p.get_accessible_connector_types(uid, org),
        "node_access": {n for n in ALL_NODES if await p.get_knowledge_hub_node_access(n, key, org, FOLDER_MIME_TYPES)},
        "control": await p.filter_accessible_record_ids(["o1"], uid, org),     # the other app's org-wide record is everyone's
    }


NOTHING = {"filter_ids": set(), "nodes": set(), "vrids": set(), "vrids_scoped_to_the_app": set(), "vrids_scoped_elsewhere": set(), "filter_vrids": set(),
           "filter_vrids_scoped": set(), "gated": False, "gated_apps": False, "user_apps": False, "connector_types": False, "node_access": set(), "control": {"o1"}}
EVERYTHING = {"filter_ids": set(MINE), "nodes": {*MINE, "g1", PERS}, "vrids": set(VRIDS.values()), "vrids_scoped_to_the_app": set(VRIDS.values()),
              "vrids_scoped_elsewhere": set(), "filter_vrids": set(VRIDS.values()), "filter_vrids_scoped": set(VRIDS.values()), "gated": True, "gated_apps": True,
              "user_apps": True, "connector_types": True, "node_access": {*MINE, "g1", PERS}, "control": {"o1"}}


async def test_only_the_connecting_user_reaches_a_personal_apps_records(w: World) -> None:
    assert await view(w, "A") == EVERYTHING                       # the connecting user: the app, its group and all three records, by every read
    assert await view(w, "B") == NOTHING                          # a plain member of the org, in the same team
    assert await view(w, "C") == NOTHING                          # an org admin (the user node says ADMIN): the app gate reads edges, not roles


async def test_a_user_of_another_org_reaches_nothing_even_with_the_same_ids(w: World, pg_db) -> None:
    other = _org()
    ow = World(w.p, other)
    try:
        with other_org(other):                                    # the other org has the same user key, user id, app id and a connection to the app, but no records
            await ow.nodes("organizations", {"_key": "org", "isActive": True})
            await ow.nodes("users", {"_key": "A", "userId": "uid-A", "email": "A@x.io", "isActive": True})
            await ow.edge("belongsTo", ("users", "A"), ("organizations", "org"), entityType="ORGANIZATION")
            await ow.nodes("apps", {"_key": PERS, "name": "Slack", "type": "SLACK", "scope": "personal", "permissionModel": "APP_LEVEL", "isActive": True})
            await ow.edge("userAppRelation", ("users", "A"), ("apps", PERS), syncState="COMPLETED", lastSyncUpdate=1)
            theirs = await view(ow, "A")
            assert theirs["filter_ids"] == set() and theirs["vrids"] == set() and theirs["nodes"] == {PERS}      # only their own app node: none of this org's records
            assert await w.p.get_knowledge_hub_access_v3("A", w.org) == {"grantee_ids": [], "gated_app_ids": [], "by_connector": {}}     # asked about this org
            assert await w.p.filter_accessible_record_ids(MINE, "uid-A", w.org) == set()
            assert await w.p.check_access("A", w.org, node_ids=ALL_NODES, virtual_record_ids=list(VRIDS.values())) == await w.p.check_access("nobody", w.org, node_ids=ALL_NODES)
        assert await view(w, "A") == EVERYTHING                   # this org's A is not changed by the other org's A
        ghost = await w.p.filter_accessible_record_ids(MINE, "uid-nobody", w.org)
        assert ghost == set()                                     # a user that is no node of this org
    finally:
        with other_org(other):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", other), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", other), write=True)


async def test_revoking_the_connection_takes_the_records_away_and_restoring_it_brings_them_back(w: World) -> None:
    assert await view(w, "A") == EVERYTHING
    assert await w.p.delete_edge("A", "users", PERS, "apps", "userAppRelation")
    assert await view(w, "A") == NOTHING                          # revoked: A is now like any other member (the control record stays)
    assert await view(w, "B") == NOTHING
    await w.edge("userAppRelation", ("users", "A"), ("apps", PERS), syncState="COMPLETED", lastSyncUpdate=2)
    assert await view(w, "A") == EVERYTHING                       # restored: all three again, and nothing changed for the others
    assert await view(w, "B") == NOTHING and await view(w, "C") == NOTHING


async def test_a_teammate_connecting_later_opens_it_for_that_user_only(w: World) -> None:
    await w.edge("userAppRelation", ("users", "B"), ("apps", PERS), syncState="NOT_STARTED", lastSyncUpdate=1)
    assert await view(w, "B") == EVERYTHING                       # the next user to connect gets the app's records ...
    assert await view(w, "C") == NOTHING                          # ... and the admin, still not connected, does not
