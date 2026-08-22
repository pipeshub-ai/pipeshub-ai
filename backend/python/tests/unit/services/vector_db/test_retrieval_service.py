"""Unit tests for the updated RetrievalService (capability-aware hybrid search)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip if langchain_core is not installed (CI env without full deps)
pytest.importorskip("langchain_core", reason="langchain_core not installed")

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FusionMethod,
    HybridSearchRequest,
    SearchResult,
    VectorDBCapabilities,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vector_db_service(supports_sparse=True, supports_text=False):
    """Create a mock IVectorDBService with configurable capabilities."""
    svc = AsyncMock()
    caps = VectorDBCapabilities(
        supports_sparse_vectors=supports_sparse,
        supports_server_side_text_search=supports_text,
    )
    # get_capabilities is synchronous in the interface — use MagicMock not AsyncMock
    svc.get_capabilities = MagicMock(return_value=caps)
    svc.get_service_name = MagicMock(return_value="mock")
    svc.filter_collection = AsyncMock(return_value=FilterExpression())
    svc.query_nearest_points = AsyncMock(return_value=[[]])
    return svc


def _make_retrieval_service(vector_db_service, with_graph_provider=None):
    """Build a RetrievalService using mock dependencies."""
    import asyncio
    from app.modules.retrieval.retrieval_service import RetrievalService

    service = RetrievalService.__new__(RetrievalService)
    service.logger = MagicMock()
    service.config_service = AsyncMock()
    service.llm = None
    service.graph_provider = with_graph_provider or AsyncMock()
    service.blob_store = AsyncMock()
    service.vector_db_service = vector_db_service
    service.collection_name = "records"
    service._capabilities = vector_db_service.get_capabilities()
    service._sparse_embedder = None
    service._sparse_embedder_lock = asyncio.Lock()
    service.embedding_model = None
    service.embedding_model_instance = None
    service.embedding_size = None
    return service


# ---------------------------------------------------------------------------
# _execute_parallel_searches
# ---------------------------------------------------------------------------


class TestExecuteParallelSearches:
    @pytest.mark.asyncio
    async def test_text_query_always_populated(self):
        """text_query must be set in HybridSearchRequest for all providers."""
        svc = _make_vector_db_service(supports_sparse=False, supports_text=True)
        rs = _make_retrieval_service(svc)
        rs.embedding_model_instance = AsyncMock()
        rs.embedding_model_instance.aembed_query = AsyncMock(return_value=[0.1, 0.2])

        # Patch get_embedding_model_instance to return the mock
        rs.get_embedding_model_instance = AsyncMock(return_value=rs.embedding_model_instance)

        await rs._execute_parallel_searches(
            queries=["What is AI?"],
            filter=FilterExpression(),
            limit=5,
        )

        # Verify query_nearest_points was called
        svc.query_nearest_points.assert_awaited_once()
        call_args = svc.query_nearest_points.call_args
        requests = call_args.kwargs.get("requests") or call_args.args[1]
        assert len(requests) == 1
        req = requests[0]
        assert req.text_query == "What is AI?"

    @pytest.mark.asyncio
    async def test_sparse_not_computed_for_text_only_provider(self):
        """sparse_query must be None when provider does not support sparse vectors."""
        svc = _make_vector_db_service(supports_sparse=False, supports_text=True)
        rs = _make_retrieval_service(svc)
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1])
        rs.get_embedding_model_instance = AsyncMock(return_value=mock_embed)

        await rs._execute_parallel_searches(["test"], FilterExpression(), 5)

        requests = svc.query_nearest_points.call_args.args[1] if svc.query_nearest_points.call_args.args else svc.query_nearest_points.call_args.kwargs.get("requests")
        assert requests[0].sparse_query is None

    @pytest.mark.asyncio
    async def test_sparse_computed_for_sparse_capable_provider(self):
        """sparse_query must be set when provider supports sparse vectors."""
        svc = _make_vector_db_service(supports_sparse=True, supports_text=False)
        rs = _make_retrieval_service(svc)

        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1])
        rs.get_embedding_model_instance = AsyncMock(return_value=mock_embed)

        # Provide fake SparseEmbedder (new API uses async embed_query)
        from app.services.vector_db.models import SparseVector
        from app.services.vector_db.sparse_embeddings import SparseEmbedder
        fake_embedder = MagicMock(spec=SparseEmbedder)
        fake_embedder.embed_query = AsyncMock(return_value=SparseVector(indices=[0], values=[1.0]))
        rs._sparse_embedder = fake_embedder
        rs._capabilities = VectorDBCapabilities(
            supports_sparse_vectors=True,
            supports_server_side_text_search=False,
        )

        await rs._execute_parallel_searches(["test"], FilterExpression(), 5)

        requests = svc.query_nearest_points.call_args.args[1] if svc.query_nearest_points.call_args.args else svc.query_nearest_points.call_args.kwargs.get("requests")
        assert requests[0].sparse_query is not None

    @pytest.mark.asyncio
    async def test_search_results_deduped_by_id(self):
        """Duplicate point IDs from multiple queries should appear only once."""
        svc = _make_vector_db_service(supports_sparse=False, supports_text=True)
        dup_result = SearchResult(
            id="shared-1", score=0.9,
            payload={"page_content": "hello", "metadata": {"virtualRecordId": "vr1"}}
        )
        svc.query_nearest_points = AsyncMock(return_value=[[dup_result], [dup_result]])

        rs = _make_retrieval_service(svc)
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1])
        rs.get_embedding_model_instance = AsyncMock(return_value=mock_embed)

        results = await rs._execute_parallel_searches(["q1", "q2"], FilterExpression(), 10)
        # De-dup: even though 2 batches return the same ID, only one result
        ids = [r["metadata"]["point_id"] for r in results]
        assert len(set(ids)) == len(ids)

    @pytest.mark.asyncio
    async def test_query_nearest_points_is_awaited(self):
        """Must await query_nearest_points — not call it synchronously."""
        svc = _make_vector_db_service(supports_sparse=False, supports_text=True)
        rs = _make_retrieval_service(svc)
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1])
        rs.get_embedding_model_instance = AsyncMock(return_value=mock_embed)

        await rs._execute_parallel_searches(["q"], FilterExpression(), 3)

        # Must be awaited, not just called
        svc.query_nearest_points.assert_awaited()

    @pytest.mark.asyncio
    async def test_qdrant_path_sparse_query_populated(self):
        """P0 regression: Qdrant path must produce sparse_query and no AttributeError.

        Before the fix, retrieval_service called embed_query on a fastembed
        SparseTextEmbedding object which does not have that method — causing
        AttributeError: 'SparseTextEmbedding' object has no attribute 'embed_query'.
        """
        svc = _make_vector_db_service(supports_sparse=True, supports_text=False)
        rs = _make_retrieval_service(svc)

        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1, 0.2])
        rs.get_embedding_model_instance = AsyncMock(return_value=mock_embed)

        from app.services.vector_db.models import SparseVector
        from app.services.vector_db.sparse_embeddings import SparseEmbedder
        fake_embedder = MagicMock(spec=SparseEmbedder)
        fake_embedder.embed_query = AsyncMock(
            return_value=SparseVector(indices=[1, 2], values=[0.8, 0.2])
        )
        rs._sparse_embedder = fake_embedder

        # Must not raise AttributeError
        await rs._execute_parallel_searches(
            queries=["What is machine learning?"],
            filter=FilterExpression(),
            limit=5,
        )

        requests = (
            svc.query_nearest_points.call_args.kwargs.get("requests")
            or svc.query_nearest_points.call_args.args[1]
        )
        req = requests[0]
        assert req.sparse_query is not None, "sparse_query must be populated for Qdrant provider"
        assert req.sparse_query.indices == [1, 2]
        assert req.text_query is None, "text_query must be None for sparse-only provider"

    @pytest.mark.asyncio
    async def test_server_side_text_provider_skips_client_sparse(self):
        """For OpenSearch/Redis (text=True, sparse=False): no SparseEmbedder call;
        text_query is set; sparse_query is None."""
        svc = _make_vector_db_service(supports_sparse=False, supports_text=True)
        rs = _make_retrieval_service(svc)

        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.5, 0.3])
        rs.get_embedding_model_instance = AsyncMock(return_value=mock_embed)

        await rs._execute_parallel_searches(
            queries=["find documents"],
            filter=FilterExpression(),
            limit=5,
        )

        requests = (
            svc.query_nearest_points.call_args.kwargs.get("requests")
            or svc.query_nearest_points.call_args.args[1]
        )
        req = requests[0]
        assert req.sparse_query is None, "No client-side sparse for text-search providers"
        assert req.text_query == "find documents"
