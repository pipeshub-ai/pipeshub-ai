"""BOOKSTACK-06: a connector clearing a user's role memberships before rewriting
its own must not take the user's roles in other connectors. Both providers.

The behaviour on real databases is in
tests/integration/graph_permissions/test_resync_identity.py.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql.return_value = []
    return provider


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query.return_value = [{"deleted": 0}]
    return provider


@pytest.mark.asyncio
async def test_arango_deletes_only_the_given_connectors_role_edges(arango) -> None:
    await arango.delete_edges_between_collections(
        "u1", "users", "permission", "roles", to_connector_id="bs-1"
    )

    query = arango.http_client.execute_aql.await_args.args[0]
    bind = arango.http_client.execute_aql.await_args.kwargs["bind_vars"]
    assert "DOCUMENT(edge._to).connectorId == @to_connector_id" in query
    assert bind["to_connector_id"] == "bs-1"


@pytest.mark.asyncio
async def test_arango_without_a_connector_deletes_every_edge_to_the_collection(arango) -> None:
    await arango.delete_edges_between_collections("u1", "users", "permission", "roles")

    assert arango.http_client.execute_aql.await_args.kwargs["bind_vars"]["to_connector_id"] is None


@pytest.mark.asyncio
async def test_neo4j_deletes_only_the_given_connectors_role_edges(neo4j) -> None:
    await neo4j.delete_edges_between_collections(
        "u1", "users", "permission", "roles", to_connector_id="bs-1"
    )

    query = neo4j.client.execute_query.await_args.args[0]
    params = neo4j.client.execute_query.await_args.kwargs["parameters"]
    assert "to.connectorId = $to_connector_id" in query
    assert params["to_connector_id"] == "bs-1"


@pytest.mark.asyncio
async def test_neo4j_without_a_connector_deletes_every_edge_to_the_collection(neo4j) -> None:
    await neo4j.delete_edges_between_collections("u1", "users", "permission", "roles")

    params = neo4j.client.execute_query.await_args.kwargs["parameters"]
    assert params["to_connector_id"] is None
    assert "$to_connector_id IS NULL" in neo4j.client.execute_query.await_args.args[0]
