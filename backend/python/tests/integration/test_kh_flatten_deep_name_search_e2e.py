"""A name search in a flatten from the App honours a depth above 50.

The name-first listings walk a fixed 50 hops, so a flatten deeper than that must
take the full walk, or a match more than 50 hops below the App is missed. Depth
is the number of hierarchy hops from the App (App -> group is 1). Both backends
must list the same nodes.

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
from tests.integration.test_kh_flatten_declared_group_depth_e2e import _remove
from tests.integration.test_tool_bounds_neo4j_e2e import _rec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kh_flatten_deep_name_search_it"
logger = logging.getLogger("kh-flatten-deep-name-search-it")
RG, REC, USR, APP = (CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value,
                     CollectionNames.USERS.value, CollectionNames.APPS.value)
CHAIN = 51  # folders below the group: the last is 52 hops from the App
# Hops from the App: shallow-needle sits under the group, deep-needle under the last folder,
# so the deep match shares no folder with the shallow one.
NEEDLES = {"shallow-needle": 2, "deep-needle": CHAIN + 2}


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
        folders = [f"f{i}" for i in range(1, CHAIN + 1)]
        w = SimpleNamespace(g=g, org=f"org-{run}", app=f"app-{run}", user=f"user-{run}",
                            ids={name: f"{name}-{run}" for name in ["group", *folders, *NEEDLES]})
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
                                     "sourceCreatedAtTimestamp": now, "sourceLastModifiedTimestamp": now, **st}],
                                   collection=RG)
        records = [*folders, *NEEDLES]
        await g.batch_upsert_records([_rec(w, w.ids[name], folder=name in folders) for name in records])

        def e(a: str, ac: str, b: str, bc: str, **x: object) -> dict[str, Any]:
            return {"from_id": a, "from_collection": ac, "to_id": b, "to_collection": bc, **st, **x}

        chain = [group, *(w.ids[f] for f in folders)]
        hierarchy = [e(w.app, APP, group, RG, relationshipType="PARENT_CHILD")]
        hierarchy += [e(p, RG if p == group else REC, c, REC, relationshipType="PARENT_CHILD")
                      for p, c in zip(chain, chain[1:])]
        hierarchy += [e(group, RG, w.ids["shallow-needle"], REC, relationshipType="PARENT_CHILD"),
                      e(w.ids[folders[-1]], REC, w.ids["deep-needle"], REC, relationshipType="PARENT_CHILD")]
        assert await g.batch_create_edges(hierarchy, collection=CollectionNames.NODE_RELATIONS.value)
        assert await g.batch_create_edges(
            [e(group, RG, w.app, APP)] + [e(w.ids[name], REC, group, RG) for name in records],
            collection=CollectionNames.BELONGS_TO.value)
        # The grant is on the group alone: each record inherits it from its parent, so a
        # record is visible only through the walk down from the group.
        assert await g.batch_create_edges(
            [e(w.user, USR, group, RG, role="READER", type="USER")],
            collection=CollectionNames.PERMISSION.value)
        inherit = CollectionNames.INHERIT_PERMISSIONS.value
        assert await g.batch_create_edges(
            [e(x["to_id"], REC, x["from_id"], x["from_collection"]) for x in hierarchy[1:]], collection=inherit)
        if not isinstance(g, Neo4jProvider):
            cleanup.push_async_callback(lambda: g.http_client.execute_aql(
                f"FOR e IN {inherit} FILTER PARSE_IDENTIFIER(e._from).key IN @ids REMOVE e IN {inherit}",
                {"ids": list(w.ids.values())}))
        assert await g.batch_create_edges(
            [e(w.user, USR, w.app, APP, syncState="COMPLETED", lastSyncUpdate=now, isExternalUser=False)],
            collection=CollectionNames.USER_APP_RELATION.value)
        yield w


@pytest.mark.parametrize("depth", [3, None, 60])
async def test_a_name_search_lists_the_matches_within_the_depth(w: SimpleNamespace, depth: int | None) -> None:
    """No depth is the default walk of 50 hops; 60 reaches the match 53 hops down."""
    r = await KnowledgeHubService(logger, w.g).get_nodes(
        user_id=f"uid-{w.user}", org_id=w.org, parent_id=w.app, parent_type="app",
        flattened=True, depth=depth, q="needle", limit=100, sort_by="name", sort_order="asc")
    assert r.success, r.error
    names = {v: k for k, v in w.ids.items()}
    got = {names.get(i.id, i.id) for i in r.items}
    assert got == {name for name, hops in NEEDLES.items() if hops <= (depth or 50)}
