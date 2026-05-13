"""
Unit tests for ArangoHTTPProvider.get_documents_paginated
(app/services/graph_db/arango/arango_http_provider.py).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    p.http_client = AsyncMock()
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(key: str, **extra) -> dict:
    return {"_key": key, "_id": f"Apps/{key}", **extra}


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestGetDocumentsPaginatedHappyPath:
    @pytest.mark.asyncio
    async def test_returns_documents_from_aql(self, provider):
        docs = [_make_doc("a"), _make_doc("b")]
        provider.http_client.execute_aql = AsyncMock(return_value=docs)

        result = await provider.get_documents_paginated("Apps", skip=0, limit=10)

        assert result == docs
        provider.http_client.execute_aql.assert_called_once()
        call_args = provider.http_client.execute_aql.call_args
        query: str = call_args[0][0]
        bind_vars: dict = call_args[1]["bind_vars"]
        assert "LIMIT @skip, @limit" in query
        assert bind_vars["skip"] == 0
        assert bind_vars["limit"] == 10
        assert bind_vars["@collection"] == "Apps"

    @pytest.mark.asyncio
    async def test_skip_and_limit_forwarded_correctly(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", skip=50, limit=25)

        bind_vars = provider.http_client.execute_aql.call_args[1]["bind_vars"]
        assert bind_vars["skip"] == 50
        assert bind_vars["limit"] == 25

    @pytest.mark.asyncio
    async def test_empty_collection_returns_empty_list(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        result = await provider.get_documents_paginated("Apps")

        assert result == []

    @pytest.mark.asyncio
    async def test_none_result_returns_empty_list(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=None)

        result = await provider.get_documents_paginated("Apps")

        assert result == []


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


class TestGetDocumentsPaginatedFilters:
    @pytest.mark.asyncio
    async def test_no_filters_omits_filter_clause(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", filters=None)

        query: str = provider.http_client.execute_aql.call_args[0][0]
        assert "FILTER" not in query

    @pytest.mark.asyncio
    async def test_single_filter_added_to_query(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", filters={"isActive": True})

        call = provider.http_client.execute_aql.call_args
        query: str = call[0][0]
        bind_vars: dict = call[1]["bind_vars"]
        assert "FILTER" in query
        assert "doc.isActive == @fv0" in query
        assert bind_vars["fv0"] is True

    @pytest.mark.asyncio
    async def test_multiple_filters_joined_with_and(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated(
            "Apps", filters={"isActive": True, "type": "Confluence"}
        )

        query: str = provider.http_client.execute_aql.call_args[0][0]
        assert "AND" in query

    @pytest.mark.asyncio
    async def test_empty_filters_dict_omits_filter_clause(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", filters={})

        query: str = provider.http_client.execute_aql.call_args[0][0]
        assert "FILTER" not in query


# ---------------------------------------------------------------------------
# Sort tests
# ---------------------------------------------------------------------------


class TestGetDocumentsPaginatedSort:
    @pytest.mark.asyncio
    async def test_sort_field_added_to_query(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", sort_field="createdAt")

        query: str = provider.http_client.execute_aql.call_args[0][0]
        assert "SORT doc.createdAt ASC" in query

    @pytest.mark.asyncio
    async def test_no_sort_field_omits_sort_clause(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps")

        query: str = provider.http_client.execute_aql.call_args[0][0]
        assert "SORT" not in query


# ---------------------------------------------------------------------------
# Transaction tests
# ---------------------------------------------------------------------------


class TestGetDocumentsPaginatedTransaction:
    @pytest.mark.asyncio
    async def test_transaction_id_forwarded(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps", transaction="txn-abc")

        call = provider.http_client.execute_aql.call_args
        assert call[1]["txn_id"] == "txn-abc"

    @pytest.mark.asyncio
    async def test_no_transaction_passes_none(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        await provider.get_documents_paginated("Apps")

        call = provider.http_client.execute_aql.call_args
        assert call[1]["txn_id"] is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestGetDocumentsPaginatedErrorHandling:
    @pytest.mark.asyncio
    async def test_aql_exception_returns_empty_list(self, provider):
        provider.http_client.execute_aql = AsyncMock(
            side_effect=RuntimeError("ArangoDB unreachable")
        )

        result = await provider.get_documents_paginated("Apps")

        assert result == []
        provider.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_logged_with_collection_name(self, provider):
        provider.http_client.execute_aql = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )

        await provider.get_documents_paginated("MyCollection")

        log_call = provider.logger.error.call_args
        assert "MyCollection" in str(log_call)
