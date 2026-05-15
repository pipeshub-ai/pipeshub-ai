"""
Unit tests for Neo4jProvider.get_documents_paginated
(app/services/graph_db/neo4j/neo4j_provider.py).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    return p


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestNeo4jGetDocumentsPaginatedHappyPath:
    @pytest.mark.asyncio
    async def test_returns_documents(self, provider):
        raw = [{"n": {"_key": "a", "isActive": True}}, {"n": {"_key": "b", "isActive": True}}]
        provider.client.execute_query = AsyncMock(return_value=raw)

        result = await provider.get_documents_paginated("Apps", skip=0, limit=10)

        assert len(result) == 2
        provider.client.execute_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_and_limit_in_parameters(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", skip=20, limit=5)

        call = provider.client.execute_query.call_args
        params: dict = call[1]["parameters"]
        assert params["skip"] == 20
        assert params["limit"] == 5

    @pytest.mark.asyncio
    async def test_skip_limit_in_cypher_query(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", skip=0, limit=10)

        query: str = provider.client.execute_query.call_args[0][0]
        assert "SKIP $skip" in query
        assert "LIMIT $limit" in query

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        result = await provider.get_documents_paginated("Apps")

        assert result == []

    @pytest.mark.asyncio
    async def test_none_result_returns_empty_list(self, provider):
        provider.client.execute_query = AsyncMock(return_value=None)

        result = await provider.get_documents_paginated("Apps")

        assert result == []


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


class TestNeo4jGetDocumentsPaginatedFilters:
    @pytest.mark.asyncio
    async def test_no_filters_omits_where_clause(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", filters=None)

        query: str = provider.client.execute_query.call_args[0][0]
        assert "WHERE" not in query

    @pytest.mark.asyncio
    async def test_single_filter_added_to_query(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", filters={"isActive": True})

        call = provider.client.execute_query.call_args
        query: str = call[0][0]
        params: dict = call[1]["parameters"]
        assert "WHERE" in query
        assert "n.isActive = $fv_isActive" in query
        assert params["fv_isActive"] is True

    @pytest.mark.asyncio
    async def test_multiple_filters_joined_with_and(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated(
            "Apps", filters={"isActive": True, "type": "Confluence"}
        )

        query: str = provider.client.execute_query.call_args[0][0]
        assert "AND" in query

    @pytest.mark.asyncio
    async def test_empty_filters_dict_omits_where_clause(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", filters={})

        query: str = provider.client.execute_query.call_args[0][0]
        assert "WHERE" not in query


# ---------------------------------------------------------------------------
# Sort tests
# ---------------------------------------------------------------------------


class TestNeo4jGetDocumentsPaginatedSort:
    @pytest.mark.asyncio
    async def test_sort_field_added_to_query(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", sort_field="createdAt")

        query: str = provider.client.execute_query.call_args[0][0]
        assert "ORDER BY n.createdAt ASC" in query

    @pytest.mark.asyncio
    async def test_no_sort_field_omits_order_clause(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps")

        query: str = provider.client.execute_query.call_args[0][0]
        assert "ORDER BY" not in query


# ---------------------------------------------------------------------------
# Transaction tests
# ---------------------------------------------------------------------------


class TestNeo4jGetDocumentsPaginatedTransaction:
    @pytest.mark.asyncio
    async def test_transaction_id_forwarded(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", transaction="txn-xyz")

        call = provider.client.execute_query.call_args
        assert call[1]["txn_id"] == "txn-xyz"

    @pytest.mark.asyncio
    async def test_no_transaction_passes_none(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps")

        call = provider.client.execute_query.call_args
        assert call[1]["txn_id"] is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestNeo4jGetDocumentsPaginatedErrorHandling:
    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self, provider):
        provider.client.execute_query = AsyncMock(
            side_effect=RuntimeError("Neo4j unreachable")
        )

        result = await provider.get_documents_paginated("Apps")

        assert result == []
        provider.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_logged_with_collection_name(self, provider):
        provider.client.execute_query = AsyncMock(
            side_effect=RuntimeError("bolt error")
        )

        await provider.get_documents_paginated("MyCollection")

        log_call = provider.logger.error.call_args
        assert "MyCollection" in str(log_call)
