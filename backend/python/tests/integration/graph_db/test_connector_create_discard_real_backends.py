"""A connector instance created and then discarded leaves nothing behind, on a real Neo4j and ArangoDB.

A connector create that fails after its instance write discards the instance
(``ConnectorRegistry.discard_connector_instance``, the providers' own
``delete_connector_instance``), so that a retry with the same name works. The
unit tests fake that delete. This checks it on the real stores for an instance
that holds nothing yet: the node and its org edge are gone, the name is free for
either scope, and another instance of the org is untouched. The PostgreSQL twin
is postgres/test_connector_create_discard.py.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_connector_create_discard_real_backends.py -m integration
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "connector_create_discard_it"
APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value

logger = logging.getLogger("connector-create-discard-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider


def _registry(graph: IGraphDBProvider) -> ConnectorRegistry:
    container = MagicMock()
    container.logger = MagicMock(return_value=logger)
    # No licence key reads as licensed, so a personal create takes no cap lock.
    container.config_service.return_value.get_config = AsyncMock(return_value=None)
    registry = ConnectorRegistry(container)
    registry._graph_provider = graph
    registry._connectors["Drive"] = {"appGroup": "Google Workspace", "supportedAuthTypes": ["OAUTH"], "config": {}}
    registry.get_connector_metadata = AsyncMock(return_value=registry._connectors["Drive"])  # type: ignore[method-assign]
    return registry


async def _listed(graph: IGraphDBProvider, org_id: str, user_id: str) -> set[str]:
    """The org's connectors an admin lists, which the list reaches through the org edge."""
    documents, _total = await graph.get_filtered_connector_instances(
        collection=APPS, edge_collection=CollectionNames.ORG_APP_RELATION.value,
        org_id=org_id, user_id=user_id, skip=0, limit=50, is_admin=True,
    )
    return {d.get("_key") or d.get("id") for d in documents}


async def _org_edge(graph: IGraphDBProvider, org_id: str, connector_id: str) -> dict | None:
    return await graph.get_edge(org_id, ORGS, connector_id, APPS, CollectionNames.ORG_APP_RELATION.value, raise_on_error=True)


async def _name_taken(graph: IGraphDBProvider, name: str, scope: str, org_id: str, user_id: str) -> bool:
    return await graph.check_connector_name_exists(
        collection=APPS, instance_name=name, scope=scope, org_id=org_id, user_id=user_id,
    )


@pytest.mark.parametrize("scope", ["personal", "team"])
async def test_a_fresh_instance_discarded_leaves_no_node_no_edge_and_frees_its_name(
    graph: IGraphDBProvider, scope: str,
) -> None:
    run = uuid.uuid4().hex[:10]
    org_id, user_id = f"org-discard-{run}", f"user-discard-{run}"
    now = get_epoch_timestamp_in_ms()
    registry = _registry(graph)
    created: list[str] = []
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "accountType": "enterprise", "name": "Acme", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=ORGS,
        )
        for name in ("Doomed", "Kept"):
            instance = await registry.create_connector_instance_on_configuration(
                connector_type="Drive", instance_name=name, scope=scope, created_by=user_id, org_id=org_id,
                is_admin=True, selected_auth_type="OAUTH",
            )
            assert instance is not None
            created.append(instance["_key"])
        doomed, kept = created
        assert await _listed(graph, org_id, user_id) == {doomed, kept}
        assert await _org_edge(graph, org_id, doomed) is not None
        assert await _name_taken(graph, "Doomed", scope, org_id, user_id)

        started = time.monotonic()
        await registry.discard_connector_instance(doomed, org_id)
        logger.info("discard_connector_instance took %.0f ms", (time.monotonic() - started) * 1000)

        assert await graph.get_document(doomed, APPS) is None
        assert await _org_edge(graph, org_id, doomed) is None
        assert await _org_edge(graph, org_id, kept) is not None
        assert await _listed(graph, org_id, user_id) == {kept}
        assert not await _name_taken(graph, "Doomed", scope, org_id, user_id)
        assert await _name_taken(graph, "Kept", scope, org_id, user_id)
        assert (await graph.get_document(kept, APPS))["name"] == "Kept"

        retry = await registry.create_connector_instance_on_configuration(
            connector_type="Drive", instance_name="Doomed", scope=scope, created_by=user_id, org_id=org_id,
            is_admin=True, selected_auth_type="OAUTH",
        )
        assert retry is not None
        created.append(retry["_key"])
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges(created, APPS)
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([org_id], ORGS)
