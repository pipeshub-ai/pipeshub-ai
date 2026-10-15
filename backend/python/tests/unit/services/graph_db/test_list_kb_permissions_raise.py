import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.client = MagicMock()
    return provider


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.http_client = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_neo4j_failure_is_empty_by_default_and_raised_on_request(neo4j_provider) -> None:
    neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("down"))

    assert await neo4j_provider.list_kb_permissions("kb1") == []
    with pytest.raises(RuntimeError, match="down"):
        await neo4j_provider.list_kb_permissions("kb1", raise_on_error=True)


@pytest.mark.asyncio
async def test_arango_failure_is_empty_by_default_and_raised_on_request(arango_provider) -> None:
    arango_provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("down"))

    assert await arango_provider.list_kb_permissions("kb1") == []
    with pytest.raises(RuntimeError, match="down"):
        await arango_provider.list_kb_permissions("kb1", raise_on_error=True)
