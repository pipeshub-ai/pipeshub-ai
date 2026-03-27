"""Unit tests for app.api.routes.search module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.routes.search import (
    SearchQuery,
    SearchRequest,
    SimilarDocumentQuery,
    get_config_service,
    get_graph_provider,
    get_retrieval_service,
    health_check,
    search,
)


# ---------------------------------------------------------------------------
# Pydantic model validation tests
# ---------------------------------------------------------------------------
class TestSearchQueryModel:
    """Tests for SearchQuery pydantic model."""

    def test_valid_query_minimal(self):
        sq = SearchQuery(query="hello world")
        assert sq.query == "hello world"
        assert sq.limit == 5
        assert sq.filters == {}

    def test_valid_query_with_all_fields(self):
        sq = SearchQuery(
            query="test",
            limit=10,
            filters={"kb": ["kb1", "kb2"]},
        )
        assert sq.query == "test"
        assert sq.limit == 10
        assert sq.filters == {"kb": ["kb1", "kb2"]}

    def test_limit_none_uses_default(self):
        sq = SearchQuery(query="q", limit=None)
        assert sq.limit is None

    def test_filters_none(self):
        sq = SearchQuery(query="q", filters=None)
        assert sq.filters is None

    def test_missing_query_raises(self):
        with pytest.raises(ValidationError):
            SearchQuery()

    def test_empty_query_allowed(self):
        sq = SearchQuery(query="")
        assert sq.query == ""


class TestSimilarDocumentQueryModel:
    """Tests for SimilarDocumentQuery pydantic model."""

    def test_valid_minimal(self):
        sdq = SimilarDocumentQuery(document_id="doc123")
        assert sdq.document_id == "doc123"
        assert sdq.limit == 5
        assert sdq.filters is None

    def test_all_fields(self):
        sdq = SimilarDocumentQuery(
            document_id="doc1", limit=20, filters={"type": ["pdf"]}
        )
        assert sdq.limit == 20
        assert sdq.filters == {"type": ["pdf"]}

    def test_missing_document_id_raises(self):
        with pytest.raises(ValidationError):
            SimilarDocumentQuery()


class TestSearchRequestModel:
    """Tests for SearchRequest pydantic model."""

    def test_valid(self):
        sr = SearchRequest(
            query="test", topK=10, filtersV1=[{"source": ["google"]}]
        )
        assert sr.query == "test"
        assert sr.topK == 10
        assert sr.filtersV1 == [{"source": ["google"]}]

    def test_default_topK(self):
        sr = SearchRequest(query="q", filtersV1=[])
        assert sr.topK == 20

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="q")  # missing filtersV1

        with pytest.raises(ValidationError):
            SearchRequest(filtersV1=[])  # missing query


# ---------------------------------------------------------------------------
# Dependency provider tests
# ---------------------------------------------------------------------------
class TestDependencyProviders:
    """Tests for dependency injection helper functions."""

    @pytest.mark.asyncio
    async def test_get_retrieval_service(self):
        mock_retrieval = MagicMock()
        mock_container = MagicMock()
        mock_container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        mock_request = MagicMock()
        mock_request.app.container = mock_container

        result = await get_retrieval_service(mock_request)
        assert result is mock_retrieval

    @pytest.mark.asyncio
    async def test_get_graph_provider(self):
        mock_graph = MagicMock()
        mock_container = MagicMock()
        mock_container.graph_provider = AsyncMock(return_value=mock_graph)

        mock_request = MagicMock()
        mock_request.app.container = mock_container

        result = await get_graph_provider(mock_request)
        assert result is mock_graph

    @pytest.mark.asyncio
    async def test_get_config_service(self):
        mock_config = MagicMock()
        mock_container = MagicMock()
        mock_container.config_service.return_value = mock_config

        mock_request = MagicMock()
        mock_request.app.container = mock_container

        result = await get_config_service(mock_request)
        assert result is mock_config


# ---------------------------------------------------------------------------
# Health check endpoint test
# ---------------------------------------------------------------------------
class TestHealthCheck:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy(self):
        result = await health_check()
        assert result == {"status": "healthy"}


# ---------------------------------------------------------------------------
# Search endpoint tests
# ---------------------------------------------------------------------------
class TestSearchEndpoint:
    """Tests for the POST /search endpoint."""

    def _build_request(self, user_id="user1", org_id="org1"):
        """Create a mock request with user state and container."""
        mock_request = MagicMock()
        mock_request.state.user = {"userId": user_id, "orgId": org_id}

        mock_logger = MagicMock()
        mock_container = MagicMock()
        mock_container.logger.return_value = mock_logger
        mock_request.app.container = mock_container
        return mock_request

    def _make_chain(self, return_value):
        """Create a mock chain whose .ainvoke() returns the given value."""
        chain = MagicMock()
        chain.ainvoke = AsyncMock(return_value=return_value)
        return chain

    @pytest.mark.asyncio
    async def test_search_no_kb_filters(self):
        """Search without KB filtering should skip KB access validation."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [], "status_code": 200}
        )

        mock_graph = MagicMock()
        body = SearchQuery(query="hello", filters={})

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("rewritten hello"),
                self._make_chain("expanded hello"),
            )

            response = await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        mock_graph.validate_user_kb_access.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_with_kb_filter_all_accessible(self):
        """Search with KB filter - all KBs accessible."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": ["r1"], "status_code": 200}
        )

        mock_graph = MagicMock()
        mock_graph.validate_user_kb_access = AsyncMock(
            return_value={"accessible": ["kb1", "kb2"], "inaccessible": []}
        )

        body = SearchQuery(query="hello", filters={"kb": ["kb1", "kb2"]})

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("rewritten"),
                self._make_chain("expanded1\nexpanded2"),
            )

            response = await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        assert response.status_code == 200
        # Verify the filter_groups passed to search_with_filters use accessible KBs
        call_kwargs = mock_retrieval.search_with_filters.call_args
        assert call_kwargs.kwargs["filter_groups"]["kb"] == ["kb1", "kb2"]

    @pytest.mark.asyncio
    async def test_search_llm_none_and_get_fails(self):
        """When LLM is None and get_llm_instance also returns None, raise 500."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = None
        mock_retrieval.get_llm_instance = AsyncMock(return_value=None)

        mock_graph = MagicMock()
        body = SearchQuery(query="hello")

        with pytest.raises(HTTPException) as exc_info:
            await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )
        assert exc_info.value.status_code == 500
        assert "LLM" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_search_llm_none_then_initialised(self):
        """When LLM is initially None but get_llm_instance succeeds."""
        request = self._build_request()
        new_llm = MagicMock()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = None
        mock_retrieval.get_llm_instance = AsyncMock(return_value=new_llm)
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [], "status_code": 200}
        )

        mock_graph = MagicMock()
        body = SearchQuery(query="test")

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("rewritten"),
                self._make_chain("exp"),
            )

            response = await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        assert response.status_code == 200
        mock_setup.assert_called_once_with(new_llm)

    @pytest.mark.asyncio
    async def test_search_exception_raises_http_500(self):
        """Any unexpected exception in search is wrapped in HTTPException 500."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()

        mock_graph = MagicMock()
        body = SearchQuery(query="test")

        with patch(
            "app.api.routes.search.setup_query_transformation",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await search(
                    request=request,
                    body=body,
                    retrieval_service=mock_retrieval,
                    graph_provider=mock_graph,
                )
            assert exc_info.value.status_code == 500
            assert "boom" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_search_query_transformation_deduplicates(self):
        """Expanded queries already in rewritten are not duplicated."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [], "status_code": 200}
        )
        mock_graph = MagicMock()
        body = SearchQuery(query="test")

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            # rewritten and first expanded are same
            mock_setup.return_value = (
                self._make_chain("same query"),
                self._make_chain("same query\ndifferent query"),
            )

            await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        call_kwargs = mock_retrieval.search_with_filters.call_args
        queries = call_kwargs.kwargs["queries"]
        assert queries == ["same query", "different query"]

    @pytest.mark.asyncio
    async def test_search_empty_rewritten_query(self):
        """If rewritten query is blank, only expanded queries are used."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [], "status_code": 200}
        )
        mock_graph = MagicMock()
        body = SearchQuery(query="test")

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("   "),
                self._make_chain("q1\nq2"),
            )

            await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        call_kwargs = mock_retrieval.search_with_filters.call_args
        queries = call_kwargs.kwargs["queries"]
        assert queries == ["q1", "q2"]

    @pytest.mark.asyncio
    async def test_search_custom_status_code_from_results(self):
        """The status code from search results is forwarded in the response."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [], "status_code": 206}
        )
        mock_graph = MagicMock()
        body = SearchQuery(query="test")

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("r"),
                self._make_chain("e"),
            )

            response = await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        assert response.status_code == 206

    @pytest.mark.asyncio
    async def test_search_default_status_code_when_missing(self):
        """When results lack status_code, default to 500."""
        request = self._build_request()

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": []}
        )
        mock_graph = MagicMock()
        body = SearchQuery(query="test")

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("r"),
                self._make_chain("e"),
            )

            response = await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_search_passes_correct_params_to_retrieval(self):
        """Verify org_id, user_id, limit, knowledge_search are forwarded."""
        request = self._build_request(user_id="u42", org_id="o99")

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [], "status_code": 200}
        )
        mock_graph = MagicMock()
        body = SearchQuery(query="q", limit=7, filters={"type": ["file"]})

        with patch(
            "app.api.routes.search.setup_query_transformation"
        ) as mock_setup:
            mock_setup.return_value = (
                self._make_chain("r"),
                self._make_chain("e"),
            )

            await search(
                request=request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        call_kwargs = mock_retrieval.search_with_filters.call_args.kwargs
        assert call_kwargs["org_id"] == "o99"
        assert call_kwargs["user_id"] == "u42"
        assert call_kwargs["limit"] == 7
        assert call_kwargs["knowledge_search"] is True
        assert call_kwargs["filter_groups"] == {"type": ["file"]}
