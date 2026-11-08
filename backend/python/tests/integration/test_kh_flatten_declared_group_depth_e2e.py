"""A depth-bounded flatten from the App lists what a RECORD_GROUP_LEVEL group holds.

The walk from the App stops at a group whose permission model is
RECORD_GROUP_LEVEL; what it holds is listed by the declared-group arms. Depth is
the number of hierarchy hops from the App (App -> group is 1). A record the
hierarchy places under the group counts by its hops; one that only BELONGS_TO
the group counts one below it. Both backends must list the same nodes.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.integration.test_tool_bounds_neo4j_e2e import _rec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kh_flatten_declared_group_it"
logger = logging.getLogger("kh-flatten-declared-group-it")
RG, REC, USR, APP = (CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value,
                     CollectionNames.USERS.value, CollectionNames.APPS.value)

# Hops from the App: the group is 1; placed and folder are placed under the
# group (2); deep is under folder (3); member only BELONGS_TO the group (2).
DEPTH = {"group": 1, "placed": 2, "folder": 2, "member": 2, "deep": 3}


async def _remove(w: SimpleNamespace) -> None:
    ids = [*w.ids.values(), w.app, w.user]
    if isinstance(w.g, Neo4jProvider):
        await w.g.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids OR n.connectorId = $app DETACH DELETE n",
            parameters={"ids": ids, "app": w.app},
        )
        return
    for collection in (REC, RG, CollectionNames.FILES.value, USR, APP):
        await w.g.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids})
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.USER_APP_RELATION.value,
                  CollectionNames.NODE_RELATIONS.value):
        await w.g.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}", {"ids": ids})


@pytest.fixture(params=["neo4j", "arango"])
async def w(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            g = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                       else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(g, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:8]
        w = SimpleNamespace(g=g, org=f"org-{run}", app=f"app-{run}", user=f"user-{run}",
                            ids={name: f"{name}-{run}" for name in DEPTH})
        cleanup.push_async_callback(_remove, w)
        now = get_epoch_timestamp_in_ms()
        st = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
        await g.batch_upsert_nodes([{"id": w.user, "userId": f"uid-{w.user}", "orgId": w.org,
                                     "email": f"{w.user}@x.com", "fullName": "u", "isActive": True, **st}],
                                   collection=USR)
        await g.batch_upsert_nodes([{"id": w.app, "name": "Drive", "type": Connectors.GOOGLE_DRIVE.value,
                                     "appGroup": "Google Workspace", "scope": "team", "isActive": True,
                                     "orgId": w.org, **st}], collection=APP)
        group = w.ids["group"]
        await g.batch_upsert_nodes([{"id": group, "orgId": w.org, "groupName": "group",
                                     "externalGroupId": f"e-{group}", "groupType": "DRIVE",
                                     "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": w.app,
                                     "permissionModel": "RECORD_GROUP_LEVEL",
                                     "sourceCreatedAtTimestamp": now, "sourceLastModifiedTimestamp": now, **st}],
                                   collection=RG)
        await g.batch_upsert_records([
            _rec(w, w.ids[name], folder=name == "folder") for name in ("placed", "folder", "member", "deep")])

        def e(a: str, ac: str, b: str, bc: str, **x: object) -> dict[str, Any]:
            return {"from_id": a, "from_collection": ac, "to_id": b, "to_collection": bc, **st, **x}

        assert await g.batch_create_edges(
            [e(group, RG, w.app, APP)]
            + [e(w.ids[name], REC, group, RG) for name in ("placed", "folder", "member", "deep")],
            collection=CollectionNames.BELONGS_TO.value)
        assert await g.batch_create_edges([
            e(w.app, APP, group, RG, relationshipType="PARENT_CHILD"),
            e(group, RG, w.ids["placed"], REC, relationshipType="PARENT_CHILD"),
            e(group, RG, w.ids["folder"], REC, relationshipType="PARENT_CHILD"),
            e(w.ids["folder"], REC, w.ids["deep"], REC, relationshipType="PARENT_CHILD"),
        ], collection=CollectionNames.NODE_RELATIONS.value)
        # The grant is on the group alone: its records are readable through it.
        assert await g.batch_create_edges([e(w.user, USR, group, RG, role="READER", type="USER")],
                                          collection=CollectionNames.PERMISSION.value)
        assert await g.batch_create_edges(
            [e(w.user, USR, w.app, APP, syncState="COMPLETED", lastSyncUpdate=now, isExternalUser=False)],
            collection=CollectionNames.USER_APP_RELATION.value)
        yield w


async def _listed(w: SimpleNamespace, depth: int) -> set[str]:
    r = await KnowledgeHubService(logger, w.g).get_nodes(
        user_id=f"uid-{w.user}", org_id=w.org, parent_id=w.app, parent_type="app",
        flattened=True, depth=depth, limit=100, sort_by="name", sort_order="asc")
    assert r.success, r.error
    names = {v: k for k, v in w.ids.items()}
    return {names.get(i.id, i.id) for i in r.items}


@pytest.mark.parametrize("depth", [1, 2, 3])
async def test_a_bounded_flatten_lists_the_declared_groups_records_within_the_depth(w: SimpleNamespace, depth: int) -> None:
    assert await _listed(w, depth) == {name for name, hops in DEPTH.items() if hops <= depth}


async def test_a_hierarchy_edge_from_a_removed_parent_does_not_hide_a_member(w: SimpleNamespace) -> None:
    """Arango can keep an edge whose parent document was removed (Neo4j cannot);
    no walk crosses it, so it must not count as placing the member."""
    if not isinstance(w.g, Neo4jProvider):
        now = get_epoch_timestamp_in_ms()
        await w.g.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @t, relationshipType: 'PARENT_CHILD', createdAtTimestamp: @now,"
            f" updatedAtTimestamp: @now}} INTO {CollectionNames.NODE_RELATIONS.value}",
            {"f": f"{REC}/gone-{w.ids['member']}", "t": f"{REC}/{w.ids['member']}", "now": now})
    assert await _listed(w, 2) == {"group", "placed", "folder", "member"}
