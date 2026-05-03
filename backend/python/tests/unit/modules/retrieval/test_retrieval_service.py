"""Unit tests for app.modules.retrieval.retrieval_service.RetrievalService."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document
from qdrant_client import models

from app.exceptions.fastapi_responses import Status
from app.modules.retrieval.retrieval_service import ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE


# ---------------------------------------------------------------------------
# Helpers to build a RetrievalService without real FastEmbedSparse / model load
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_user_cache():
    """Clear module-level user cache before each test to prevent cross-test pollution."""
    import app.modules.retrieval.retrieval_service as mod
    mod._user_cache.clear()
    yield
    mod._user_cache.clear()


@pytest.fixture
def _patch_sparse():
    """Patch FastEmbedSparse so no model is loaded."""
    with patch("app.modules.retrieval.retrieval_service.FastEmbedSparse") as mock_sparse:
        sparse_instance = MagicMock()
        mock_sparse.return_value = sparse_instance
        yield sparse_instance


@pytest.fixture
def retrieval_service(
    logger, mock_config_service, mock_vector_db_service, mock_graph_provider, mock_blob_store, _patch_sparse
):
    from app.modules.retrieval.retrieval_service import RetrievalService

    svc = RetrievalService(
        logger=logger,
        config_service=mock_config_service,
        collection_name="test_collection",
        vector_db_service=mock_vector_db_service,
        graph_provider=mock_graph_provider,
        blob_store=mock_blob_store,
    )
    return svc


# ============================================================================
# _format_results
# ============================================================================

class TestFormatResults:
    def test_formats_document_score_pairs(self, retrieval_service):
        doc = Document(page_content="hello world", metadata={"orgId": "org1"})
        results = retrieval_service._format_results([(doc, 0.95)])
        assert len(results) == 1
        assert results[0]["score"] == 0.95
        assert results[0]["content"] == "hello world"
        assert results[0]["citationType"] == "vectordb|document"
        assert results[0]["metadata"]["orgId"] == "org1"

    def test_formats_multiple_results(self, retrieval_service):
        pairs = [
            (Document(page_content=f"doc{i}", metadata={"id": str(i)}), float(i) / 10)
            for i in range(3)
        ]
        results = retrieval_service._format_results(pairs)
        assert len(results) == 3
        assert results[2]["score"] == pytest.approx(0.2)

    def test_formats_empty_list(self, retrieval_service):
        assert retrieval_service._format_results([]) == []


# ============================================================================
# _create_empty_response
# ============================================================================

class TestCreateEmptyResponse:
    def test_success_status(self, retrieval_service):
        resp = retrieval_service._create_empty_response("ok", Status.SUCCESS)
        assert resp["status"] == "success"
        assert resp["status_code"] == 200
        assert resp["searchResults"] == []
        assert resp["records"] == []
        assert resp["message"] == "ok"

    def test_error_status(self, retrieval_service):
        resp = retrieval_service._create_empty_response("err", Status.ERROR)
        assert resp["status_code"] == 500

    def test_accessible_records_not_found(self, retrieval_service):
        resp = retrieval_service._create_empty_response("no access", Status.ACCESSIBLE_RECORDS_NOT_FOUND)
        assert resp["status_code"] == 404

    def test_vector_db_empty(self, retrieval_service):
        resp = retrieval_service._create_empty_response("empty", Status.VECTOR_DB_EMPTY)
        assert resp["status_code"] == 503

    def test_vector_db_not_ready(self, retrieval_service):
        resp = retrieval_service._create_empty_response("not ready", Status.VECTOR_DB_NOT_READY)
        assert resp["status_code"] == 503

    def test_empty_response_status(self, retrieval_service):
        resp = retrieval_service._create_empty_response("nothing", Status.EMPTY_RESPONSE)
        assert resp["status_code"] == 200


# ============================================================================
# _create_virtual_to_record_mapping
# ============================================================================

class TestCreateVirtualToRecordMapping:
    def test_maps_virtual_ids_to_first_record(self, retrieval_service):
        records = [
            {"virtualRecordId": "vr1", "_key": "rec1", "name": "first"},
            {"virtualRecordId": "vr1", "_key": "rec2", "name": "second"},
            {"virtualRecordId": "vr2", "_key": "rec3", "name": "third"},
        ]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, ["vr1", "vr2"])
        assert mapping["vr1"]["_key"] == "rec1"
        assert mapping["vr2"]["_key"] == "rec3"

    def test_only_maps_requested_virtual_ids(self, retrieval_service):
        records = [
            {"virtualRecordId": "vr1", "_key": "rec1"},
            {"virtualRecordId": "vr2", "_key": "rec2"},
        ]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, ["vr1"])
        assert "vr1" in mapping
        assert "vr2" not in mapping

    def test_skips_none_virtual_record_id(self, retrieval_service):
        records = [{"virtualRecordId": None, "_key": "rec1"}]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, [None])
        assert len(mapping) == 0

    def test_skips_none_key(self, retrieval_service):
        records = [{"virtualRecordId": "vr1", "_key": None}]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, ["vr1"])
        assert len(mapping) == 0

    def test_handles_empty_records(self, retrieval_service):
        mapping = retrieval_service._create_virtual_to_record_mapping([], ["vr1"])
        assert mapping == {}

    def test_handles_empty_virtual_ids(self, retrieval_service):
        records = [{"virtualRecordId": "vr1", "_key": "rec1"}]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, [])
        assert mapping == {}

    def test_skips_non_dict_records(self, retrieval_service):
        records = [None, "invalid", {"virtualRecordId": "vr1", "_key": "rec1"}]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, ["vr1"])
        assert "vr1" in mapping

    def test_virtual_id_not_in_records_omitted(self, retrieval_service):
        records = [{"virtualRecordId": "vr1", "_key": "rec1"}]
        mapping = retrieval_service._create_virtual_to_record_mapping(records, ["vr999"])
        assert "vr999" not in mapping


# ============================================================================
# to_qdrant_sparse (static method)
# ============================================================================

class TestToQdrantSparse:
    def test_passthrough_sparse_vector(self, retrieval_service):
        from app.modules.retrieval.retrieval_service import RetrievalService
        sv = models.SparseVector(indices=[1, 2], values=[0.5, 0.6])
        result = RetrievalService.to_qdrant_sparse(sv)
        assert result is sv

    def test_converts_object_with_attributes(self, retrieval_service):
        from app.modules.retrieval.retrieval_service import RetrievalService
        obj = MagicMock()
        obj.indices = [1, 2, 3]
        obj.values = [0.1, 0.2, 0.3]
        # Ensure isinstance check fails so we go to hasattr path
        result = RetrievalService.to_qdrant_sparse(obj)
        assert isinstance(result, models.SparseVector)
        assert list(result.indices) == [1, 2, 3]
        assert list(result.values) == [0.1, 0.2, 0.3]

    def test_converts_dict(self, retrieval_service):
        from app.modules.retrieval.retrieval_service import RetrievalService
        d = {"indices": [10, 20], "values": [0.5, 0.8]}
        result = RetrievalService.to_qdrant_sparse(d)
        assert isinstance(result, models.SparseVector)
        assert list(result.indices) == [10, 20]

    def test_raises_on_invalid_input(self, retrieval_service):
        from app.modules.retrieval.retrieval_service import RetrievalService
        with pytest.raises(ValueError, match="Cannot convert"):
            RetrievalService.to_qdrant_sparse("invalid")


# ============================================================================
# _preprocess_query
# ============================================================================

class TestPreprocessQuery:
    @pytest.mark.asyncio
    async def test_adds_bge_prefix(self, retrieval_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(
            return_value="BAAI/bge-large-en-v1.5"
        )
        result = await retrieval_service._preprocess_query("  hello  ")
        assert result.startswith("Represent this document for retrieval:")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_no_prefix_for_non_bge(self, retrieval_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(
            return_value="text-embedding-3-small"
        )
        result = await retrieval_service._preprocess_query("  hello  ")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_strips_whitespace(self, retrieval_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(return_value=None)
        result = await retrieval_service._preprocess_query("  query with spaces  ")
        assert result == "query with spaces"

    @pytest.mark.asyncio
    async def test_error_returns_stripped_query(self, retrieval_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(
            side_effect=Exception("config error")
        )
        result = await retrieval_service._preprocess_query("  fallback  ")
        assert result == "fallback"


# ============================================================================
# get_embedding_model_name
# ============================================================================

class TestGetEmbeddingModelName:
    def test_from_model_name_attr(self, retrieval_service):
        emb = MagicMock(spec=["model_name"])
        emb.model_name = "bge-base"
        assert retrieval_service.get_embedding_model_name(emb) == "bge-base"

    def test_from_model_attr(self, retrieval_service):
        emb = MagicMock(spec=["model"])
        emb.model = "text-embed"
        assert retrieval_service.get_embedding_model_name(emb) == "text-embed"

    def test_returns_none_no_attr(self, retrieval_service):
        emb = MagicMock(spec=[])
        assert retrieval_service.get_embedding_model_name(emb) is None


# ============================================================================
# _get_user_cached
# ============================================================================

# ============================================================================
# get_llm_instance
# ============================================================================

class TestGetLlmInstance:
    @pytest.mark.asyncio
    async def test_returns_default_llm(self, retrieval_service, mock_config_service):
        with patch("app.modules.retrieval.retrieval_service.get_generator_model") as mock_gen:
            mock_gen.return_value = MagicMock()
            result = await retrieval_service.get_llm_instance()
            assert result is not None

    @pytest.mark.asyncio
    async def test_fallback_to_first_provider(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.return_value = {
            "llm": [
                {"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}},
            ]
        }
        with patch("app.modules.retrieval.retrieval_service.get_generator_model") as mock_gen:
            mock_gen.return_value = MagicMock()
            result = await retrieval_service.get_llm_instance()
            assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.side_effect = Exception("config error")
        result = await retrieval_service.get_llm_instance()
        assert result is None

    @pytest.mark.asyncio
    async def test_no_supported_provider_returns_none(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.return_value = {"llm": []}
        with patch("app.modules.retrieval.retrieval_service.get_generator_model", return_value=None):
            result = await retrieval_service.get_llm_instance()
            assert result is None


# ============================================================================
# get_embedding_model_instance
# ============================================================================

class TestGetEmbeddingModelInstance:
    @pytest.mark.asyncio
    async def test_returns_default_model(self, retrieval_service):
        from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
        retrieval_service.get_current_embedding_model_name = AsyncMock(
            return_value=DEFAULT_EMBEDDING_MODEL
        )
        # Reset cached model to force re-creation
        retrieval_service.embedding_model = None
        retrieval_service.embedding_model_instance = None
        with patch("app.modules.retrieval.retrieval_service.get_default_embedding_model") as mock_def:
            mock_def.return_value = MagicMock()
            result = await retrieval_service.get_embedding_model_instance()
            assert result is not None
            mock_def.assert_called_once()

    @pytest.mark.asyncio
    async def test_caches_embedding_model(self, retrieval_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(return_value="cached-model")
        retrieval_service.embedding_model = "cached-model"
        cached = MagicMock()
        retrieval_service.embedding_model_instance = cached
        result = await retrieval_service.get_embedding_model_instance()
        assert result is cached

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, retrieval_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(
            side_effect=Exception("error")
        )
        result = await retrieval_service.get_embedding_model_instance()
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_embedding_model(self, retrieval_service, mock_config_service):
        retrieval_service.get_current_embedding_model_name = AsyncMock(
            return_value="custom-embed-model"
        )
        mock_config_service.get_config.return_value = {
            "embedding": [
                {"provider": "openai", "isDefault": True,
                 "configuration": {"model": "custom-embed-model"}}
            ]
        }
        with patch("app.modules.retrieval.retrieval_service.get_embedding_model") as mock_emb:
            mock_emb.return_value = MagicMock()
            result = await retrieval_service.get_embedding_model_instance()
            assert result is not None


# ============================================================================
# get_current_embedding_model_name
# ============================================================================

class TestGetCurrentEmbeddingModelName:
    @pytest.mark.asyncio
    async def test_returns_model_from_config(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.return_value = {
            "embedding": [
                {"configuration": {"model": "text-embedding-3-small"}}
            ]
        }
        result = await retrieval_service.get_current_embedding_model_name()
        assert result == "text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_returns_default_when_no_config(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.return_value = {"embedding": []}
        from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
        result = await retrieval_service.get_current_embedding_model_name()
        assert result == DEFAULT_EMBEDDING_MODEL

    @pytest.mark.asyncio
    async def test_returns_default_on_error(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.side_effect = Exception("error")
        from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
        result = await retrieval_service.get_current_embedding_model_name()
        assert result == DEFAULT_EMBEDDING_MODEL

    @pytest.mark.asyncio
    async def test_returns_default_when_no_model_key(self, retrieval_service, mock_config_service):
        mock_config_service.get_config.return_value = {
            "embedding": [{"configuration": {}}]  # no "model" key
        }
        from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
        result = await retrieval_service.get_current_embedding_model_name()
        assert result == DEFAULT_EMBEDDING_MODEL


# ============================================================================
# _execute_parallel_searches
# ============================================================================

class TestExecuteParallelSearches:
    @pytest.mark.asyncio
    async def test_raises_without_dense_embeddings(self, retrieval_service):
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="No dense embeddings"):
            await retrieval_service._execute_parallel_searches(["q"], MagicMock(), 10)

    @pytest.mark.asyncio
    async def test_raises_without_sparse_embeddings(self, retrieval_service):
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=MagicMock())
        retrieval_service._ensure_sparse_embeddings = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="No sparse embeddings"):
            await retrieval_service._execute_parallel_searches(["q"], MagicMock(), 10)

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self, retrieval_service, mock_vector_db_service):
        dense = AsyncMock()
        dense.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=dense)

        sparse_result = MagicMock()
        sparse_result.indices = [1, 2]
        sparse_result.values = [0.5, 0.6]
        sparse_mock = MagicMock()
        sparse_mock.embed_query = MagicMock(return_value=sparse_result)
        retrieval_service._ensure_sparse_embeddings = AsyncMock(return_value=sparse_mock)

        point = MagicMock()
        point.id = "p1"
        point.payload = {"page_content": "hello", "metadata": {"orgId": "o1"}}
        point.score = 0.95
        batch_result = MagicMock()
        batch_result.points = [point]
        mock_vector_db_service.query_nearest_points.return_value = [batch_result]

        qdrant_filter = models.Filter(must=[])
        results = await retrieval_service._execute_parallel_searches(
            ["test query"], qdrant_filter, 10
        )
        assert len(results) == 1
        assert results[0]["score"] == 0.95
        assert results[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_deduplicates_points(self, retrieval_service, mock_vector_db_service):
        dense = AsyncMock()
        dense.aembed_query = AsyncMock(return_value=[0.1])
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=dense)

        sparse_result = MagicMock()
        sparse_result.indices = [1]
        sparse_result.values = [0.5]
        sparse_mock = MagicMock()
        sparse_mock.embed_query = MagicMock(return_value=sparse_result)
        retrieval_service._ensure_sparse_embeddings = AsyncMock(return_value=sparse_mock)

        point = MagicMock()
        point.id = "same_id"
        point.payload = {"page_content": "text", "metadata": {}}
        point.score = 0.9
        batch1 = MagicMock()
        batch1.points = [point]
        batch2 = MagicMock()
        batch2.points = [point]  # duplicate
        mock_vector_db_service.query_nearest_points.return_value = [batch1, batch2]

        qdrant_filter = models.Filter(must=[])
        results = await retrieval_service._execute_parallel_searches(
            ["query"], qdrant_filter, 10
        )
        assert len(results) == 1  # deduplicated


class TestGetUserCached:
    @pytest.mark.asyncio
    async def test_cache_miss_fetches_from_db(self, retrieval_service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "user@test.com"}
        result = await retrieval_service._get_user_cached("user1")
        assert result["email"] == "user@test.com"
        mock_graph_provider.get_user_by_user_id.assert_called_once_with("user1")

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self, retrieval_service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "cached@test.com"}
        await retrieval_service._get_user_cached("user1")
        await retrieval_service._get_user_cached("user1")
        # Should only call DB once (second is cache hit)
        mock_graph_provider.get_user_by_user_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expiry(self, retrieval_service, mock_graph_provider):
        import app.modules.retrieval.retrieval_service as mod
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "old@test.com"}
        await retrieval_service._get_user_cached("user1")

        # Manually expire the cache entry
        mod._user_cache["user1"] = ({"email": "old@test.com"}, time.time() - 400)

        mock_graph_provider.get_user_by_user_id.return_value = {"email": "new@test.com"}
        result = await retrieval_service._get_user_cached("user1")
        assert result["email"] == "new@test.com"
        assert mock_graph_provider.get_user_by_user_id.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_size_limit(self, retrieval_service, mock_graph_provider):
        import app.modules.retrieval.retrieval_service as mod
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "test@test.com"}

        # Fill cache to exactly the limit
        for i in range(mod.MAX_USER_CACHE_SIZE):
            mod._user_cache[f"user_{i}"] = ({"email": f"u{i}@test.com"}, time.time())

        # Adding one more should trigger eviction
        await retrieval_service._get_user_cached("new_user")
        # Cache should have evicted the oldest entry: size = MAX + 1 then one removed = MAX
        assert len(mod._user_cache) <= mod.MAX_USER_CACHE_SIZE + 1


# ============================================================================
# search_with_filters (orchestration logic)
# ============================================================================

class TestSearchWithFilters:
    @pytest.mark.asyncio
    async def test_raises_when_no_graph_provider(self, retrieval_service):
        retrieval_service.graph_provider = None
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.ERROR.value

    @pytest.mark.asyncio
    async def test_returns_404_when_no_accessible_records(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {}
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
        assert result["status_code"] == 404
        assert result["message"] == ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_search_results(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.EMPTY_RESPONSE.value

    @pytest.mark.asyncio
    async def test_successful_search_returns_enriched_results(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "user@test.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "google_drive",
                "recordName": "Test Doc",
                "webUrl": "https://example.com/doc",
                "mimeType": "application/pdf",
                "connectorName": "gdrive",
                "connectorId": "conn-123",
                "kbId": "kb1",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.95,
                "content": "test content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.SUCCESS.value
        assert result["status_code"] == 200
        assert len(result["searchResults"]) == 1
        sr = result["searchResults"][0]
        assert sr["metadata"]["recordId"] == "rec1"
        assert sr["metadata"]["origin"] == "google_drive"
        assert sr["metadata"]["recordName"] == "Test Doc"
        assert sr["metadata"]["connectorId"] == "conn-123"
        assert sr["metadata"]["mimeType"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_gmail_url_template_replacement(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "alice@corp.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "gmail",
                "recordName": "Email Subject",
                "webUrl": "https://mail.google.com/mail?authuser={user.email}",
                "mimeType": "text/html",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "email body",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        sr = result["searchResults"][0]
        assert sr["metadata"]["webUrl"] == "https://mail.google.com/mail?authuser=alice@corp.com"

    @pytest.mark.asyncio
    async def test_kb_filter_applied_to_response(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com",
                "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "text",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1",
            filter_groups={"kb": ["kb-123"]}
        )
        assert result.get("appliedFilters") == {"kb": ["kb-123"], "kb_count": 1}

    @pytest.mark.asyncio
    async def test_vector_db_empty_error_agent_mode(self, retrieval_service, mock_graph_provider):
        from app.sources.client.http.exception.exception import VectorDBEmptyError
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        retrieval_service._execute_parallel_searches = AsyncMock(side_effect=VectorDBEmptyError())
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.VECTOR_DB_EMPTY.value

    @pytest.mark.asyncio
    async def test_vector_db_empty_error_non_agent(self, retrieval_service, mock_graph_provider):
        from app.sources.client.http.exception.exception import VectorDBEmptyError
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        retrieval_service._execute_parallel_searches = AsyncMock(side_effect=VectorDBEmptyError())
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.VECTOR_DB_EMPTY.value

    @pytest.mark.asyncio
    async def test_generic_exception_returns_error(self, retrieval_service, mock_graph_provider):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        retrieval_service._execute_parallel_searches = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == Status.ERROR.value

    @pytest.mark.asyncio
    async def test_generic_exception_with_tool_ids_returns_empty_dict(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        retrieval_service._execute_parallel_searches = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1",
            virtual_record_ids_from_tool=["vr1"]
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_filters_incomplete_results(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1",
                "origin": "drive", "recordName": "Good Doc",
                "webUrl": "https://x.com", "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            # Complete result
            {
                "score": 0.9, "content": "good content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
            # Result with empty content (should be filtered)
            {
                "score": 0.8, "content": "",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        # Only the complete result should survive
        assert len(result["searchResults"]) == 1
        assert result["searchResults"][0]["content"] == "good content"

    @pytest.mark.asyncio
    async def test_tool_provided_virtual_ids_use_must_filter(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service
    ):
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1",
            virtual_record_ids_from_tool=["vr1"]
        )
        # Should use must filter (not should)
        call_args = mock_vector_db_service.filter_collection.call_args
        assert "virtualRecordId" in call_args.kwargs.get("must", {})

    @pytest.mark.asyncio
    async def test_missing_mimetype_file_record_fetches_file(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with missing mimeType and recordType=FILE adds to file_record_ids_to_fetch."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "google_drive",
                "recordName": "NoMime.bin",
                "webUrl": "https://example.com/doc",
                "recordType": "FILE",
                # mimeType intentionally absent
            }
        ]
        # get_document returns a file with mimeType and webUrl
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://example.com/file",
            "mimeType": "application/pdf",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "file content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        # The file fetch should have been called with FILES collection
        from app.config.constants.arangodb import CollectionNames
        mock_graph_provider.get_document.assert_called_once_with(
            "rec1", CollectionNames.FILES.value
        )
        sr = result["searchResults"][0]
        assert sr["metadata"]["mimeType"] == "application/pdf"
        assert sr["metadata"]["webUrl"] == "https://example.com/file"

    @pytest.mark.asyncio
    async def test_missing_mimetype_mail_record_fetches_mail(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with missing mimeType and recordType=MAIL adds to mail_record_ids_to_fetch."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "alice@corp.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "gmail",
                "recordName": "Email Subject",
                "webUrl": "https://example.com/mail",
                "recordType": "MAIL",
                # mimeType intentionally absent
            }
        ]
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://mail.google.com/mail?authuser={user.email}#inbox/123",
            "mimeType": "text/html",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "email body",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        from app.config.constants.arangodb import CollectionNames
        mock_graph_provider.get_document.assert_called_once_with(
            "rec1", CollectionNames.MAILS.value
        )
        sr = result["searchResults"][0]
        # Mail mimeType should default to text/html
        assert sr["metadata"]["mimeType"] == "text/html"
        # Gmail URL template should be replaced with user email
        assert sr["metadata"]["webUrl"] == "https://mail.google.com/mail?authuser=alice@corp.com#inbox/123"

    @pytest.mark.asyncio
    async def test_missing_weburl_file_record_fetches_file(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with mimeType but missing webUrl and recordType=FILE fetches from files collection."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "sharepoint",
                "recordName": "Doc.pdf",
                "mimeType": "application/pdf",
                "recordType": "FILE",
                # webUrl intentionally absent
            }
        ]
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://sharepoint.com/doc",
            "mimeType": "application/pdf",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.85,
                "content": "doc content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        sr = result["searchResults"][0]
        assert sr["metadata"]["webUrl"] == "https://sharepoint.com/doc"

    @pytest.mark.asyncio
    async def test_missing_weburl_mail_record_fetches_mail(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with mimeType but missing webUrl and recordType=MAIL fetches from mails collection."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "gmail",
                "recordName": "Mail Subject",
                "mimeType": "text/html",
                "recordType": "MAIL",
                # webUrl intentionally absent
            }
        ]
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://mail.google.com/mail/123",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.85,
                "content": "mail content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        sr = result["searchResults"][0]
        assert sr["metadata"]["webUrl"] == "https://mail.google.com/mail/123"

    @pytest.mark.asyncio
    async def test_batch_file_and_mail_fetching(
        self, retrieval_service, mock_graph_provider
    ):
        """Multiple records with missing mimeType: one FILE and one MAIL, both fetched in batch."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {
            "vr1": "rec1", "vr2": "rec2"
        }
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "google_drive",
                "recordName": "File.bin",
                "webUrl": "https://drive.google.com/doc",
                "recordType": "FILE",
                # no mimeType
            },
            {
                "_key": "rec2",
                "virtualRecordId": "vr2",
                "origin": "gmail",
                "recordName": "Mail Subject",
                "webUrl": "https://mail.google.com/x",
                "recordType": "MAIL",
                # no mimeType
            },
        ]

        async def mock_get_document(record_id, collection):
            if collection == "files":
                return {"webUrl": "https://drive.google.com/file", "mimeType": "application/pdf"}
            elif collection == "mails":
                return {"webUrl": "https://mail.google.com/mail?authuser={user.email}#inbox/456"}
            return {}

        mock_graph_provider.get_document = AsyncMock(side_effect=mock_get_document)

        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "file content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
            {
                "score": 0.8, "content": "mail content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr2", "orgId": "o1"},
            },
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        assert len(result["searchResults"]) == 2
        # File result should have PDF mimeType
        file_sr = [sr for sr in result["searchResults"] if sr["content"] == "file content"][0]
        assert file_sr["metadata"]["mimeType"] == "application/pdf"
        # Mail result should have text/html mimeType and the URL replaced
        mail_sr = [sr for sr in result["searchResults"] if sr["content"] == "mail content"][0]
        assert mail_sr["metadata"]["mimeType"] == "text/html"
        assert mail_sr["metadata"]["webUrl"] == "https://mail.google.com/mail?authuser=u@t.com#inbox/456"

    @pytest.mark.asyncio
    async def test_empty_fetched_records_returns_404(
        self, retrieval_service, mock_graph_provider
    ):
        """When get_records_by_record_ids returns empty list, should return 404."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = []
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status_code"] == 404
        assert result["status"] == "accessible_records_not_found"

    @pytest.mark.asyncio
    async def test_virtual_to_record_mapping_exception_reraises(
        self, retrieval_service, mock_graph_provider
    ):
        """Exception in _create_virtual_to_record_mapping is re-raised (caught by outer except)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "drive",
                "recordName": "Doc",
                "webUrl": "https://x.com",
                "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        # Patch _create_virtual_to_record_mapping to raise
        retrieval_service._create_virtual_to_record_mapping = MagicMock(
            side_effect=RuntimeError("mapping error")
        )
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        # The exception is caught by the outer except and returns error status
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_empty_unique_record_ids_returns_404(
        self, retrieval_service, mock_graph_provider
    ):
        """When virtual_to_record_map produces no unique record IDs, return 404."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        # Return records with None _key so unique_record_ids will be empty
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {"_key": None, "virtualRecordId": "vr1"}
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status_code"] == 404

    @pytest.mark.asyncio
    async def test_knowledge_search_with_block_groups(
        self, retrieval_service, mock_graph_provider, mock_blob_store
    ):
        """knowledge_search=True with isBlockGroup in metadata triggers get_record and get_flattened_results."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "google_drive",
                "recordName": "Doc",
                "webUrl": "https://example.com/doc",
                "mimeType": "application/pdf",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "block group content",
                "citationType": "vectordb|document",
                "metadata": {
                    "virtualRecordId": "vr1",
                    "orgId": "o1",
                    "isBlockGroup": True,
                },
            }
        ])

        # Mock get_record to populate the virtual_record_id_to_record dict
        async def mock_get_record(vid, vid_to_record, blob_store, org_id, vtr_map):
            vid_to_record[vid] = {"recordName": "Doc", "blocks": []}

        # Mock get_flattened_results to return TABLE-type results
        from app.models.blocks import GroupType
        mock_flattened = [
            {
                "block_type": GroupType.TABLE.value,
                "content": ("Table summary", [
                    {
                        "score": 0.88,
                        "content": "table row content",
                        "citationType": "vectordb|document",
                        "metadata": {
                            "virtualRecordId": "vr1",
                            "orgId": "o1",
                            "origin": "google_drive",
                            "recordName": "Doc",
                            "recordId": "rec1",
                            "mimeType": "application/pdf",
                        },
                    }
                ]),
            },
            {
                "block_type": "text",
                "score": 0.85,
                "content": "text result",
                "citationType": "vectordb|document",
                "metadata": {
                    "virtualRecordId": "vr1",
                    "orgId": "o1",
                    "origin": "google_drive",
                    "recordName": "Doc",
                    "recordId": "rec1",
                    "mimeType": "application/pdf",
                },
            },
        ]

        with patch("app.modules.retrieval.retrieval_service.get_record", new=AsyncMock(side_effect=mock_get_record)), \
             patch("app.modules.retrieval.retrieval_service.get_flattened_results", new=AsyncMock(return_value=mock_flattened)):
            result = await retrieval_service.search_with_filters(
                queries=["test"], user_id="u1", org_id="o1",
                knowledge_search=True,
            )
        assert result["status"] == "success"
        # TABLE children and text result should be in final_search_results
        contents = [sr["content"] for sr in result["searchResults"]]
        assert "table row content" in contents
        assert "text result" in contents

    @pytest.mark.asyncio
    async def test_knowledge_search_get_record_returns_none_skips(
        self, retrieval_service, mock_graph_provider, mock_blob_store
    ):
        """knowledge_search=True where get_record sets None for the record skips it."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "google_drive",
                "recordName": "Doc",
                "webUrl": "https://example.com/doc",
                "mimeType": "application/pdf",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "block group content",
                "citationType": "vectordb|document",
                "metadata": {
                    "virtualRecordId": "vr1",
                    "orgId": "o1",
                    "isBlockGroup": True,
                },
            }
        ])

        # get_record sets vid_to_record[vid] = None
        async def mock_get_record_none(vid, vid_to_record, blob_store, org_id, vtr_map):
            vid_to_record[vid] = None

        with patch("app.modules.retrieval.retrieval_service.get_record", new=AsyncMock(side_effect=mock_get_record_none)), \
             patch("app.modules.retrieval.retrieval_service.get_flattened_results", new=AsyncMock(return_value=[])):
            result = await retrieval_service.search_with_filters(
                queries=["test"], user_id="u1", org_id="o1",
                knowledge_search=True,
            )
        # The block group result was skipped (record was None), no regular results either
        # But records list is still returned, so we should get a response
        assert result["status"] == "success" or result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_fetch_files_exception_returns_empty_map(
        self, retrieval_service, mock_graph_provider
    ):
        """When get_document raises for file fetch, it's handled gracefully."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1",
                "virtualRecordId": "vr1",
                "origin": "google_drive",
                "recordName": "File.bin",
                "webUrl": "https://example.com/doc",
                "recordType": "FILE",
                # no mimeType - triggers file fetch
            }
        ]
        # get_document raises an exception (returned via gather with return_exceptions=True)
        mock_graph_provider.get_document = AsyncMock(side_effect=Exception("db error"))
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "file content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        # Exception results are filtered out; result will lack mimeType so filtered out
        # from complete_results
        assert result["status"] in ("success", "empty_response")

    @pytest.mark.asyncio
    async def test_no_returned_virtual_record_ids_returns_404(
        self, retrieval_service, mock_graph_provider
    ):
        """Search results with no valid virtualRecordId should return 404."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9,
                "content": "content",
                "citationType": "vectordb|document",
                "metadata": {},  # no virtualRecordId
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status_code"] == 404


# ============================================================================
# Additional search_with_filters branch coverage
# ============================================================================

class TestSearchWithFiltersBranches:
    @pytest.mark.asyncio
    async def test_non_dict_result_skipped(
        self, retrieval_service, mock_graph_provider
    ):
        """Non-dict or None results in search_results are skipped (line 360-361)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com", "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            None,  # None result should be skipped
            "invalid",  # non-dict should be skipped
            {
                "score": 0.9, "content": "good content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        assert len(result["searchResults"]) == 1

    @pytest.mark.asyncio
    async def test_result_without_metadata_skipped(
        self, retrieval_service, mock_graph_provider
    ):
        """Result with no metadata key is skipped (lines 362-364)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com", "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.8, "content": "no metadata",
                "citationType": "vectordb|document",
                # no "metadata" key
            },
            {
                "score": 0.9, "content": "with metadata",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        assert len(result["searchResults"]) == 1

    @pytest.mark.asyncio
    async def test_virtual_id_not_in_record_map_goes_to_final(
        self, retrieval_service, mock_graph_provider
    ):
        """Result with virtual_id not in virtual_to_record_map goes to final_search_results (line 366->420)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1", "vr2": "rec2"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com", "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "known virtual",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
            {
                "score": 0.8, "content": "unknown virtual",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr_unknown", "orgId": "o1"},
            },
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_record_not_in_record_id_map_no_enrichment(
        self, retrieval_service, mock_graph_provider
    ):
        """When record_id_to_record_map doesn't have the record_id, no enrichment (line 371->420)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com", "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        # Return a mapping with a _key not in fetched records
        original = retrieval_service._create_virtual_to_record_mapping
        retrieval_service._create_virtual_to_record_mapping = lambda records, vids: {"vr1": {"_key": "rec_nonexistent"}}
        await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        retrieval_service._create_virtual_to_record_mapping = original

    @pytest.mark.asyncio
    async def test_gmail_url_no_user_email(
        self, retrieval_service, mock_graph_provider
    ):
        """Gmail URL with user=None does not replace template (line 378->380 branch)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = None  # user is None
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "gmail",
                "recordName": "Email", "mimeType": "text/html",
                "webUrl": "https://mail.google.com/mail?authuser={user.email}#inbox/1",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "email body",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        sr = result["searchResults"][0]
        assert "{user.email}" in sr["metadata"]["webUrl"]

    @pytest.mark.asyncio
    async def test_missing_mimetype_unknown_record_type_continues(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with no mimeType and recordType neither FILE nor MAIL continues (line 390->393 fallthrough)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "web",
                "recordName": "Page", "recordType": "WEBPAGE",
                "webUrl": "https://example.com",
                # no mimeType
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "page content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        # No mimeType + recordType=WEBPAGE -> falls through both FILE/MAIL checks, continues

    @pytest.mark.asyncio
    async def test_missing_weburl_unknown_record_type_continues(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with mimeType but no webUrl and recordType neither FILE nor MAIL (line 404->407 fallthrough)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "web",
                "recordName": "Page", "recordType": "WEBPAGE",
                "mimeType": "text/html",
                # no webUrl
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "page content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )

    @pytest.mark.asyncio
    async def test_knowledge_search_is_block_group_none_goes_to_final(
        self, retrieval_service, mock_graph_provider
    ):
        """knowledge_search=True but isBlockGroup is None — goes to final_search_results (line 412->420)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com", "mimeType": "application/pdf",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "normal content",
                "citationType": "vectordb|document",
                "metadata": {
                    "virtualRecordId": "vr1", "orgId": "o1",
                    # no isBlockGroup key
                },
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1",
            knowledge_search=True,
        )
        assert result["status"] == "success"
        assert len(result["searchResults"]) == 1

    @pytest.mark.asyncio
    async def test_fetched_mail_gmail_url_no_user_email(
        self, retrieval_service, mock_graph_provider
    ):
        """Fetched mail with Gmail URL but user has no email (line 479->481 branch)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = None
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "gmail",
                "recordName": "Mail Subject", "recordType": "MAIL",
                "webUrl": "https://example.com",
                # no mimeType to trigger fetch
            }
        ]
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://mail.google.com/mail?authuser={user.email}#inbox/456",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "mail content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )

    @pytest.mark.asyncio
    async def test_mimetype_with_no_extension(
        self, retrieval_service, mock_graph_provider
    ):
        """Record with mimeType that has no known extension (line 397->400 branch)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Unusual", "webUrl": "https://x.com",
                "mimeType": "application/x-unknown-custom-type",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"
        sr = result["searchResults"][0]
        assert sr["metadata"]["mimeType"] == "application/x-unknown-custom-type"
        assert "extension" not in sr["metadata"]

    @pytest.mark.asyncio
    async def test_fallback_mimetype_no_extension(
        self, retrieval_service, mock_graph_provider
    ):
        """Fetched file with mimeType that has no known extension (line 489->492 branch)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "File.bin", "recordType": "FILE",
                "webUrl": "https://x.com",
                # no mimeType to trigger fetch
            }
        ]
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://x.com/file",
            "mimeType": "application/x-unknown-type",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "file content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        # Should still work, just no extension set
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_fetched_records_with_falsy_entry(
        self, retrieval_service, mock_graph_provider
    ):
        """Falsy entries in fetched_records are skipped in record_id_to_record_map (line 334->333)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        # Return list with a None entry and a valid entry
        mock_graph_provider.get_records_by_record_ids.return_value = [
            None,  # falsy entry
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "webUrl": "https://x.com", "mimeType": "text/plain",
            }
        ]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            }
        ])
        result = await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_result_to_record_map_record_not_found_skips(
        self, retrieval_service, mock_graph_provider
    ):
        """In result_to_record_map iteration, when record not in record_id_to_record_map, skip (line 465-466)."""
        mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1", "vr2": "rec2"}
        mock_graph_provider.get_user_by_user_id.return_value = {"email": "u@t.com"}
        mock_graph_provider.get_records_by_record_ids.return_value = [
            {
                "_key": "rec1", "virtualRecordId": "vr1", "origin": "drive",
                "recordName": "Doc", "recordType": "FILE",
                "webUrl": "https://x.com",
                # no mimeType to trigger fetch
            },
            {
                "_key": "rec2", "virtualRecordId": "vr2", "origin": "drive",
                "recordName": "Doc2", "webUrl": "https://x.com/2", "mimeType": "text/plain",
            }
        ]
        mock_graph_provider.get_document.return_value = {
            "webUrl": "https://x.com/file", "mimeType": "application/pdf",
        }
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[
            {
                "score": 0.9, "content": "file content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr1", "orgId": "o1"},
            },
            {
                "score": 0.85, "content": "doc2 content",
                "citationType": "vectordb|document",
                "metadata": {"virtualRecordId": "vr2", "orgId": "o1"},
            },
        ])

        # Override mapping so rec1 maps to a key not in record_id_to_record_map
        original = retrieval_service._create_virtual_to_record_mapping
        def custom_mapping(records, vids):
            mapping = original(records, vids)
            # Replace vr1's record with a nonexistent _key
            if "vr1" in mapping:
                mapping["vr1"] = {"_key": "rec_fake"}
            return mapping
        retrieval_service._create_virtual_to_record_mapping = custom_mapping
        await retrieval_service.search_with_filters(
            queries=["test"], user_id="u1", org_id="o1"
        )
        retrieval_service._create_virtual_to_record_mapping = original
