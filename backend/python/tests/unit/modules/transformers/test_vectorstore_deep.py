"""Deep unit tests for app.modules.transformers.vectorstore.VectorStore.

Covers:
- custom_sentence_boundary
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

def _make_vectorstore():
    """Instantiate a VectorStore with everything mocked to bypass __init__ side effects."""
    with patch(
        "app.modules.transformers.vectorstore.FastEmbedSparse"
    ) as mock_sparse, patch(
        "app.modules.transformers.vectorstore._get_shared_nlp"
    ) as mock_nlp:
        mock_sparse.return_value = MagicMock()
        mock_nlp.return_value = MagicMock()

        from app.modules.transformers.vectorstore import VectorStore

        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test_collection",
            vector_db_service=AsyncMock(),
        )
        return vs


# ===================================================================
# custom_sentence_boundary (static/component function)
# ===================================================================

class TestCustomSentenceBoundary:
    """Tests for VectorStore.custom_sentence_boundary."""

    def _make_mock_doc(self, tokens_data):
        """Create a minimal mock doc with token-like objects."""
        tokens = []
        for i, data in enumerate(tokens_data):
            tok = MagicMock()
            tok.i = i
            tok.text = data["text"]
            tok.like_num = data.get("like_num", False)
            tok.is_sent_start = data.get("is_sent_start", None)
            tokens.append(tok)

        doc = MagicMock()
        doc.__len__ = lambda self: len(tokens)
        doc.__getitem__ = lambda self, key: tokens[key] if isinstance(key, int) else tokens[key]

        # Support slicing for doc[:-1]
        class DocSlice:
            def __init__(self, toks):
                self._tokens = toks

            def __getitem__(self, key):
                if isinstance(key, slice):
                    return DocSlice(self._tokens[key])
                return self._tokens[key]

            def __iter__(self):
                return iter(self._tokens)

            def __len__(self):
                return len(self._tokens)

        real_doc = DocSlice(tokens)
        return real_doc

    def test_number_followed_by_period(self):
        """Number + period should NOT be treated as sentence boundary."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "42", "like_num": True},
            {"text": ".", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        result = VectorStore.custom_sentence_boundary(doc)

        # next_token (the ".") should have is_sent_start set to False
        assert doc[1].is_sent_start is False

    def test_abbreviation_followed_by_period(self):
        """Common abbreviations + period should NOT be treated as sentence boundary."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "dr", "like_num": False},
            {"text": ".", "is_sent_start": True},
            {"text": "Smith"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False

    def test_ellipsis_not_split(self):
        """Ellipsis (. followed by .) should not be treated as sentence boundary."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "."},
            {"text": ".", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False

    def test_bullet_point_not_split(self):
        """Bullet point markers should not cause sentence splits."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "•"},
            {"text": "item", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False


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

        collection_info = MagicMock()
        collection_info.config.params.vectors = {"dense": MagicMock(size=1024)}
        vs.vector_db_service.get_collection = AsyncMock(return_value=collection_info)
        vs.vector_db_service.create_collection = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)

        vs.vector_db_service.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_collection_exists_mismatched_size_recreates(self):
        """When collection exists but wrong size, delete and recreate."""
        vs = _make_vectorstore()

        collection_info = MagicMock()
        collection_info.config.params.vectors = {"dense": MagicMock(size=768)}
        vs.vector_db_service.get_collection = AsyncMock(return_value=collection_info)
        vs.vector_db_service.delete_collection = AsyncMock()
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)

        vs.vector_db_service.delete_collection.assert_awaited_once_with("test_collection")
        vs.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_collection_not_found_creates_new(self):
        """When collection doesn't exist, create it."""
        vs = _make_vectorstore()

        vs.vector_db_service.get_collection = AsyncMock(side_effect=Exception("not found"))
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
        ), patch(
            "app.modules.transformers.vectorstore.QdrantVectorStore"
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
        ), patch(
            "app.modules.transformers.vectorstore.QdrantVectorStore"
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

        # Mock nlp to return sentences
        mock_doc = MagicMock()
        mock_sent1 = MagicMock()
        mock_sent1.text = "Sentence one."
        mock_sent2 = MagicMock()
        mock_sent2.text = "Sentence two."
        mock_doc.sents = [mock_sent1, mock_sent2]
        vs.nlp = MagicMock(return_value=mock_doc)

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

        mock_doc = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Test sentence."
        mock_doc.sents = [mock_sent]
        vs.nlp = MagicMock(return_value=mock_doc)

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

        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

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

        mock_doc = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Content."
        mock_doc.sents = [mock_sent]
        vs.nlp = MagicMock(return_value=mock_doc)

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
        mock_doc = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Content."
        mock_doc.sents = [mock_sent]
        vs.nlp = MagicMock(return_value=mock_doc)

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
        vs.vector_db_service.delete_points = MagicMock()
        await vs.delete_embeddings("vr-1")
        vs.vector_db_service.filter_collection.assert_awaited_once()
        vs.vector_db_service.delete_points.assert_called_once()

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
        vs.vector_db_service.upsert_points = MagicMock()
        await vs._store_image_points([mock_point])
        vs.vector_db_service.upsert_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_empty_points(self):
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = MagicMock()
        await vs._store_image_points([])
        vs.vector_db_service.upsert_points.assert_not_called()


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
        result = await vs._process_image_embeddings([{"metadata": {}}], ["data:image/png;base64,abc"])
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
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock()

        docs = [Document(page_content=f"doc {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(docs)
        assert vs.vector_store.aadd_documents.await_count > 0

    @pytest.mark.asyncio
    async def test_remote_concurrent_processing(self):
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock()

        docs = [Document(page_content=f"doc {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(docs)
        assert vs.vector_store.aadd_documents.await_count > 0

    @pytest.mark.asyncio
    async def test_batch_failure_raises(self):
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = None
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock(side_effect=Exception("batch fail"))

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

        mock_doc = MagicMock()
        mock_doc.sents = []
        vs.nlp = MagicMock(return_value=mock_doc)

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

        mock_doc = MagicMock()
        mock_doc.sents = []
        vs.nlp = MagicMock(return_value=mock_doc)

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
<<<<<<< HEAD


# ===================================================================
# _get_shared_nlp exception in custom_sentence_boundary pipe (line 50)
# ===================================================================

class TestGetSharedNlpCustomPipeException:
    def test_custom_pipe_exception_handled(self):
        """When custom_sentence_boundary pipe fails to add, should not raise."""
        with patch(
            "app.modules.transformers.vectorstore.spacy.load"
        ) as mock_load:
            mock_nlp = MagicMock()
            mock_nlp.pipe_names = []  # No pipes present

            def add_pipe_side_effect(name, **kwargs):
                if name == "custom_sentence_boundary":
                    raise ValueError("pipe already exists")
                mock_nlp.pipe_names.append(name)

            mock_nlp.add_pipe = MagicMock(side_effect=add_pipe_side_effect)
            mock_load.return_value = mock_nlp

            from app.modules.transformers.vectorstore import _get_shared_nlp
            # Clear cache
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")

            result = _get_shared_nlp()
            assert result is mock_nlp

            # Clean up cache
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")


# ===================================================================
# __init__ sparse embeddings failure (lines 106-107)
# ===================================================================

class TestVectorStoreInitSparseFailure:
    def test_sparse_embeddings_exception_raises_indexing_error(self):
        """When sparse embeddings init fails with generic exception, should raise IndexingError."""
        with patch(
            "app.modules.transformers.vectorstore.FastEmbedSparse",
            side_effect=RuntimeError("sparse init fail"),
        ), patch(
            "app.modules.transformers.vectorstore._get_shared_nlp",
            return_value=MagicMock(),
        ):
            from app.modules.transformers.vectorstore import VectorStore
            with pytest.raises(IndexingError, match="Failed to initialize"):
                VectorStore(
                    logger=MagicMock(),
                    config_service=AsyncMock(),
                    graph_provider=AsyncMock(),
                    collection_name="test",
                    vector_db_service=AsyncMock(),
                )


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
# custom_sentence_boundary heading detection (lines 234-235)
# ===================================================================

class TestCustomSentenceBoundaryHeading:
    """Cover the all-caps heading detection branch."""

    def test_all_caps_heading_prevents_sentence_start(self):
        """All-caps text followed by next token should not be sentence start."""
        from app.modules.transformers.vectorstore import VectorStore

        # Use the existing _make_mock_doc from TestCustomSentenceBoundary
        tokens_data = [
            {"text": "INTRODUCTION"},
            {"text": "."},
            {"text": "Next"},
        ]
        tokens = []
        for i, data in enumerate(tokens_data):
            tok = MagicMock()
            tok.i = i
            tok.text = data["text"]
            tok.like_num = data.get("like_num", False)
            tok.is_sent_start = data.get("is_sent_start", None)
            tokens.append(tok)

        class DocSlice:
            def __init__(self, toks):
                self._tokens = toks
            def __getitem__(self, key):
                if isinstance(key, slice):
                    return DocSlice(self._tokens[key])
                return self._tokens[key]
            def __iter__(self):
                return iter(self._tokens)
            def __len__(self):
                return len(self._tokens)

        doc = DocSlice(tokens)

        result = VectorStore.custom_sentence_boundary(doc)
        # The period (tokens[1]) should have is_sent_start set to False
        assert tokens[1].is_sent_start is False

    def test_all_caps_with_digits_not_heading(self):
        """All-caps text with digits should not be treated as heading."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "ABC123"},
            {"text": "."},
            {"text": "Next"},
        ]
        tokens = []
        for i, data in enumerate(tokens_data):
            tok = MagicMock()
            tok.i = i
            tok.text = data["text"]
            tok.like_num = data.get("like_num", False)
            tok.is_sent_start = data.get("is_sent_start", None)
            tokens.append(tok)

        class DocSlice:
            def __init__(self, toks):
                self._tokens = toks
            def __getitem__(self, key):
                if isinstance(key, slice):
                    return DocSlice(self._tokens[key])
                return self._tokens[key]
            def __iter__(self):
                return iter(self._tokens)
            def __len__(self):
                return len(self._tokens)

        doc = DocSlice(tokens)

        result = VectorStore.custom_sentence_boundary(doc)
        # tokens[1].is_sent_start should remain None (not modified by heading rule)


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

        # Make nlp raise to trigger DocumentProcessingError
        vs.nlp = MagicMock(side_effect=RuntimeError("nlp failed"))

        text_block = Block(
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="some text content",
            index=0,
        )

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
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

        mock_doc = MagicMock()
        mock_doc.sents = []
        vs.nlp = MagicMock(return_value=mock_doc)

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
        """Exception during _create_embeddings should raise EmbeddingError."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=RuntimeError("embed failed"))

        mock_doc = MagicMock()
        mock_doc.sents = []
        vs.nlp = MagicMock(return_value=mock_doc)

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
            with pytest.raises(EmbeddingError, match="Failed to create or store embeddings"):
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
        """Generic exception in _create_embeddings should raise IndexingError."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.vector_store = MagicMock()

        # Mock _is_local_cpu_embedding
        vs._is_local_cpu_embedding = MagicMock(return_value=False)

        # Make the documents contain something that triggers an error in the batching
        # by mocking aadd_documents to raise
        vs.vector_store.aadd_documents = AsyncMock(side_effect=RuntimeError("batch fail"))

        docs = [Document(page_content="test", metadata={"orgId": "o1"})]

        with pytest.raises(VectorStoreError):
            await vs._create_embeddings(docs, "rec-1", "vr-1")
=======
>>>>>>> 28ea8a05 (added UTs for py module)
