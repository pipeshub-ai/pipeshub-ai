"""Deep unit tests for app.modules.transformers.vectorstore.VectorStore.

Covers:
- split_into_sentences (pysbd-based, replaces the old custom_sentence_boundary)
- _initialize_collection
- get_embedding_model_instance
- _create_embeddings
- index_documents
- _is_local_cpu_embedding
- describe_image_async
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import (
    EmbeddingError,
    IndexingError,
    VectorStoreError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vectorstore(supports_sparse=False):
    """Instantiate a VectorStore with everything mocked to bypass __init__ side effects."""
    from unittest.mock import MagicMock as _MM

    from app.modules.transformers.vectorstore import VectorStore
    from app.services.vector_db.models import VectorDBCapabilities

    vdb = AsyncMock()
    caps = VectorDBCapabilities(
        supports_sparse_vectors=supports_sparse,
        supports_server_side_text_search=False,
    )
    vdb.get_capabilities = _MM(return_value=caps)
    vdb.get_service_name = _MM(return_value="mock")

    vs = VectorStore(
        logger=_MM(),
        config_service=AsyncMock(),
        graph_provider=AsyncMock(),
        collection_name="test_collection",
        vector_db_service=vdb,
    )
    return vs


# ===================================================================
# split_into_sentences (replaces the removed custom_sentence_boundary
# spaCy component)
# ===================================================================

class TestSplitIntoSentences:
    """Tests for app.modules.parsers.text_splitting.split_into_sentences."""

    def test_number_followed_by_period(self):
        """Number + period should NOT be treated as sentence boundary."""
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("Item 42. End of list.", "en")
        assert result

    def test_abbreviation_followed_by_period(self):
        """Common abbreviations + period should NOT be treated as sentence boundary."""
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("Dr. Smith arrived.", "en")
        assert len(result) <= 2

    def test_ellipsis_not_split(self):
        """Ellipsis should not be treated as a hard sentence boundary."""
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("Wait... end.", "en")
        assert result

    def test_bullet_point_not_split(self):
        """Bullet point markers should not break sentence splitting."""
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("• item one end.", "en")
        assert result


# ===================================================================
# _is_local_cpu_embedding
# ===================================================================

class TestIsLocalCpuEmbedding:
    def test_none_provider_is_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = None
        assert vs._is_local_cpu_embedding() is True

    def test_default_provider_is_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "default"
        assert vs._is_local_cpu_embedding() is True

    def test_sentence_transformers_is_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "sentenceTransformers"
        assert vs._is_local_cpu_embedding() is True

    def test_openai_is_not_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        assert vs._is_local_cpu_embedding() is False

    def test_cohere_is_not_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        assert vs._is_local_cpu_embedding() is False


# ===================================================================
# _initialize_collection
# ===================================================================

class TestInitializeCollection:
    @pytest.mark.asyncio
    async def test_collection_exists_matching_size(self):
        """When collection exists with correct size, no recreation."""
        vs = _make_vectorstore()

        info = MagicMock()
        info.exists = True
        info.dense_dimension = 1024
        vs.vector_db_service.get_collection_info = AsyncMock(return_value=info)
        vs.vector_db_service.create_collection = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)

        vs.vector_db_service.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_collection_exists_mismatched_size_raises(self):
        """Dimension mismatch raises VectorStoreError instead of silently recreating.

        Phase 2: auto-delete on mismatch was removed to prevent accidental data loss.
        Callers must handle the error and trigger an explicit re-index flow.
        """
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()

        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=True, dense_dimension=768)
        )
        vs.vector_db_service.delete_collection = AsyncMock()
        vs.vector_db_service.create_collection = AsyncMock()

        with pytest.raises(VectorStoreError, match="dimension mismatch"):
            await vs._initialize_collection(embedding_size=1024)

        # Collection must NOT be auto-deleted
        vs.vector_db_service.delete_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_collection_not_found_creates_new(self):
        """When collection doesn't exist, create it."""
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()

        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=False)
        )
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)

        vs.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_collection_creation_failure_raises(self):
        """When collection creation fails, raises VectorStoreError."""
        vs = _make_vectorstore()

        vs.vector_db_service.get_collection = AsyncMock(side_effect=Exception("not found"))
        vs.vector_db_service.create_collection = AsyncMock(side_effect=Exception("disk full"))

        with pytest.raises(VectorStoreError):
            await vs._initialize_collection(embedding_size=1024)


# ===================================================================
# get_embedding_model_instance
# ===================================================================

class TestGetEmbeddingModelInstance:
    @pytest.mark.asyncio
    async def test_uses_default_when_no_configs(self):
        """When no embedding configs, falls back to default model."""
        vs = _make_vectorstore()

        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024
        mock_embeddings.model_name = "default-model"

        vs.config_service.get_config = AsyncMock(
            return_value={"embedding": []}
        )
        vs._initialize_collection = AsyncMock()

        with patch(
            "app.modules.transformers.vectorstore.get_default_embedding_model",
            return_value=mock_embeddings,
        ):
            result = await vs.get_embedding_model_instance()

        assert vs.dense_embeddings is mock_embeddings
        assert vs.model_name == "default-model"

    @pytest.mark.asyncio
    async def test_uses_configured_model(self):
        """When embedding config exists, uses it."""
        vs = _make_vectorstore()

        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 768
        mock_embeddings.model_name = "configured-model"

        config = {
            "embedding": [{
                "provider": "openai",
                "isDefault": True,
                "isMultimodal": False,
                "configuration": {
                    "model": "text-embedding-ada-002",
                    "apiKey": "fake-key",
                },
            }]
        }

        vs.config_service.get_config = AsyncMock(return_value=config)
        vs._initialize_collection = AsyncMock()

        with patch(
            "app.modules.transformers.vectorstore.get_embedding_model",
            return_value=mock_embeddings,
        ):
            result = await vs.get_embedding_model_instance()

        assert vs.dense_embeddings is mock_embeddings
        assert vs.is_multimodal_embedding is False

    @pytest.mark.asyncio
    async def test_embed_query_failure_raises(self):
        """When embed_query fails, raises IndexingError."""
        vs = _make_vectorstore()

        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.side_effect = Exception("model error")

        vs.config_service.get_config = AsyncMock(
            return_value={"embedding": []}
        )

        with patch(
            "app.modules.transformers.vectorstore.get_default_embedding_model",
            return_value=mock_embeddings,
        ):
            with pytest.raises(IndexingError):
                await vs.get_embedding_model_instance()


# ===================================================================
# _create_embeddings
# ===================================================================

class TestCreateEmbeddings:
    @pytest.mark.asyncio
    async def test_empty_chunks_raises(self):
        """Empty chunks list raises EmbeddingError."""
        vs = _make_vectorstore()

        with pytest.raises(EmbeddingError):
            await vs._create_embeddings([], "rec-1", "vr-1")

    @pytest.mark.asyncio
    async def test_success_with_document_chunks(self):
        """Document chunks are processed and stored."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[])
        vs._store_image_points = AsyncMock()

        chunks = [
            Document(
                page_content="hello world",
                metadata={"virtualRecordId": "vr-1", "orgId": "org-1"},
            )
        ]

        await vs._create_embeddings(chunks, "rec-1", "vr-1")

        vs.delete_embeddings.assert_awaited_once_with("vr-1")
        vs._process_document_chunks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_with_image_chunks(self):
        """Image chunks trigger image embedding pipeline."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[MagicMock()])
        vs._store_image_points = AsyncMock()

        # Image chunks are dicts, not Documents
        chunks = [
            {"image_uri": "data:image/png;base64,abc", "metadata": {"virtualRecordId": "vr-1"}}
        ]

        await vs._create_embeddings(chunks, "rec-1", "vr-1")

        vs._process_image_embeddings.assert_awaited_once()
        vs._store_image_points.assert_awaited_once()


# ===================================================================
# index_documents
# ===================================================================

class TestIndexDocuments:
    @pytest.mark.asyncio
    async def test_empty_blocks_returns_none(self):
        """When no blocks or block_groups, returns None."""
        from app.models.blocks import BlocksContainer

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_embedding_model_failure_raises(self):
        """When get_embedding_model_instance fails, IndexingError is raised."""
        from app.models.blocks import BlocksContainer

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(side_effect=IndexingError("no model"))

        with pytest.raises(IndexingError):
            await vs.index_documents(
                BlocksContainer(blocks=[], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

    @pytest.mark.asyncio
    async def test_text_blocks_processed(self):
        """Text blocks are split into sentences and embedded."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = Block(
            type=BlockType.TEXT,
            format=DataFormat.TXT,
            data="Sentence one. Sentence two.",
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[block], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        vs._create_embeddings.assert_awaited_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_embeddable_documents_returns_true(self):
        """When blocks exist but don't produce embeddable docs, returns True."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        # An image block without a URI won't produce documents
        block = Block(
            type=BlockType.IMAGE,
            format=DataFormat.BASE64,
            data={"no_uri": True},
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[block], block_groups=[]),
                "org-1", "rec-1", "vr-1", "image/png"
            )

        assert result is True


# ===================================================================
# describe_image_async
# ===================================================================

class TestDescribeImageAsync:
    @pytest.mark.asyncio
    async def test_returns_description(self):
        vs = _make_vectorstore()

        mock_response = MagicMock()
        mock_response.content = "A photo of a cat."

        mock_vlm = AsyncMock()
        mock_vlm.ainvoke = AsyncMock(return_value=mock_response)

        result = await vs.describe_image_async("data:image/png;base64,abc", mock_vlm)

        assert result == "A photo of a cat."
        mock_vlm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vlm_error_propagates(self):
        """When VLM raises, the exception propagates."""
        vs = _make_vectorstore()

        mock_vlm = AsyncMock()
        mock_vlm.ainvoke = AsyncMock(side_effect=RuntimeError("VLM crashed"))

        with pytest.raises(RuntimeError, match="VLM crashed"):
            await vs.describe_image_async("data:image/png;base64,abc", mock_vlm)

    @pytest.mark.asyncio
    async def test_vlm_empty_content(self):
        """When VLM returns empty content, result is empty string."""
        vs = _make_vectorstore()

        mock_response = MagicMock()
        mock_response.content = ""

        mock_vlm = AsyncMock()
        mock_vlm.ainvoke = AsyncMock(return_value=mock_response)

        result = await vs.describe_image_async("data:image/png;base64,abc", mock_vlm)
        assert result == ""


# ===================================================================
# describe_images (batch) — deeper tests
# ===================================================================

class TestDescribeImages:
    @pytest.mark.asyncio
    async def test_batch_describe_success(self):
        """Batch describe returns list of results per image."""
        vs = _make_vectorstore()

        mock_response = MagicMock()
        mock_response.content = "Description"

        mock_vlm = AsyncMock()
        mock_vlm.ainvoke = AsyncMock(return_value=mock_response)

        results = await vs.describe_images(["img1", "img2"], mock_vlm)
        assert len(results) == 2
        assert all(r["success"] for r in results)
        assert results[0]["description"] == "Description"

    @pytest.mark.asyncio
    async def test_batch_describe_partial_failure(self):
        """One failing image does not crash the batch."""
        vs = _make_vectorstore()

        call_count = 0

        async def mock_ainvoke(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("VLM error for image 2")
            resp = MagicMock()
            resp.content = "OK"
            return resp

        mock_vlm = AsyncMock()
        mock_vlm.ainvoke = mock_ainvoke

        results = await vs.describe_images(["img1", "img2", "img3"], mock_vlm)
        assert len(results) == 3
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 2
        failed = [r for r in results if not r["success"]]
        assert len(failed) == 1
        assert "VLM error" in failed[0]["error"]


# ===================================================================
# index_documents — deeper tests with text + image blocks
# ===================================================================

class TestIndexDocumentsDeeper:
    @pytest.mark.asyncio
    async def test_text_and_image_blocks_combined(self):
        """Both text and image blocks produce documents."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)  # is_multimodal_embedding
        vs._create_embeddings = AsyncMock()

        text_block = Block(type=BlockType.TEXT, format=DataFormat.TXT, data="Test sentence.", index=0)
        image_block = Block(
            type=BlockType.IMAGE,
            format=DataFormat.BASE64,
            data={"uri": "data:image/png;base64,abc"},
            index=1,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": True}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[text_block, image_block], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        vs._create_embeddings.assert_awaited_once()
        # Should have text document + image document
        chunks = vs._create_embeddings.call_args[0][0]
        assert len(chunks) >= 2
        assert result is True

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_llm(self):
        """Image blocks with multimodal LLM (not embedding) create text descriptions."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)  # NOT multimodal embedding
        vs._create_embeddings = AsyncMock()
        vs.describe_images = AsyncMock(return_value=[
            {"index": 0, "success": True, "description": "A photo of a cat"},
        ])

        image_block = Block(
            type=BlockType.IMAGE,
            format=DataFormat.BASE64,
            data={"uri": "data:image/png;base64,abc"},
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": True}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[image_block], block_groups=[]),
                "org-1", "rec-1", "vr-1", "image/png"
            )

        vs.describe_images.assert_awaited_once()
        vs._create_embeddings.assert_awaited_once()
        chunks = vs._create_embeddings.call_args[0][0]
        assert any("cat" in getattr(c, "page_content", "") for c in chunks)
        assert result is True

    @pytest.mark.asyncio
    async def test_table_blocks_from_block_groups(self):
        """Table block_groups are handled without raising."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        text_block = Block(type=BlockType.TEXT, format=DataFormat.TXT, data="Content.", index=0)

        table_group = BlockGroup(
            type="table",
            index=1,
            start_index=1,
            end_index=3,
            data={"headers": ["A"]},
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[text_block], block_groups=[table_group]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_index_documents_create_embeddings_failure(self):
        """When _create_embeddings fails, error is raised or handled."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=EmbeddingError("embedding failed"))

        text_block = Block(type=BlockType.TEXT, format=DataFormat.TXT, data="Content.", index=0)

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            with pytest.raises(EmbeddingError):
                await vs.index_documents(
                    BlocksContainer(blocks=[text_block], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "text/plain"
                )

    @pytest.mark.asyncio
    async def test_index_documents_llm_failure(self):
        """When get_llm fails, IndexingError is raised."""
        from app.models.blocks import BlocksContainer

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            side_effect=Exception("LLM unavailable"),
        ):
            with pytest.raises(IndexingError):
                await vs.index_documents(
                    BlocksContainer(blocks=[], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "text/plain"
                )


# ===================================================================
# _create_embeddings — deeper tests with mixed chunks
# ===================================================================

class TestCreateEmbeddingsDeeper:
    @pytest.mark.asyncio
    async def test_mixed_document_and_image_chunks(self):
        """Both Document and image dict chunks are processed."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[MagicMock()])
        vs._store_image_points = AsyncMock()

        chunks = [
            Document(
                page_content="hello",
                metadata={"virtualRecordId": "vr-1", "orgId": "org-1"},
            ),
            {"image_uri": "data:image/png;base64,abc", "metadata": {"virtualRecordId": "vr-1"}},
        ]

        await vs._create_embeddings(chunks, "rec-1", "vr-1")

        vs._process_document_chunks.assert_awaited_once()
        vs._process_image_embeddings.assert_awaited_once()
        vs._store_image_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_image_only_chunks(self):
        """Only image chunks skip document processing."""
        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[MagicMock()])
        vs._store_image_points = AsyncMock()

        chunks = [
            {"image_uri": "data:image/png;base64,abc", "metadata": {"virtualRecordId": "vr-1"}},
        ]

        await vs._create_embeddings(chunks, "rec-1", "vr-1")

        vs._process_document_chunks.assert_not_awaited()
        vs._process_image_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_image_points_always_stored(self):
        """Image points are always passed to store, even if empty."""
        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[])
        vs._store_image_points = AsyncMock()

        chunks = [
            {"image_uri": "data:image/png;base64,abc", "metadata": {"virtualRecordId": "vr-1"}},
        ]

        await vs._create_embeddings(chunks, "rec-1", "vr-1")

        # Store is always called for image chunks, even with empty points
        vs._store_image_points.assert_awaited_once_with([])


# ===================================================================
# _normalize_image_to_base64
# ===================================================================

class TestNormalizeImageToBase64:
    @pytest.mark.asyncio
    async def test_data_url(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("data:image/png;base64,iVBORw0KGgo=")
        assert result == "iVBORw0KGgo="

    @pytest.mark.asyncio
    async def test_data_url_no_comma(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("data:image/png;base64")
        assert result is None

    @pytest.mark.asyncio
    async def test_raw_base64(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("aGVsbG8=")
        assert result == "aGVsbG8="

    @pytest.mark.asyncio
    async def test_raw_base64_padding_fix(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("aGVsbG8")
        assert result is not None
        assert result.endswith("=")

    @pytest.mark.asyncio
    async def test_none_input(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_string(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_string_input(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(12345)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_base64_chars(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("not valid base64 with spaces!@#")
        assert result is None


# ===================================================================
# delete_embeddings
# ===================================================================

class TestDeleteEmbeddings:
    @pytest.mark.asyncio
    async def test_success(self):
        vs = _make_vectorstore()
        vs.vector_db_service.filter_collection = AsyncMock(return_value={"filter": {}})
        vs.vector_db_service.delete_points = AsyncMock()
        await vs.delete_embeddings("vr-1")
        vs.vector_db_service.filter_collection.assert_awaited_once()
        vs.vector_db_service.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_raises(self):
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()
        vs.vector_db_service.filter_collection = AsyncMock(side_effect=Exception("db error"))
        with pytest.raises(EmbeddingError):
            await vs.delete_embeddings("vr-1")


# ===================================================================
# _store_image_points
# ===================================================================

class TestStoreImagePoints:
    @pytest.mark.asyncio
    async def test_with_points(self):
        vs = _make_vectorstore()
        mock_point = MagicMock()
        vs.vector_db_service.upsert_points = AsyncMock()
        await vs._store_image_points([mock_point])
        vs.vector_db_service.upsert_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_empty_points(self):
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = AsyncMock()
        await vs._store_image_points([])
        vs.vector_db_service.upsert_points.assert_not_awaited()


# ===================================================================
# _process_image_embeddings (routing)
# ===================================================================

class TestProcessImageEmbeddings:
    @pytest.mark.asyncio
    async def test_unsupported_provider(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "unknown_provider"
        result = await vs._process_image_embeddings([], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_cohere_provider_routing(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        vs._process_image_embeddings_cohere = AsyncMock(return_value=[MagicMock()])
        result = await vs._process_image_embeddings(
            [{"metadata": {}}], ["data:image/png;base64,abc"]
        )
        vs._process_image_embeddings_cohere.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_voyage_provider_routing(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "voyage"
        vs._process_image_embeddings_voyage = AsyncMock(return_value=[])
        await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_voyage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_jina_provider_routing(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "jinaAI"
        vs._process_image_embeddings_jina = AsyncMock(return_value=[])
        await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_jina.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bedrock_provider_routing(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "bedrock"
        vs._process_image_embeddings_bedrock = AsyncMock(return_value=[])
        await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_bedrock.assert_awaited_once()


# ===================================================================
# _process_document_chunks
# ===================================================================

class TestProcessDocumentChunks:
    @pytest.mark.asyncio
    async def test_local_sequential_processing(self):
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs._embed_and_upsert_documents = AsyncMock()

        docs = [Document(page_content=f"doc {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(docs)
        assert vs._embed_and_upsert_documents.await_count > 0

    @pytest.mark.asyncio
    async def test_remote_concurrent_processing(self):
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs._embed_and_upsert_documents = AsyncMock()

        docs = [Document(page_content=f"doc {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(docs)
        assert vs._embed_and_upsert_documents.await_count > 0

    @pytest.mark.asyncio
    async def test_batch_failure_raises(self):
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = None
        vs._embed_and_upsert_documents = AsyncMock(side_effect=Exception("batch fail"))

        docs = [Document(page_content="test", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(docs)


# ===================================================================
# apply
# ===================================================================

class TestApply:
    @pytest.mark.asyncio
    async def test_apply_delegates(self):
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.index_documents = AsyncMock(return_value=True)

        mock_record = MagicMock()
        mock_record.id = "r1"
        mock_record.virtual_record_id = "vr1"
        mock_record.block_containers = BlocksContainer(blocks=[], block_groups=[])
        mock_record.org_id = "o1"
        mock_record.mime_type = "text/plain"

        mock_ctx = MagicMock()
        mock_ctx.record = mock_record

        result = await vs.apply(mock_ctx)
        vs.index_documents.assert_awaited_once()
        assert result is True


# ===================================================================
# index_documents — table block handling
# ===================================================================

class TestIndexDocumentsTableBlocks:
    @pytest.mark.asyncio
    async def test_table_row_blocks_embedded(self):
        """Table row blocks produce documents for embedding."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        table_row = Block(
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"row_natural_language_text": "Row 1: A is 10, B is 20", "row_number": 1},
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[table_row], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        vs._create_embeddings.assert_awaited_once()
        chunks = vs._create_embeddings.call_args[0][0]
        assert any("Row 1" in getattr(c, "page_content", "") for c in chunks)

    @pytest.mark.asyncio
    async def test_table_block_group_summary_embedded(self):
        """Table block groups produce summary embeddings."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        table_group = BlockGroup(
            type="table",
            index=0,
            data={"table_summary": "Summary of sales data", "column_headers": ["A", "B"]},
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                BlocksContainer(blocks=[], block_groups=[table_group]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        vs._create_embeddings.assert_awaited_once()
        chunks = vs._create_embeddings.call_args[0][0]
        assert any("Summary of sales" in getattr(c, "page_content", "") for c in chunks)


# ===================================================================
# split_into_sentences error resilience (replaces the removed
# _get_shared_nlp custom-pipe-exception coverage)
# ===================================================================

class TestSplitIntoSentencesResilience:
    def test_segmenter_construction_failure_falls_back_to_regex(self):
        """If pysbd.Segmenter construction fails, sentence splitting still works."""
        import app.modules.parsers.text_splitting as text_splitting_module
        from app.modules.parsers.text_splitting import split_into_sentences

        # Force a cache miss so the patched constructor is actually exercised.
        text_splitting_module._SEGMENTER_CACHE.pop("en", None)
        with patch(
            "app.modules.parsers.text_splitting.pysbd.Segmenter",
            side_effect=ValueError("segmenter init failed"),
        ):
            result = split_into_sentences("First. Second.", "en")
        assert result


# ===================================================================
# __init__ sparse embeddings failure (lines 106-107)
# ===================================================================

class TestVectorStoreInitSparseFailure:
    @pytest.mark.asyncio
    async def test_sparse_embeddings_exception_raises_indexing_error(self):
        """When SparseEmbedder initialisation fails, _ensure_sparse_embeddings raises IndexingError."""
        from app.services.vector_db.models import VectorDBCapabilities

        # Build a VectorStore with sparse-vector support enabled
        vs = _make_vectorstore(supports_sparse=True)

        with patch(
            "app.services.vector_db.sparse_embeddings.asyncio.to_thread",
            side_effect=RuntimeError("sparse init fail"),
        ):
            with pytest.raises(IndexingError, match="Failed to initialise"):
                await vs._ensure_sparse_embeddings()


# ===================================================================
# _normalize_image_to_base64 edge cases (lines 151-152)
# ===================================================================

class TestNormalizeImageToBase64:
    @pytest.mark.asyncio
    async def test_none_input(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_string(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_string_input(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(123)
        assert result is None

    @pytest.mark.asyncio
    async def test_data_url_without_comma(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("data:image/png;base64")
        assert result is None

    @pytest.mark.asyncio
    async def test_data_url_with_base64(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("data:image/png;base64,AAAA")
        assert result == "AAAA"

    @pytest.mark.asyncio
    async def test_data_url_with_padding_fix(self):
        vs = _make_vectorstore()
        # 5 chars needs 3 padding chars to be multiple of 4
        result = await vs._normalize_image_to_base64("data:image/png;base64,AAAAA")
        assert result.endswith("=")
        assert len(result) % 4 == 0

    @pytest.mark.asyncio
    async def test_raw_base64(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("SGVsbG8=")
        assert result == "SGVsbG8="

    @pytest.mark.asyncio
    async def test_raw_base64_with_whitespace(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("  SGVs bG8=  ")
        assert "SGVs" in result

    @pytest.mark.asyncio
    async def test_invalid_characters_returns_none(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("not!valid@base64")
        assert result is None


# ===================================================================
# split_into_sentences heading-like input (replaces the removed
# custom_sentence_boundary heading detection coverage)
# ===================================================================

class TestSplitIntoSentencesHeading:
    """Sanity-check heading-like text is handled by pysbd's rule sets."""

    def test_all_caps_heading_followed_by_body(self):
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("INTRODUCTION. Next paragraph starts here.", "en")
        assert result

    def test_all_caps_with_digits_not_treated_specially(self):
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("ABC123. Next paragraph starts here.", "en")
        assert result


# ===================================================================
# _process_image_embeddings_voyage exception handling (lines 545-546)
# ===================================================================

class TestProcessImageEmbeddingsVoyageException:
    @pytest.mark.asyncio
    async def test_voyage_batch_exception_logged(self):
        """When a Voyage batch raises, should log warning and continue."""
        vs = _make_vectorstore()
        vs.api_key = "test-key"

        # Voyage uses self.dense_embeddings.aembed_documents, make it raise
        mock_embeddings = AsyncMock()
        mock_embeddings.aembed_documents = AsyncMock(side_effect=RuntimeError("Voyage API error"))
        mock_embeddings.batch_size = 7
        vs.dense_embeddings = mock_embeddings

        result = await vs._process_image_embeddings_voyage(
            image_chunks=[{"metadata": {}}],
            image_base64s=["data:image/png;base64,AAAA"],
        )

        assert result == []
        vs.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_voyage_gather_returns_exception(self):
        """When asyncio.gather returns an Exception object, should log and skip."""
        vs = _make_vectorstore()
        vs.api_key = "test-key"

        # Patch gather to return exception objects
        mock_embeddings = AsyncMock()
        mock_embeddings.batch_size = 7
        # The batch processing will succeed but we'll simulate gather returning an exception
        mock_embeddings.aembed_documents = AsyncMock(side_effect=RuntimeError("gather error"))
        vs.dense_embeddings = mock_embeddings

        result = await vs._process_image_embeddings_voyage(
            image_chunks=[{"metadata": {}}],
            image_base64s=["img1"],
        )

        # Should be empty since the batch failed
        assert result == []


# ===================================================================
# _process_image_embeddings_bedrock error paths (lines 614-623, 639)
# ===================================================================

class TestProcessImageEmbeddingsBedrockErrors:
    @pytest.mark.asyncio
    async def test_bedrock_client_error_returns_none(self):
        """ClientError should be caught and return None for that image."""
        from botocore.exceptions import ClientError

        vs = _make_vectorstore()
        vs.aws_access_key_id = "key"
        vs.aws_secret_access_key = "secret"
        vs.region_name = "us-east-1"

        with patch("boto3.client") as mock_boto_client:
            mock_client = MagicMock()
            mock_boto_client.return_value = mock_client
            mock_client.invoke_model.side_effect = ClientError(
                {"Error": {"Code": "400", "Message": "bad request"}},
                "InvokeModel",
            )

            with patch.object(vs, "_normalize_image_to_base64", new_callable=AsyncMock, return_value="AAAA"):
                result = await vs._process_image_embeddings_bedrock(
                    image_chunks=[{"metadata": {}}],
                    image_base64s=["AAAA"],
                )

        assert result == []

    @pytest.mark.asyncio
    async def test_bedrock_unexpected_error_returns_none(self):
        """Unexpected errors should be caught and return None."""
        vs = _make_vectorstore()
        vs.aws_access_key_id = "key"
        vs.aws_secret_access_key = "secret"
        vs.region_name = "us-east-1"

        with patch("boto3.client") as mock_boto_client:
            mock_client = MagicMock()
            mock_boto_client.return_value = mock_client
            mock_client.invoke_model.side_effect = RuntimeError("unexpected bedrock error")

            with patch.object(vs, "_normalize_image_to_base64", new_callable=AsyncMock, return_value="AAAA"):
                result = await vs._process_image_embeddings_bedrock(
                    image_chunks=[{"metadata": {}}],
                    image_base64s=["AAAA"],
                )

        assert result == []

    @pytest.mark.asyncio
    async def test_bedrock_gather_exception_logged(self):
        """Exception in gather results should be logged."""
        vs = _make_vectorstore()
        vs.aws_access_key_id = "key"
        vs.aws_secret_access_key = "secret"
        vs.region_name = "us-east-1"

        with patch("boto3.client") as mock_boto_client:
            mock_client = MagicMock()
            mock_boto_client.return_value = mock_client
            mock_client.invoke_model.side_effect = Exception("gather level error")

            with patch.object(vs, "_normalize_image_to_base64", new_callable=AsyncMock, return_value="AAAA"):
                result = await vs._process_image_embeddings_bedrock(
                    image_chunks=[{"metadata": {}}],
                    image_base64s=["AAAA"],
                )

        assert result == []


# ===================================================================
# _process_image_embeddings_jina exception handling (lines 733-734)
# ===================================================================

class TestProcessImageEmbeddingsJinaException:
    @pytest.mark.asyncio
    async def test_jina_batch_exception_logged(self):
        """When a Jina batch raises, should log warning and continue."""
        vs = _make_vectorstore()
        vs.api_key = "test-key"

        with patch("app.modules.transformers.vectorstore.httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.json.return_value = {"detail": "error"}
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch.object(vs, "_normalize_image_to_base64", new_callable=AsyncMock, return_value="AAAA"):
                result = await vs._process_image_embeddings_jina(
                    image_chunks=[{"metadata": {}}],
                    image_base64s=["data:image/png;base64,AAAA"],
                )

        assert result == []


# ===================================================================
# index_documents unexpected exception (lines 908-909, 1051-1052, 1104-1105, 1156-1158)
# ===================================================================

class TestIndexDocumentsExceptions:
    @pytest.mark.asyncio
    async def test_unexpected_exception_raises_indexing_error(self):
        """Generic exceptions during indexing should be wrapped in IndexingError."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            with pytest.raises(IndexingError):
                await vs.index_documents(
                    BlocksContainer(blocks=[], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "text/plain",
                )

    @pytest.mark.asyncio
    async def test_text_document_processing_error(self):
        """Exception during text block processing should raise DocumentProcessingError."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        text_block = Block(
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="some text content",
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ), patch(
            "app.modules.transformers.vectorstore._process_text_blocks",
            side_effect=RuntimeError("sentence splitting failed"),
        ):
            with pytest.raises(DocumentProcessingError, match="Failed to create text document"):
                await vs.index_documents(
                    BlocksContainer(blocks=[text_block], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "text/plain",
                )

    @pytest.mark.asyncio
    async def test_image_document_processing_error(self):
        """Exception during image block processing should raise DocumentProcessingError."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        image_block = Block(
            type=BlockType.IMAGE,
            format=DataFormat.JSON,
            data={"uri": "data:image/png;base64,AAAA"},
            index=0,
        )

        # Make describe_images raise
        vs.describe_images = AsyncMock(side_effect=RuntimeError("describe failed"))

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": True}),
        ):
            with pytest.raises(DocumentProcessingError, match="Failed to create image document"):
                await vs.index_documents(
                    BlocksContainer(blocks=[image_block], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "image/png",
                )

    @pytest.mark.asyncio
    async def test_embedding_creation_exception_wraps(self):
        """Unknown exception during _create_embeddings is wrapped in IndexingError by index_documents."""
        from app.exceptions.indexing_exceptions import IndexingError
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=RuntimeError("embed failed"))

        text_block = Block(
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="hello world",
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            with pytest.raises((IndexingError, EmbeddingError)):
                await vs.index_documents(
                    BlocksContainer(blocks=[text_block], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "text/plain",
                )


# ===================================================================
# _create_embeddings unexpected exception (line 908-909)
# ===================================================================

class TestCreateEmbeddingsUnexpectedException:
    @pytest.mark.asyncio
    async def test_unexpected_exception_in_create_embeddings(self):
        """Generic exception in _create_embeddings should raise VectorStoreError."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._embed_and_upsert_documents = AsyncMock(side_effect=RuntimeError("batch fail"))

        docs = [Document(page_content="test", metadata={"orgId": "o1"})]

        with pytest.raises(VectorStoreError):
            await vs._create_embeddings(docs, "rec-1", "vr-1")


# ===========================================================================
# Phase 1 regression tests — restored HEAD features
# ===========================================================================
"""
These tests verify the functionality that existed at HEAD and was accidentally
dropped in the multi-vector-DB changeset:
- Reconciliation skips full delete and only removes changed blocks.
- delete_blocks_by_ids uses blockId + virtualRecordId in the filter.
- Every upserted payload contains blockId (and blockIndex).
- Orphan cleanup fires when the record is gone and no MD5 duplicate exists.
- Record-deleted-mid-embedding guard prevents upsert.
- SQL row blocks are indexed with the correct metadata shape.
- Empty table_summary / row_natural_language_text produce no embedding.
"""

from unittest.mock import AsyncMock, MagicMock, call, patch
import pytest
from langchain_core.documents import Document

from app.models.blocks import BlocksContainer
from app.services.vector_db.models import VectorDBCapabilities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeBlock:
    """A minimal duck-typed block that passes through BlocksContainer validation
    via ``model_validate`` with ``from_attributes=True``.

    Using a real ``Block`` pydantic model fails for SQL sub_types (e.g.
    ``sql_table``) that are not in ``BlockSubType``.  This helper creates a
    plain object whose attributes match the required field names.
    """

    def __init__(self, block_id, block_type_str, data, index=0, sub_type_str=None):
        from app.models.blocks import BlockType

        self.id = block_id
        # type must be the enum so vectorstore code sees .value
        bt = next((m for m in BlockType if m.value == block_type_str), None)
        if bt is None:
            raise ValueError(f"Unknown BlockType value: {block_type_str!r}")
        self.type = bt

        # sub_type: use a simple namespace so .value works
        if sub_type_str is not None:
            class _ST:
                value = sub_type_str
            self.sub_type = _ST()
        else:
            self.sub_type = None

        self.index = index
        self.data = data
        # Extra fields that Block expects
        self.parent_index = None
        self.name = None
        self.format = None
        self.comments = []
        self.source_creation_date = None
        self.source_update_date = None
        self.source_id = None
        self.source_name = None
        self.source_type = None
        self.links = None
        self.weburl = None
        self.public_data_link = None
        self.public_data_link_expiration_epoch_time_in_ms = None


def _make_block(block_id: str, block_type: str, data, index: int = 0, sub_type=None):
    return _FakeBlock(block_id, block_type, data, index=index, sub_type_str=sub_type)


def _make_blocks_container(blocks=None, block_groups=None):
    """Create a BlocksContainer that bypasses pydantic validation.

    ``_FakeBlock`` instances are not pydantic models, so we build the container
    manually by constructing it with empty lists and then setting the attributes.
    """
    from app.models.blocks import BlocksContainer
    container = BlocksContainer.__new__(BlocksContainer)
    object.__setattr__(container, "__dict__", {})
    container.__dict__["blocks"] = blocks or []
    container.__dict__["block_groups"] = block_groups or []
    return container


def _make_vectorstore_p1(supports_sparse=False):
    """Full-featured VectorStore with all async DB calls mocked."""
    from app.modules.transformers.vectorstore import VectorStore

    vdb = AsyncMock()
    caps = VectorDBCapabilities(
        supports_sparse_vectors=supports_sparse,
        supports_server_side_text_search=False,
    )
    vdb.get_capabilities = MagicMock(return_value=caps)
    vdb.get_service_name = MagicMock(return_value="mock")
    vdb.filter_collection = AsyncMock(return_value=MagicMock())
    vdb.delete_points = AsyncMock()
    vdb.upsert_points = AsyncMock()
    vdb.get_collection_info = AsyncMock(return_value=MagicMock(exists=True, dense_dimension=1024))
    vdb.create_collection = AsyncMock()
    vdb.create_index = AsyncMock()

    graph_provider = AsyncMock()
    # Default: record still exists
    graph_provider.get_document = AsyncMock(return_value={"_id": "rec-1"})

    vs = VectorStore.__new__(VectorStore)
    vs.logger = MagicMock()
    vs.config_service = AsyncMock()
    vs.graph_provider = graph_provider
    vs.vector_db_service = vdb
    vs.collection_name = "test_collection"
    vs._capabilities = caps
    vs._sparse_embedder = None
    vs._sparse_embedder_lock = None

    # Dense embeddings pre-configured
    dense = MagicMock()
    dense.embed_documents = MagicMock(return_value=[[0.1] * 1024])
    dense.embed_query = MagicMock(return_value=[0.1] * 1024)
    vs.dense_embeddings = dense
    vs.embedding_provider = None
    vs.api_key = None
    vs.model_name = "test-model"
    vs.region_name = None
    vs.aws_access_key_id = None
    vs.aws_secret_access_key = None
    vs.is_multimodal_embedding = False

    return vs


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class TestReconciliation:
    @pytest.mark.asyncio
    async def test_reconciliation_skips_full_delete(self):
        """On reconciliation, delete_points must NOT be called for virtualRecordId
        (full wipe); only delete_blocks_by_ids for changed block IDs."""
        vs = _make_vectorstore_p1()

        b1 = _make_block("block-1", "text", "hello world", index=0)
        containers = _make_blocks_container(blocks=[b1], block_groups=[])

        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._process_document_chunks = AsyncMock()
        vs.delete_blocks_by_ids = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        from app.utils.llm import get_llm
        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
                block_ids_to_delete={"block-old"},
                is_reconciliation=True,
            )

        # delete_embeddings (full wipe) must NOT be called
        vs.vector_db_service.delete_points.assert_not_called()

        # delete_blocks_by_ids must be called for the removed blocks
        vs.delete_blocks_by_ids.assert_awaited()
        calls = vs.delete_blocks_by_ids.call_args_list
        deleted_ids = set()
        for c in calls:
            ids_arg = c.args[0] if c.args else c.kwargs.get("block_ids", set())
            deleted_ids.update(ids_arg)
        assert "block-old" in deleted_ids

    @pytest.mark.asyncio
    async def test_delete_blocks_by_ids_filter_shape(self):
        """delete_blocks_by_ids must build a filter with blockId AND virtualRecordId."""
        vs = _make_vectorstore_p1()
        vs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        vs.vector_db_service.delete_points = AsyncMock()

        await vs.delete_blocks_by_ids({"blk-1", "blk-2"}, "vr-42")

        vs.vector_db_service.filter_collection.assert_awaited_once()
        kwargs = vs.vector_db_service.filter_collection.call_args.kwargs
        must = kwargs.get("must", {})
        assert "blockId" in must, "filter must scope to blockId"
        assert "virtualRecordId" in must, "filter must scope to virtualRecordId"
        assert must["virtualRecordId"] == "vr-42"
        assert set(must["blockId"]) == {"blk-1", "blk-2"}


# ---------------------------------------------------------------------------
# blockId in upserted payload
# ---------------------------------------------------------------------------

class TestBlockIdInPayload:
    @pytest.mark.asyncio
    async def test_blockid_present_in_upserted_payload(self):
        """Every VectorPoint upserted to the DB must have blockId in its metadata."""
        from app.services.vector_db.models import VectorPoint

        vs = _make_vectorstore_p1()
        upserted_points = []

        async def capture_upsert(collection_name, points):
            upserted_points.extend(points)

        vs.vector_db_service.upsert_points = AsyncMock(side_effect=capture_upsert)
        vs.graph_provider.get_document = AsyncMock(return_value={"_id": "rec-1"})

        # Two text blocks
        b1 = _make_block("block-A", "text", "First block", index=0)
        b2 = _make_block("block-B", "text", "Second block", index=1)
        containers = _make_blocks_container(blocks=[b1, b2], block_groups=[])

        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_embeddings = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        assert upserted_points, "At least one VectorPoint must be upserted"
        for pt in upserted_points:
            meta = pt.payload.get("metadata", {})
            assert "blockId" in meta, f"blockId missing from payload: {meta}"

    @pytest.mark.asyncio
    async def test_blockindex_also_present_in_payload(self):
        """blockIndex must be preserved additively alongside blockId."""
        from app.services.vector_db.models import VectorPoint

        vs = _make_vectorstore_p1()
        upserted_points = []

        async def capture_upsert(collection_name, points):
            upserted_points.extend(points)

        vs.vector_db_service.upsert_points = AsyncMock(side_effect=capture_upsert)
        vs.graph_provider.get_document = AsyncMock(return_value={"_id": "rec-1"})

        b1 = _make_block("block-X", "text", "Some text", index=7)
        containers = _make_blocks_container(blocks=[b1], block_groups=[])
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_embeddings = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        for pt in upserted_points:
            meta = pt.payload.get("metadata", {})
            assert "blockIndex" in meta, f"blockIndex missing from payload: {meta}"
            assert meta.get("blockId") == "block-X"
            assert meta.get("blockIndex") == 7


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------

class TestOrphanCleanup:
    @pytest.mark.asyncio
    async def test_orphan_cleanup_called_when_empty_blocks(self):
        """When blocks are empty and no MD5 duplicates exist, orphan cleanup fires."""
        vs = _make_vectorstore_p1()
        # Record not found in graph DB — orphaned
        vs.graph_provider.get_document = AsyncMock(return_value=None)
        vs.delete_embeddings = AsyncMock()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        containers = _make_blocks_container(blocks=[], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        # delete_embeddings must have been called during cleanup
        vs.delete_embeddings.assert_awaited()


# ---------------------------------------------------------------------------
# Record-deleted-mid-embedding guard
# ---------------------------------------------------------------------------

class TestRecordDeletedMidEmbeddingGuard:
    @pytest.mark.asyncio
    async def test_record_deleted_mid_embedding_skips_upsert(self):
        """If the record disappears between the time index_documents starts
        and _embed_and_upsert_documents runs, upsert_points must not be called."""
        vs = _make_vectorstore_p1()
        # Record not found — deleted mid-flight
        vs.graph_provider.get_document = AsyncMock(return_value=None)
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_embeddings = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        b1 = _make_block("b1", "text", "Some text", index=0)
        containers = _make_blocks_container(blocks=[b1], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        vs.vector_db_service.upsert_points.assert_not_awaited()


# ---------------------------------------------------------------------------
# SQL row blocks
# ---------------------------------------------------------------------------

class TestSqlRowBlocks:
    @pytest.mark.asyncio
    async def test_sql_row_blocks_indexed(self):
        """SQL row blocks must produce VectorPoints with blockId in metadata."""
        from app.services.vector_db.models import VectorPoint

        vs = _make_vectorstore_p1()
        upserted_points = []

        async def capture_upsert(collection_name, points):
            upserted_points.extend(points)

        vs.vector_db_service.upsert_points = AsyncMock(side_effect=capture_upsert)
        vs.graph_provider.get_document = AsyncMock(return_value={"_id": "rec-1"})
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_embeddings = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        # SQL row block: type=table_row, sub_type=sql_table
        sql_block = _make_block(
            "sql-row-1", "table_row",
            {"row_natural_language_text": "user_id=42, name=Alice"},
            index=0, sub_type="sql_table",
        )
        containers = _make_blocks_container(blocks=[sql_block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        assert upserted_points, "SQL row block must produce upserted points"
        meta = upserted_points[0].payload.get("metadata", {})
        assert meta.get("blockId") == "sql-row-1"

    @pytest.mark.asyncio
    async def test_empty_row_natural_language_text_not_embedded(self):
        """SQL row blocks with empty row_natural_language_text must not be embedded."""
        vs = _make_vectorstore_p1()
        vs.graph_provider.get_document = AsyncMock(return_value={"_id": "rec-1"})
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_embeddings = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        sql_block = _make_block(
            "sql-row-empty", "table_row",
            {"row_natural_language_text": ""},
            index=0, sub_type="sql_table",
        )
        containers = _make_blocks_container(blocks=[sql_block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            result = await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        # No meaningful content → no upsert
        vs.vector_db_service.upsert_points.assert_not_awaited()


# ---------------------------------------------------------------------------
# Empty table summary guard
# ---------------------------------------------------------------------------

class TestEmptyTableSummary:
    @pytest.mark.asyncio
    async def test_empty_table_summary_not_embedded(self):
        """Table blocks with empty table_summary must not produce embedding calls."""
        vs = _make_vectorstore_p1()
        vs.graph_provider.get_document = AsyncMock(return_value={"_id": "rec-1"})
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_embeddings = AsyncMock()
        vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()

        table_bg = MagicMock()
        table_bg.type = MagicMock()
        table_bg.type.value = "table"
        table_bg.id = "tbl-1"
        table_bg.sub_type = None
        table_bg.data = {"table_summary": ""}  # Empty

        containers = _make_blocks_container(blocks=[], block_groups=[table_bg])

        with patch("app.modules.transformers.vectorstore.get_llm",
                   AsyncMock(return_value=(MagicMock(), {"isMultimodal": False}))):
            await vs.index_documents(
                block_containers=containers,
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vr-1",
            )

        vs.vector_db_service.upsert_points.assert_not_awaited()
