"""Against a real ArangoDB: a knowledge graph provisioned before an edge
collection existed gets that edge definition on the next ``ensure_schema``,
so deletes that list edge collections from the graph remove its edges.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_arango_graph_migration_real_backend.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, GraphNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "graph_migration_it"
GRAPH = GraphNames.KNOWLEDGE_GRAPH.value
MENTIONS = CollectionNames.MENTIONS_ENTITY.value

logger = logging.getLogger("arango-graph-migration-it")


async def _open() -> ArangoHTTPProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {ARANGO_URL}: {exc}")
    assert await provider.ensure_schema()
    return provider


async def _drop_definition(provider: ArangoHTTPProvider, edge_collection: str) -> None:
    client = provider.http_client
    session = await client._get_session()
    url = f"{client.base_url}/_db/{client.database}/_api/gharial/{GRAPH}/edge/{edge_collection}"
    async with session.delete(url, params={"dropCollections": "false"}) as resp:
        assert resp.status in (200, 202), await resp.text()


async def test_an_edge_collection_missing_from_the_graph_is_added_back() -> None:
    provider = await _open()
    try:
        await _drop_definition(provider, MENTIONS)
        assert MENTIONS not in await provider._get_all_edge_collections()
        assert await provider.http_client.has_collection(MENTIONS)

        assert await provider.ensure_schema()

        assert MENTIONS in await provider._get_all_edge_collections()
        graph = await provider.http_client.get_graph(GRAPH)
        (definition,) = [d for d in graph["graph"]["edgeDefinitions"] if d["collection"] == MENTIONS]
        assert definition["from"] == [CollectionNames.RECORDS.value]
        assert definition["to"] == [CollectionNames.NAMED_ENTITIES.value]
    finally:
        await provider.disconnect()
