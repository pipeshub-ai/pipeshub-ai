"""ensure_nodes against real Neo4j and ArangoDB.

Taxonomy nodes are written by many records at once, so ensure_nodes must create
a node exactly once under concurrency and never modify one that exists. Both
guarantees are database behaviour (MERGE ... ON CREATE under a uniqueness
constraint; insert with overwriteMode=ignore), so they are checked on the real
engines rather than mocks.

Run against throwaway engines, never a stack with data in it:

  docker run -d --rm --name neo4j-ensure-it -p 17687:7687 \\
      -e NEO4J_AUTH=neo4j/ensure-it-pass neo4j:5.26.0
  docker run -d --rm --name arango-ensure-it -p 18529:8529 \\
      -e ARANGO_ROOT_PASSWORD=ensure-it-pass arangodb:3.12

Each test skips when its engine is not reachable.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = pytest.mark.integration

NEO4J_URI = os.environ.get("GRAPH_IT_NEO4J_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("GRAPH_IT_NEO4J_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("GRAPH_IT_ARANGO_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("GRAPH_IT_ARANGO_PASSWORD", "ensure-it-pass")

COLLECTION = CollectionNames.TOPICS.value
LABEL = collection_to_label(COLLECTION)
CONCURRENT_WRITERS = 50
_logger = logging.getLogger("ensure-nodes-it")


def _node(key: str, name: str) -> dict[str, str]:
    return {"id": key, "name": name, "normalizedName": name.casefold(), "orgId": "ensure-it"}


@pytest.fixture
async def neo4j(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Neo4jProvider]:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(_logger, AsyncMock())
    if not await provider.connect():
        pytest.skip(f"Neo4j not reachable at {NEO4J_URI}")
    assert provider.client is not None
    # The constraint the provider's schema bootstrap creates for every node label.
    await provider.client.execute_query(
        f"CREATE CONSTRAINT {LABEL.lower()}_id_unique IF NOT EXISTS "
        f"FOR (n:{LABEL}) REQUIRE n.id IS UNIQUE"
    )
    yield provider
    await provider.client.execute_query(
        f"MATCH (n:{LABEL}) WHERE n.orgId = 'ensure-it' DETACH DELETE n"
    )
    await provider.disconnect()


@pytest.fixture
async def arango() -> AsyncIterator[ArangoHTTPProvider]:
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": "ensure_it",
    })
    provider = ArangoHTTPProvider(_logger, config)
    if not await provider.connect():
        pytest.skip(f"ArangoDB not reachable at {ARANGO_URL}")
    assert provider.http_client is not None
    if not await provider.http_client.has_collection(COLLECTION):
        await provider.http_client.create_collection(COLLECTION, edge=False, schema=None)
    yield provider
    await provider.disconnect()


async def _neo4j_names(provider: Neo4jProvider, key: str) -> list[str]:
    assert provider.client is not None
    rows = await provider.client.execute_query(
        f"MATCH (n:{LABEL} {{id: $id}}) RETURN n.name AS name", parameters={"id": key}
    )
    return [row["name"] for row in rows or []]


async def _arango_names(provider: ArangoHTTPProvider, key: str) -> list[str]:
    doc = await provider.get_document(key, COLLECTION)
    return [doc["name"]] if doc else []


async def _race(provider: Neo4jProvider | ArangoHTTPProvider, key: str) -> set[str]:
    names = {f"Writer {i}" for i in range(CONCURRENT_WRITERS)}
    await asyncio.gather(*(provider.ensure_nodes([_node(key, name)], COLLECTION) for name in names))
    return names


class TestNeo4jEnsureNodes:
    @pytest.mark.asyncio
    async def test_concurrent_writers_create_exactly_one_node(self, neo4j: Neo4jProvider) -> None:
        key = str(uuid.uuid4())
        names = await _race(neo4j, key)
        created = await _neo4j_names(neo4j, key)
        assert len(created) == 1
        assert created[0] in names

    @pytest.mark.asyncio
    async def test_an_existing_node_is_never_modified(self, neo4j: Neo4jProvider) -> None:
        key = str(uuid.uuid4())
        await neo4j.ensure_nodes([_node(key, "Original")], COLLECTION)
        await neo4j.ensure_nodes([_node(key, "Changed")], COLLECTION)
        assert await _neo4j_names(neo4j, key) == ["Original"]

    @pytest.mark.asyncio
    async def test_a_mixed_batch_creates_only_the_missing_nodes(self, neo4j: Neo4jProvider) -> None:
        existing, missing = str(uuid.uuid4()), str(uuid.uuid4())
        await neo4j.ensure_nodes([_node(existing, "Original")], COLLECTION)
        await neo4j.ensure_nodes([_node(existing, "Changed"), _node(missing, "New")], COLLECTION)
        assert await _neo4j_names(neo4j, existing) == ["Original"]
        assert await _neo4j_names(neo4j, missing) == ["New"]


class TestArangoEnsureNodes:
    @pytest.mark.asyncio
    async def test_concurrent_writers_create_exactly_one_node(self, arango: ArangoHTTPProvider) -> None:
        key = str(uuid.uuid4())
        names = await _race(arango, key)
        created = await _arango_names(arango, key)
        assert len(created) == 1
        assert created[0] in names

    @pytest.mark.asyncio
    async def test_an_existing_node_is_never_modified(self, arango: ArangoHTTPProvider) -> None:
        key = str(uuid.uuid4())
        await arango.ensure_nodes([_node(key, "Original")], COLLECTION)
        await arango.ensure_nodes([_node(key, "Changed")], COLLECTION)
        assert await _arango_names(arango, key) == ["Original"]

    @pytest.mark.asyncio
    async def test_a_mixed_batch_creates_only_the_missing_nodes(self, arango: ArangoHTTPProvider) -> None:
        existing, missing = str(uuid.uuid4()), str(uuid.uuid4())
        await arango.ensure_nodes([_node(existing, "Original")], COLLECTION)
        await arango.ensure_nodes([_node(existing, "Changed"), _node(missing, "New")], COLLECTION)
        assert await _arango_names(arango, existing) == ["Original"]
        assert await _arango_names(arango, missing) == ["New"]
