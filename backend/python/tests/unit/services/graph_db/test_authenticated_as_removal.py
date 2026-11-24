"""The link removal of ArangoDB and Neo4j is scoped to one connector and finds the link by its connector id."""

from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CONNECTOR_ID = "ws-1"


async def test_arango_removes_the_link_by_key_or_connector_id() -> None:
    provider = object.__new__(ArangoHTTPProvider)
    provider.execute_query = AsyncMock(return_value=[CONNECTOR_ID])

    assert await ArangoHTTPProvider.remove_authenticated_as(provider, CONNECTOR_ID) is True

    query = provider.execute_query.await_args.args[0]
    assert "link._key == @connectorId OR link.connectorId == @connectorId" in query and "REMOVE link" in query
    assert provider.execute_query.await_args.kwargs["bind_vars"] == {"connectorId": CONNECTOR_ID}


async def test_neo4j_removes_only_the_link_with_the_connector_id() -> None:
    provider = object.__new__(Neo4jProvider)
    provider.client = MagicMock(execute_query=AsyncMock(return_value=[{"deleted": 1}]))

    assert await Neo4jProvider.remove_authenticated_as(provider, CONNECTOR_ID) is True

    call = provider.client.execute_query.await_args
    assert "AUTHENTICATED_AS {connectorId: $connector_id}" in call.args[0]
    assert call.kwargs["parameters"] == {"connector_id": CONNECTOR_ID}
