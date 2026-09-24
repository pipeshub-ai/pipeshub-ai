"""Unit tests for vector membership backfill AQL/Cypher builders and providers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.vector_membership_queries import (
    build_app_needing_vector_membership_backfill_aql,
    build_app_needing_vector_membership_backfill_cypher,
    build_page_records_for_vector_membership_backfill_aql,
    build_page_records_for_vector_membership_backfill_cypher,
)


class TestVectorMembershipBackfillQueries:
    def test_aql_app_query_treats_missing_flag_as_false_and_skips_deleting(self):
        query = build_app_needing_vector_membership_backfill_aql()
        assert "FOR doc IN apps" in query
        assert "doc.vectorMembershipBackfilled != true" in query
        assert 'doc.status != "DELETING"' in query
        assert "LIMIT 1" in query

    def test_aql_page_query_uses_connector_id_and_after_key(self):
        with_cursor = build_page_records_for_vector_membership_backfill_aql(
            has_after_key=True
        )
        assert "FOR record IN records" in with_cursor
        assert "record.connectorId == @connector_id" in with_cursor
        assert "record._key > @after_key" in with_cursor
        assert "SORT record._key" in with_cursor
        assert "LIMIT @limit" in with_cursor
        assert "virtualRecordId" in with_cursor

        first_page = build_page_records_for_vector_membership_backfill_aql(
            has_after_key=False
        )
        assert "record._key > @after_key" not in first_page
        assert "record.connectorId == @connector_id" in first_page

    def test_cypher_app_query_treats_missing_flag_as_false_and_skips_deleting(self):
        query = build_app_needing_vector_membership_backfill_cypher()
        assert "MATCH (n:App)" in query
        assert "coalesce(n.vectorMembershipBackfilled, false) = false" in query
        assert "DELETING" in query
        assert "LIMIT 1" in query

    def test_cypher_page_query_uses_connector_id_and_after_key(self):
        query = build_page_records_for_vector_membership_backfill_cypher(
            has_after_key=True
        )
        assert "MATCH (r:Record)" in query
        assert "r.connectorId = $connector_id" in query
        assert "AND r.id > $after_key" in query
        assert "ORDER BY r.id" in query
        assert "LIMIT $limit" in query
        assert "virtualRecordId" in query

    def test_cypher_first_page_omits_after_key_predicate(self):
        """An `IS NULL OR` form would stop the planner range-scanning r.id."""
        query = build_page_records_for_vector_membership_backfill_cypher(
            has_after_key=False
        )
        assert "$after_key" not in query
        assert "IS NULL" not in query
        assert "ORDER BY r.id" in query


class TestArangoVectorMembershipBackfillProvider:
    def _provider(self) -> ArangoHTTPProvider:
        provider = ArangoHTTPProvider(MagicMock(), AsyncMock())
        provider.http_client = AsyncMock()
        return provider

    @pytest.mark.asyncio
    async def test_get_app_executes_backfill_aql(self):
        provider = self._provider()
        provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "app-1", "name": "Drive"}]
        )
        result = await provider.get_app_needing_vector_membership_backfill()
        assert result["_key"] == "app-1"
        query = provider.http_client.execute_aql.await_args.args[0]
        assert "vectorMembershipBackfilled" in query
        assert "DELETING" in query

    @pytest.mark.asyncio
    async def test_get_app_returns_none_when_empty(self):
        provider = self._provider()
        provider.http_client.execute_aql = AsyncMock(return_value=[])
        assert await provider.get_app_needing_vector_membership_backfill() is None

    @pytest.mark.asyncio
    async def test_get_app_propagates_query_failure(self):
        provider = self._provider()
        provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("aql down"))
        with pytest.raises(RuntimeError, match="aql down"):
            await provider.get_app_needing_vector_membership_backfill()

    @pytest.mark.asyncio
    async def test_page_records_passes_after_key(self):
        provider = self._provider()
        provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r2", "virtualRecordId": "v2"}]
        )
        rows = await provider.page_records_for_vector_membership_backfill(
            "conn-1", "r1", 50
        )
        assert rows == [{"_key": "r2", "virtualRecordId": "v2"}]
        query = provider.http_client.execute_aql.await_args.args[0]
        bind_vars = provider.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert "record._key > @after_key" in query
        assert bind_vars["connector_id"] == "conn-1"
        assert bind_vars["after_key"] == "r1"
        assert bind_vars["limit"] == 50

    @pytest.mark.asyncio
    async def test_page_records_omits_after_key_on_first_page(self):
        provider = self._provider()
        provider.http_client.execute_aql = AsyncMock(return_value=[])
        await provider.page_records_for_vector_membership_backfill("conn-1", None, 50)
        query = provider.http_client.execute_aql.await_args.args[0]
        bind_vars = provider.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert "record._key > @after_key" not in query
        assert "after_key" not in bind_vars


class TestNeo4jVectorMembershipBackfillProvider:
    def _provider(self) -> Neo4jProvider:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.client = AsyncMock()
        return provider

    @pytest.mark.asyncio
    async def test_get_app_converts_node(self):
        provider = self._provider()
        provider.client.execute_query = AsyncMock(
            return_value=[{"n": {"id": "app-1", "name": "Drive"}}]
        )
        result = await provider.get_app_needing_vector_membership_backfill()
        assert result["_key"] == "app-1"
        query = provider.client.execute_query.await_args.args[0]
        assert "vectorMembershipBackfilled" in query
        assert "DELETING" in query

    @pytest.mark.asyncio
    async def test_legacy_kb_migrate_leaves_backfill_false(self):
        provider = self._provider()
        provider.begin_transaction = AsyncMock(return_value="txn")
        provider.commit_transaction = AsyncMock()
        provider.rollback_transaction = AsyncMock()
        provider.batch_create_edges = AsyncMock()
        provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"rg": {"id": "kb1"}}],
                None,
                [{"owner_count": 1}],
            ]
        )
        result = await provider.migrate_legacy_kb_to_app(
            {"_key": "kb1", "groupName": "Old KB"},
            "org-1",
            "user-1",
        )
        assert result["success"] is True
        query = provider.client.execute_query.await_args_list[0].args[0]
        assert "rg.vectorMembershipBackfilled = false" in query

    @pytest.mark.asyncio
    async def test_page_records_passes_after_key(self):
        provider = self._provider()
        provider.client.execute_query = AsyncMock(
            return_value=[{"_key": "r2", "virtualRecordId": "v2"}]
        )
        rows = await provider.page_records_for_vector_membership_backfill(
            "conn-1", "r1", 50
        )
        assert rows == [{"_key": "r2", "virtualRecordId": "v2"}]
        params = provider.client.execute_query.await_args.kwargs["parameters"]
        query = provider.client.execute_query.await_args.args[0]
        assert params["connector_id"] == "conn-1"
        assert params["after_key"] == "r1"
        assert params["limit"] == 50
        assert "r.id > $after_key" in query

    @pytest.mark.asyncio
    async def test_get_app_returns_none_when_empty(self):
        provider = self._provider()
        provider.client.execute_query = AsyncMock(return_value=[])
        assert await provider.get_app_needing_vector_membership_backfill() is None

    @pytest.mark.asyncio
    async def test_get_app_propagates_query_failure(self):
        provider = self._provider()
        provider.client.execute_query = AsyncMock(side_effect=RuntimeError("cypher down"))
        with pytest.raises(RuntimeError, match="cypher down"):
            await provider.get_app_needing_vector_membership_backfill()
