"""The hierarchy edge rename on ArangoDB when both collections already exist.

The indexing service runs ``ensure_schema()`` too. When it starts before the
connector service on an upgraded deployment, it creates an empty collection
under the new name while the edges still sit under the old one, and the rename
has to fold the old collection into the new one and drop it. ArangoDB refuses to
drop a collection a named graph still uses, and this migration is fatal at
startup, so a drop that fails keeps the connector service from starting at all.

Neo4j renames a relationship type and has no such state.

Runs in backend-matrix on the arangodb graph job. Environment: ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, GraphNames
from app.migrations.node_relation_migration import (
    LEGACY_ARANGO_COLLECTION,
    LEGACY_NEO4J_RELATIONSHIP_TYPE,
)
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "legacy_hierarchy_edge_rename_it"
CURRENT = CollectionNames.NODE_RELATIONS.value
GRAPH = GraphNames.KNOWLEDGE_GRAPH.value

logger = logging.getLogger("legacy-hierarchy-edge-rename-it")


async def _graph_edge_collections(graph: ArangoHTTPProvider) -> set[str]:
    info = await graph.http_client.get_graph(GRAPH)
    return {definition["collection"] for definition in info["graph"]["edgeDefinitions"]}


async def _edge_keys(graph: ArangoHTTPProvider, collection: str) -> list[str]:
    return await graph.execute_query("FOR e IN @@edges SORT e._key RETURN e._key", bind_vars={"@edges": collection})


@pytest.fixture(params=["arango"])
async def old_deployment(request: pytest.FixtureRequest) -> AsyncIterator[ArangoHTTPProvider]:
    """A database whose hierarchy edges still sit under the legacy name, inside the graph."""
    try:
        graph = await connect_arango(logger, ARANGO_DB)
    except Exception as exc:
        backend_unavailable(request.param, exc)
    try:
        client = graph.http_client
        if await client.collection_exists(LEGACY_ARANGO_COLLECTION):
            # Left by a run that stopped halfway.
            assert await client.remove_edge_definition(GRAPH, LEGACY_ARANGO_COLLECTION)
            assert await client.delete_collection(LEGACY_ARANGO_COLLECTION)
        assert await client.rename_collection(CURRENT, LEGACY_ARANGO_COLLECTION)
        await graph.execute_query(
            "FOR i IN 1..2 INSERT {_key: CONCAT('legacy-', i), _from: CONCAT('records/rename-', i),"
            " _to: CONCAT('records/rename-', i + 1), relationshipType: 'PARENT_CHILD',"
            " createdAtTimestamp: 1, updatedAtTimestamp: 1} INTO @@edges OPTIONS {overwriteMode: 'replace'}",
            bind_vars={"@edges": LEGACY_ARANGO_COLLECTION},
        )
        assert LEGACY_ARANGO_COLLECTION in await _graph_edge_collections(graph)
        yield graph
    finally:
        await graph.disconnect()


async def test_the_rename_alone_carries_the_edges_and_the_graph(old_deployment: ArangoHTTPProvider) -> None:
    graph = old_deployment
    await graph.execute_query("FOR e IN @@edges REMOVE e IN @@edges", bind_vars={"@edges": LEGACY_ARANGO_COLLECTION})
    await graph.execute_query(
        "INSERT {_key: 'legacy-1', _from: 'records/rename-1', _to: 'records/rename-2',"
        " relationshipType: 'PARENT_CHILD', createdAtTimestamp: 1, updatedAtTimestamp: 1} INTO @@edges",
        bind_vars={"@edges": LEGACY_ARANGO_COLLECTION},
    )

    result = await graph.migrate_legacy_relation_edge(
        legacy_collection=LEGACY_ARANGO_COLLECTION, legacy_relationship_type=LEGACY_NEO4J_RELATIONSHIP_TYPE,
    )

    assert result == {"migrated": 1, "already_current": False}
    assert not await graph.http_client.collection_exists(LEGACY_ARANGO_COLLECTION)
    assert await _edge_keys(graph, CURRENT) == ["legacy-1"]
    edge_collections = await _graph_edge_collections(graph)
    assert CURRENT in edge_collections and LEGACY_ARANGO_COLLECTION not in edge_collections


async def test_both_collections_exist_when_another_service_made_the_schema_first(
    old_deployment: ArangoHTTPProvider,
) -> None:
    graph = old_deployment
    assert await graph.ensure_schema()
    assert await graph.http_client.collection_exists(CURRENT)
    assert await _edge_keys(graph, CURRENT) == []

    result = await graph.migrate_legacy_relation_edge(
        legacy_collection=LEGACY_ARANGO_COLLECTION, legacy_relationship_type=LEGACY_NEO4J_RELATIONSHIP_TYPE,
    )

    assert result == {"migrated": 2, "already_current": False}
    assert not await graph.http_client.collection_exists(LEGACY_ARANGO_COLLECTION)
    assert await _edge_keys(graph, CURRENT) == ["legacy-1", "legacy-2"]
    edge_collections = await _graph_edge_collections(graph)
    assert CURRENT in edge_collections and LEGACY_ARANGO_COLLECTION not in edge_collections

    again = await graph.migrate_legacy_relation_edge(
        legacy_collection=LEGACY_ARANGO_COLLECTION, legacy_relationship_type=LEGACY_NEO4J_RELATIONSHIP_TYPE,
    )
    assert again == {"migrated": 0, "already_current": True}
    # The schema pass and a cascading delete both read the graph's edge collections.
    assert await graph.ensure_schema()
    await graph.delete_nodes_and_edges(["rename-2"], CollectionNames.RECORDS.value)
    assert await _edge_keys(graph, CURRENT) == []
