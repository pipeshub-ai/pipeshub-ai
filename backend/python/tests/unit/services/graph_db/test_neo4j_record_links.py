"""Links between records live on their own relationship type:
NODE_RELATION carries hierarchy only, so the knowledge hub and the access check
cannot walk a link as a parent-child edge."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.record_link_migration import RecordLinkMigrationService
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(rows=None) -> tuple[Neo4jProvider, list]:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    queries: list = []

    async def execute(query, parameters=None, txn_id=None) -> list:
        queries.append((query, parameters))
        return rows.pop(0) if rows else []

    provider.client = MagicMock()
    provider.client.execute_query = execute
    return provider, queries


def _edge(to_id: str, relation_type: str | None) -> dict:
    edge = {"from_id": "a", "from_collection": "records", "to_id": to_id, "to_collection": "records"}
    if relation_type is not None:
        edge["relationshipType"] = relation_type
    return edge


class TestWritesRouteByType:
    @pytest.mark.asyncio
    async def test_a_link_is_written_as_a_record_link_and_hierarchy_stays(self) -> None:
        provider, queries = _provider()
        await provider.batch_create_edges(
            [_edge("child", "PARENT_CHILD"), _edge("blocked", "BLOCKS"), _edge("untyped", None)],
            CollectionNames.NODE_RELATIONS.value,
        )
        by_target = {
            e["to_key"]: q for q, params in queries for e in params["edges"]
        }
        assert "MERGE (from)-[r:NODE_RELATION]->(to)" in by_target["child"]
        assert "MERGE (from)-[r:NODE_RELATION]->(to)" in by_target["untyped"]
        assert "MERGE (from)-[r:RECORD_LINK {relationshipType: edge.props.relationshipType}]->(to)" in by_target["blocked"]

    @pytest.mark.asyncio
    async def test_other_collections_are_untouched(self) -> None:
        provider, queries = _provider()
        await provider.batch_create_edges([_edge("g", "BLOCKS")], CollectionNames.BELONGS_TO.value)
        assert "RECORD_LINK" not in queries[0][0]

    @pytest.mark.asyncio
    async def test_foreign_keys_are_links(self) -> None:
        provider, queries = _provider()
        await provider.batch_upsert_node_relations([
            {"from_id": "t1", "to_id": "t2", "relationshipType": "FOREIGN_KEY", "constraintName": "fk"},
        ])
        assert len(queries) == 1 and "MERGE (from)-[r:RECORD_LINK " in queries[0][0]


class TestReadsCoverBoth:
    def test_node_relations_reads_cover_links(self) -> None:
        assert Neo4jProvider._rel_pattern(CollectionNames.NODE_RELATIONS.value) == "NODE_RELATION|RECORD_LINK"
        assert Neo4jProvider._rel_pattern(CollectionNames.BELONGS_TO.value) == "BELONGS_TO"

    @pytest.mark.asyncio
    async def test_link_cleanup_reaches_record_links(self) -> None:
        provider, queries = _provider(rows=[[{"deleted_count": 2}]])
        deleted = await provider.delete_edges_by_relationship_types(
            "a", "records", CollectionNames.NODE_RELATIONS.value, ["BLOCKS"],
        )
        assert deleted == 2 and "NODE_RELATION|RECORD_LINK" in queries[0][0]

    @pytest.mark.asyncio
    async def test_a_single_pair_delete_stays_on_the_hierarchy(self) -> None:
        """Re-parenting deletes the old hierarchy edge; a link between the same
        pair must survive it."""
        provider, queries = _provider(rows=[[{"deleted": 1}]])
        await provider.delete_edge("p", "records", "c", "records", CollectionNames.NODE_RELATIONS.value)
        assert "RECORD_LINK" not in queries[0][0]


class TestMigration:
    @pytest.mark.asyncio
    async def test_the_split_is_one_scan_committed_in_batches(self) -> None:
        provider, queries = _provider(rows=[[{"n": 1007}]])
        assert await provider.split_link_edges(batch_size=500) == {"migrated": 1007}
        (query, params), = queries
        assert "coalesce(r.relationshipType, r.relationType)" in query
        assert "IN TRANSACTIONS OF 500 ROWS" in query
        assert params["hierarchy"] == ["ATTACHMENT", "PARENT_CHILD"]

    @pytest.mark.asyncio
    async def test_the_split_keeps_the_upsert_key(self) -> None:
        """Two foreign keys between the same tables differ by constraint name."""
        provider, queries = _provider(rows=[[{"n": 0}]])
        await provider.split_link_edges()
        assert "{relationshipType: t, constraintName: coalesce(r.constraintName, '')}" in queries[0][0]

    @staticmethod
    def _service(provider, done=False) -> tuple[RecordLinkMigrationService, MagicMock]:
        config = MagicMock()
        config.get_config = AsyncMock(return_value={"done": True} if done else None)
        config.set_config = AsyncMock()
        return RecordLinkMigrationService(provider, config, MagicMock()), config

    @pytest.mark.asyncio
    async def test_the_flag_is_set_after_a_successful_run(self) -> None:
        provider = MagicMock()
        provider.split_link_edges = AsyncMock(return_value={"migrated": 3})
        service, config = self._service(provider)
        assert await service.migrate() == {"success": True, "migrated": 3}
        assert config.set_config.await_args.args[0] == "/migrations/record_link_v1"

    @pytest.mark.asyncio
    async def test_a_backend_without_the_split_is_skipped_and_not_flagged(self) -> None:
        provider = MagicMock()
        provider.split_link_edges = AsyncMock(side_effect=NotImplementedError)
        service, config = self._service(provider)
        result = await service.migrate()
        assert result["success"] and result["unsupported"]
        config.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failure_is_retried_next_startup(self) -> None:
        provider = MagicMock()
        provider.split_link_edges = AsyncMock(side_effect=RuntimeError("down"))
        service, config = self._service(provider)
        assert (await service.migrate())["success"] is False
        config.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_finished_migration_does_not_run_again(self) -> None:
        provider = MagicMock()
        provider.split_link_edges = AsyncMock()
        service, _ = self._service(provider, done=True)
        assert (await service.migrate())["skipped"] is True
        provider.split_link_edges.assert_not_called()
