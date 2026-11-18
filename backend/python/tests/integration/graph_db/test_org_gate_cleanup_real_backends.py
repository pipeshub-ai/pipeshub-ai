"""The org gate's grant cleanup and the org user read, on real Neo4j and ArangoDB.

Before a team connector lets the org's All team through its gate (content the source opens to anyone), every grant still
naming a user the connector does not link goes: ``remove_unlinked_user_access_to_connector`` checks the link in the same
statement, so a linked user keeps theirs. ``get_users`` lists the org's users only (no record group that belongs to the
org) and answers a failed read with no users unless asked to raise.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD. PostgreSQL has the same cases in postgres/test_grants.py and postgres/test_identity.py.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "org_gate_cleanup_it"
NOW = 1_700_000_000_000
STAMPS = {"createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}

logger = logging.getLogger("org-gate-cleanup-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    yield provider
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is not None:
        await disconnect()


async def _granted(graph: IGraphDBProvider, user: str) -> set[str]:
    edges = await graph.get_edges_from_node(
        f"{CollectionNames.USERS.value}/{user}", CollectionNames.PERMISSION.value, raise_on_error=True
    )
    return {str(e.get("to_id") or e.get("_to") or "").rsplit("/", 1)[-1] for e in edges}


async def test_users_the_connector_does_not_link_lose_their_grants_on_it_and_linked_users_keep_theirs(
    graph: IGraphDBProvider,
) -> None:
    run = uuid.uuid4().hex[:10]
    org_id, app, other_app = f"org-gate-{run}", f"jira-{run}", f"drive-{run}"
    linked, stale = f"linked-{run}", f"stale-{run}"
    group, elsewhere = f"proj-{run}", f"folder-{run}"
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "accountType": "enterprise", "isActive": True, "name": "org gate", **STAMPS}],
            collection=CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": a, "orgId": org_id, "name": a, "type": "Jira", "appGroup": "Atlassian", "scope": "team",
              "isActive": True, "createdBy": linked, **STAMPS} for a in (app, other_app)],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": u, "userId": u, "orgId": org_id, "email": f"{u}@example.com", "isActive": True, **STAMPS}
             for u in (linked, stale)],
            collection=CollectionNames.USERS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": g, "orgId": org_id, "connectorId": c, "groupName": g, "groupType": "PROJECT", "connectorName": "JIRA",
              **STAMPS} for g, c in ((group, app), (elsewhere, other_app))],
            collection=CollectionNames.RECORD_GROUPS.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": linked, "from_collection": CollectionNames.USERS.value, "to_id": app,
              "to_collection": CollectionNames.APPS.value, "syncState": "NOT_STARTED", "lastSyncUpdate": NOW, **STAMPS}],
            collection=CollectionNames.USER_APP_RELATION.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": u, "from_collection": CollectionNames.USERS.value, "to_id": g,
              "to_collection": CollectionNames.RECORD_GROUPS.value, "role": "READER", "type": "USER", **STAMPS}
             for u, g in ((linked, group), (stale, group), (stale, elsewhere))],
            collection=CollectionNames.PERMISSION.value,
        )

        assert await graph.remove_unlinked_user_access_to_connector(app) == 1
        assert await _granted(graph, stale) == {elsewhere}, "another connector's grant stays"
        assert await _granted(graph, linked) == {group}, "a linked user keeps theirs"
        assert await graph.remove_unlinked_user_access_to_connector(app) == 0
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([group, elsewhere], CollectionNames.RECORD_GROUPS.value)
            await graph.delete_nodes_and_edges([app, other_app], CollectionNames.APPS.value)
            await graph.delete_nodes_and_edges([linked, stale], CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([org_id], CollectionNames.ORGS.value)


async def test_the_org_user_read_lists_users_only_and_a_failure_is_no_users_unless_asked_to_raise(
    graph: IGraphDBProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = uuid.uuid4().hex[:10]
    org_id, user, group = f"org-users-{run}", f"user-{run}", f"group-{run}"
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "accountType": "enterprise", "isActive": True, "name": "org users", **STAMPS}],
            collection=CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": user, "userId": user, "orgId": org_id, "email": f"{user}@example.com", "isActive": True, **STAMPS}],
            collection=CollectionNames.USERS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": group, "orgId": org_id, "connectorId": f"c-{run}", "groupName": group, "groupType": "PROJECT",
              "connectorName": "JIRA", **STAMPS}],
            collection=CollectionNames.RECORD_GROUPS.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": key, "from_collection": coll, "to_id": org_id, "to_collection": CollectionNames.ORGS.value,
              "entityType": "ORGANIZATION", **STAMPS}
             for key, coll in ((user, CollectionNames.USERS.value), (group, CollectionNames.RECORD_GROUPS.value))],
            collection=CollectionNames.BELONGS_TO.value,
        )
        assert {u.get("_key") or u.get("id") for u in await graph.get_users(org_id, active=False)} == {user}

        def down(*_a: object, **_k: object) -> object:
            raise RuntimeError("store down")

        with monkeypatch.context() as patched:  # undone before the cleanup below, which uses the same query method
            if isinstance(graph, Neo4jProvider):
                patched.setattr(graph.client, "execute_query", down)
            else:
                patched.setattr(graph.http_client, "execute_aql", down)
            assert await graph.get_users(org_id) == []
            with pytest.raises(RuntimeError):
                await graph.get_users(org_id, raise_on_error=True)
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([group], CollectionNames.RECORD_GROUPS.value)
            await graph.delete_nodes_and_edges([user], CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([org_id], CollectionNames.ORGS.value)
