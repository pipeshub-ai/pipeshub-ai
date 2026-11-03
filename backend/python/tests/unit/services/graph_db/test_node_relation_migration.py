"""The node relation edge migration.

It runs once, before schema init, and writes a completion flag that makes every
later startup skip it. That combination is what would make a silent partial
failure permanent: edges left under the old name match no query, and nothing
ever re-checks them.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

LEGACY_COLLECTION = "recordRelations"
LEGACY_TYPE = "RECORD_RELATION"


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.execute_query = AsyncMock()
    return provider


class TestNeo4jLegacyRelationMigration:
    @pytest.mark.asyncio
    async def test_no_legacy_edges_is_a_no_op(self, neo4j: Neo4jProvider) -> None:
        neo4j.client.execute_query = AsyncMock(return_value=[{"n": 0}])

        result = await neo4j.migrate_legacy_relation_edge(
            legacy_collection=LEGACY_COLLECTION,
            legacy_relationship_type=LEGACY_TYPE,
        )

        assert result == {"migrated": 0, "already_current": True}
        neo4j.client.execute_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apoc_partial_failure_is_not_reported_as_success(
        self, neo4j: Neo4jProvider
    ) -> None:
        """APOC batches internally and returns normally on partial failure.

        committedOperations alone cannot tell a complete rename from one that
        left edges behind, so the count is re-probed. Without that, the caller
        writes the completion flag over a half-migrated graph and those edges
        become permanently invisible.
        """
        neo4j.client.execute_query = AsyncMock(side_effect=[
            [{"n": 10}],  # edges still carrying the legacy type
            [{"n": 4}],   # APOC reports 4 committed
            [{"n": 6}],   # but 6 are still there
        ])

        with pytest.raises(Exception, match="still carry"):
            await neo4j.migrate_legacy_relation_edge(
                legacy_collection=LEGACY_COLLECTION,
                legacy_relationship_type=LEGACY_TYPE,
            )

    @pytest.mark.asyncio
    async def test_apoc_success_is_verified_before_reporting(
        self, neo4j: Neo4jProvider
    ) -> None:
        neo4j.client.execute_query = AsyncMock(side_effect=[
            [{"n": 10}], [{"n": 10}], [{"n": 0}],
        ])

        result = await neo4j.migrate_legacy_relation_edge(
            legacy_collection=LEGACY_COLLECTION,
            legacy_relationship_type=LEGACY_TYPE,
        )

        assert result == {"migrated": 10, "already_current": False}
        assert neo4j.client.execute_query.await_count == 3, (
            "the verification probe must run before success is reported"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_batches_when_apoc_is_absent(
        self, neo4j: Neo4jProvider
    ) -> None:
        """A self-managed Neo4j may not ship APOC, unlike every shipped image."""
        neo4j.client.execute_query = AsyncMock(side_effect=[
            [{"n": 2}],
            Exception("Unknown procedure 'apoc.refactor.rename.type'"),
            [{"n": 2}],  # first batch recreated
            [{"n": 0}],  # nothing left, loop terminates
        ])

        result = await neo4j.migrate_legacy_relation_edge(
            legacy_collection=LEGACY_COLLECTION,
            legacy_relationship_type=LEGACY_TYPE,
        )

        assert result == {"migrated": 2, "already_current": False}

    @pytest.mark.asyncio
    async def test_an_unsafe_relationship_type_is_refused(
        self, neo4j: Neo4jProvider
    ) -> None:
        """The type is interpolated, never parameterised: it is Cypher syntax,
        not a value. The guard is what keeps that safe."""
        with pytest.raises(ValueError):
            await neo4j.migrate_legacy_relation_edge(
                legacy_collection=LEGACY_COLLECTION,
                legacy_relationship_type="BAD TYPE`",
            )


class TestArangoLegacyRelationMigration:
    @pytest.mark.asyncio
    async def test_absent_legacy_collection_is_a_no_op(
        self, arango: ArangoHTTPProvider
    ) -> None:
        arango.http_client.collection_exists = AsyncMock(side_effect=[False, True])

        result = await arango.migrate_legacy_relation_edge(
            legacy_collection=LEGACY_COLLECTION,
            legacy_relationship_type=LEGACY_TYPE,
        )

        assert result == {"migrated": 0, "already_current": True}
        arango.http_client.rename_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renames_when_only_the_legacy_collection_exists(
        self, arango: ArangoHTTPProvider
    ) -> None:
        arango.http_client.collection_exists = AsyncMock(side_effect=[True, False])
        arango.http_client.rename_collection = AsyncMock(return_value=True)
        arango.execute_query = AsyncMock(return_value=[7])

        result = await arango.migrate_legacy_relation_edge(
            legacy_collection=LEGACY_COLLECTION,
            legacy_relationship_type=LEGACY_TYPE,
        )

        assert result == {"migrated": 7, "already_current": False}
        arango.http_client.rename_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_both_existing_merges_then_drops_the_legacy_collection(
        self, arango: ArangoHTTPProvider
    ) -> None:
        """Reached when another service ran ensure_schema() first, creating an
        empty collection under the new name while the edges still sat under the
        old one."""
        arango.http_client.collection_exists = AsyncMock(side_effect=[True, True])
        arango.execute_query = AsyncMock(return_value=[3])
        arango.http_client.delete_collection = AsyncMock(return_value=True)

        result = await arango.migrate_legacy_relation_edge(
            legacy_collection=LEGACY_COLLECTION,
            legacy_relationship_type=LEGACY_TYPE,
        )

        assert result == {"migrated": 3, "already_current": False}
        arango.http_client.delete_collection.assert_awaited_once_with(LEGACY_COLLECTION)

    @pytest.mark.asyncio
    async def test_a_failed_drop_raises_rather_than_reporting_success(
        self, arango: ArangoHTTPProvider
    ) -> None:
        """Leaving the legacy collection in place splits the edges across two
        collections, and the flag would stop anyone noticing."""
        arango.http_client.collection_exists = AsyncMock(side_effect=[True, True])
        arango.execute_query = AsyncMock(return_value=[3])
        arango.http_client.delete_collection = AsyncMock(return_value=False)

        with pytest.raises(Exception, match="could not drop"):
            await arango.migrate_legacy_relation_edge(
                legacy_collection=LEGACY_COLLECTION,
                legacy_relationship_type=LEGACY_TYPE,
            )


class _Response:
    """Minimal aiohttp response double: an async context manager with .status."""

    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False


class _Session:
    """`session.post(...)` must return the context manager, not a coroutine."""

    def __init__(self, response: _Response) -> None:
        self._response = response
        self.posts: list[tuple[str, dict]] = []
        self.puts: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict | None = None) -> _Response:
        self.posts.append((url, json or {}))
        return self._response

    def put(self, url: str, json: dict | None = None) -> _Response:
        self.puts.append((url, json or {}))
        return self._response


class TestEdgeDefinitionRegistration:
    """The other half of the rename, and the half with teeth.

    A renamed collection arrives in a database whose named graph has never
    heard of it. Until it is registered, `GRAPH` traversals cannot cross it and
    `delete_nodes_and_edges` — which enumerates edge collections *from the
    graph definition* — cannot see it, so deleting a record silently leaves
    every hierarchy edge dangling. The old code skipped absent definitions
    entirely (`if not existing: continue`), so it only ever widened ones that
    already existed.
    """

    @pytest.mark.asyncio
    async def test_a_missing_edge_definition_is_added(
        self, arango: ArangoHTTPProvider
    ) -> None:
        """The named graph must admit NODE_RELATION, or every hierarchy
        edge written through it dangles."""
        session = _Session(_Response(201))
        arango.http_client.base_url = "http://arango:8529"
        arango.http_client.database = "testdb"
        arango.http_client.get_graph = AsyncMock(
            return_value={"graph": {"edgeDefinitions": []}}
        )
        arango.http_client._get_session = AsyncMock(return_value=session)

        await arango._ensure_edge_definitions_up_to_date("knowledgeGraph")

        posted = {payload["collection"] for _url, payload in session.posts}
        assert "nodeRelations" in posted, sorted(posted)
        for _url, payload in session.posts:
            assert payload["from"] and payload["to"], payload
            assert _url.endswith("/_api/gharial/knowledgeGraph/edge"), _url

    @pytest.mark.asyncio
    async def test_an_existing_definition_is_not_re_added(
        self, arango: ArangoHTTPProvider
    ) -> None:
        """It is widened via PUT instead — POSTing it again would 409."""
        session = _Session(_Response(200))
        arango.http_client.base_url = "http://arango:8529"
        arango.http_client.database = "testdb"
        arango.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [
                {"collection": "nodeRelations", "from": ["records"], "to": ["records"]},
            ]}
        })
        arango.http_client._get_session = AsyncMock(return_value=session)

        await arango._ensure_edge_definitions_up_to_date("knowledgeGraph")

        assert "nodeRelations" not in {p["collection"] for _u, p in session.posts}
