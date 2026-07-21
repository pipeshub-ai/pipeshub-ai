"""
Unit tests for get_entities_for_sync bulk/incremental entity sync — record type.

Covers:
- Neo4jProvider._ENTITY_LABEL_MAP / get_entities_for_sync("record")
- ArangoHTTPProvider._ENTITY_COLLECTION_MAP / get_entities_for_sync("record")

These tests exercise query/bind-var construction and result mapping without
requiring a live database connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_neo4j_provider():
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    return p


def _make_arango_provider():
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.config_service = MagicMock()
    p.client = AsyncMock()
    return p


# ============================================================================
# Neo4j bulk sync — record type
# ============================================================================


class TestNeo4jRecordBulkSync:
    def test_record_in_entity_label_map(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        assert "record" in Neo4jProvider._ENTITY_LABEL_MAP
        label, name_field = Neo4jProvider._ENTITY_LABEL_MAP["record"]
        assert label == "Record"
        assert name_field == "recordName"

    @pytest.mark.asyncio
    async def test_get_entities_for_sync_filters_by_org_and_completed_status(self):
        """Record sync query must scope by orgId and only completed records."""
        from app.config.constants.arangodb import ProgressStatus

        provider = _make_neo4j_provider()
        provider.client.execute_query = AsyncMock(return_value=[
            {"id": "rec1", "name": "Q3 Security Report", "description": None},
        ])

        results = await provider.get_entities_for_sync(org_id="org1", entity_types=["record"])

        assert len(results) == 1
        assert results[0]["entityType"] == "record"
        assert results[0]["entityId"] == "rec1"
        assert results[0]["name"] == "Q3 Security Report"
        assert results[0]["orgId"] == "org1"

        call_args = provider.client.execute_query.call_args
        query = call_args.args[0] if call_args.args else call_args.kwargs.get("query", "")
        params = call_args.kwargs.get("parameters", {})
        assert "n.orgId = $org_id" in query
        assert "n.indexingStatus = $completedStatus" in query
        assert params["org_id"] == "org1"
        assert params["completedStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_get_entities_for_sync_skips_rows_without_id(self):
        provider = _make_neo4j_provider()
        provider.client.execute_query = AsyncMock(return_value=[
            {"id": "", "name": "orphan"},
            {"id": "rec2", "name": "Valid Record"},
        ])

        results = await provider.get_entities_for_sync(org_id="org1", entity_types=["record"])

        assert len(results) == 1
        assert results[0]["entityId"] == "rec2"

    @pytest.mark.asyncio
    async def test_get_entities_for_sync_query_failure_returns_empty_not_raises(self):
        provider = _make_neo4j_provider()
        provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db down"))

        results = await provider.get_entities_for_sync(org_id="org1", entity_types=["record"])

        assert results == []

    @pytest.mark.asyncio
    async def test_record_included_in_default_entity_types(self):
        """When entity_types is None, 'record' is among the types queried."""
        provider = _make_neo4j_provider()
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_entities_for_sync(org_id="org1")

        queried_params = [
            call.kwargs.get("parameters", {}) for call in provider.client.execute_query.call_args_list
        ]
        # At least one call should have used the record-specific bind params
        assert any("completedStatus" in params for params in queried_params)


# ============================================================================
# Arango bulk sync — record type
# ============================================================================


class TestArangoRecordBulkSync:
    def test_record_in_entity_collection_map(self):
        from app.config.constants.arangodb import CollectionNames
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        assert "record" in ArangoHTTPProvider._ENTITY_COLLECTION_MAP
        collection, name_field = ArangoHTTPProvider._ENTITY_COLLECTION_MAP["record"]
        assert collection == CollectionNames.RECORDS.value
        assert name_field == "recordName"

    @pytest.mark.asyncio
    async def test_get_entities_for_sync_filters_by_org_and_completed_status(self):
        """Record sync query must scope by orgId and only completed records."""
        from app.config.constants.arangodb import ProgressStatus

        provider = _make_arango_provider()
        provider.execute_query = AsyncMock(return_value=[
            {"_key": "rec1", "recordName": "Q3 Security Report", "description": ""},
        ])

        results = await provider.get_entities_for_sync(org_id="org1", entity_types=["record"])

        assert len(results) == 1
        assert results[0]["entityType"] == "record"
        assert results[0]["entityId"] == "rec1"
        assert results[0]["name"] == "Q3 Security Report"

        call_args = provider.execute_query.call_args
        query = call_args.args[0] if call_args.args else call_args.kwargs.get("query", "")
        bind_vars = call_args.kwargs.get("bind_vars", {})
        assert "node.orgId == @org_id" in query
        assert "node.indexingStatus == @completedStatus" in query
        assert bind_vars["org_id"] == "org1"
        assert bind_vars["completedStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_get_entities_for_sync_skips_rows_without_key(self):
        provider = _make_arango_provider()
        provider.execute_query = AsyncMock(return_value=[
            {"_key": "", "recordName": "orphan"},
            {"_key": "rec2", "recordName": "Valid Record"},
        ])

        results = await provider.get_entities_for_sync(org_id="org1", entity_types=["record"])

        assert len(results) == 1
        assert results[0]["entityId"] == "rec2"

    @pytest.mark.asyncio
    async def test_get_entities_for_sync_query_failure_returns_empty_not_raises(self):
        provider = _make_arango_provider()
        provider.execute_query = AsyncMock(side_effect=RuntimeError("db down"))

        results = await provider.get_entities_for_sync(org_id="org1", entity_types=["record"])

        assert results == []

    @pytest.mark.asyncio
    async def test_non_record_types_do_not_add_completed_status_bind_var(self):
        """Only the 'record' branch should bind completedStatus."""
        provider = _make_arango_provider()
        provider.execute_query = AsyncMock(return_value=[])

        await provider.get_entities_for_sync(org_id="org1", entity_types=["category"])

        call_args = provider.execute_query.call_args
        bind_vars = call_args.kwargs.get("bind_vars", {})
        assert "completedStatus" not in bind_vars
