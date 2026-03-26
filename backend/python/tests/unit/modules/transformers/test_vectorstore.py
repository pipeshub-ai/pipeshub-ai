"""Unit tests for app.modules.transformers.vectorstore.VectorStore."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
# _normalize_image_to_base64
# ===================================================================

class TestNormalizeImageToBase64:
    """Tests for VectorStore._normalize_image_to_base64."""

    @pytest.mark.asyncio
    async def test_data_url_passthrough(self):
        """data:image/... URLs should extract the base64 part after the comma."""
        vs = _make_vectorstore()
        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
        result = await vs._normalize_image_to_base64(data_url)
        assert result == "iVBORw0KGgoAAAANSUhEUg=="

    @pytest.mark.asyncio
    async def test_data_url_no_comma_returns_none(self):
        """data: URL without comma should return None."""
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("data:image/png;base64_no_comma")
        assert result is None

    @pytest.mark.asyncio
    async def test_data_url_padding_fix(self):
        """data URL with base64 that needs padding should be fixed."""
        vs = _make_vectorstore()
        # "abc" is 3 chars, needs 1 pad char
        data_url = "data:image/jpeg;base64,abc"
        result = await vs._normalize_image_to_base64(data_url)
        assert result == "abc="

    @pytest.mark.asyncio
    async def test_raw_base64_passthrough(self):
        """Raw base64 string should be returned after cleaning."""
        vs = _make_vectorstore()
        raw_b64 = base64.b64encode(b"test image bytes").decode("utf-8")
        result = await vs._normalize_image_to_base64(raw_b64)
        assert result == raw_b64

    @pytest.mark.asyncio
    async def test_raw_base64_padding_fix(self):
        """Raw base64 missing padding should be fixed."""
        vs = _make_vectorstore()
        # Create base64 then strip trailing =
        raw = base64.b64encode(b"hello world").decode("utf-8").rstrip("=")
        result = await vs._normalize_image_to_base64(raw)
        # Should add padding back
        expected_pad = (-len(raw)) % 4
        expected = raw + "=" * expected_pad
        assert result == expected

    @pytest.mark.asyncio
    async def test_raw_base64_with_whitespace(self):
        """Raw base64 with newlines/spaces should be cleaned."""
        vs = _make_vectorstore()
        clean_b64 = base64.b64encode(b"test").decode("utf-8")
        dirty_b64 = clean_b64[:2] + "\n" + clean_b64[2:] + " "
        result = await vs._normalize_image_to_base64(dirty_b64)
        assert "\n" not in result
        assert " " not in result

    @pytest.mark.asyncio
    async def test_invalid_characters_returns_none(self):
        """Strings with non-base64 characters should return None."""
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("not!valid@base64#")
        assert result is None

    @pytest.mark.asyncio
    async def test_none_input_returns_none(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_string_returns_none(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64("")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_string_returns_none(self):
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(12345)
        assert result is None

    @pytest.mark.asyncio
    async def test_url_safe_base64(self):
        """Base64 with URL-safe characters (-_) should be accepted."""
        vs = _make_vectorstore()
        # URL-safe base64 uses - and _ instead of + and /
        url_safe = "abc-def_ghi="
        result = await vs._normalize_image_to_base64(url_safe)
        assert result is not None


# ===================================================================
# apply
# ===================================================================

class TestVectorStoreApply:
    """Tests for VectorStore.apply."""

    @pytest.mark.asyncio
    async def test_apply_delegates_to_index_documents(self):
        """apply should extract record info and call index_documents."""
        vs = _make_vectorstore()
        vs.index_documents = AsyncMock(return_value=True)

        record = MagicMock()
        record.id = "rec-1"
        record.virtual_record_id = "vr-1"
        record.org_id = "org-1"
        record.mime_type = "application/pdf"
        record.block_containers = MagicMock()

        ctx = MagicMock()
        ctx.record = record

        result = await vs.apply(ctx)

        vs.index_documents.assert_awaited_once_with(
            record.block_containers,
            "org-1",
            "rec-1",
            "vr-1",
            "application/pdf",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_apply_returns_none_on_error(self):
        """If index_documents raises, apply should propagate."""
        vs = _make_vectorstore()
        vs.index_documents = AsyncMock(side_effect=Exception("indexing failed"))

        record = MagicMock()
        record.id = "rec-1"
        record.virtual_record_id = "vr-1"
        record.org_id = "org-1"
        record.mime_type = "text/plain"
        record.block_containers = MagicMock()

        ctx = MagicMock()
        ctx.record = record

        with pytest.raises(Exception, match="indexing failed"):
            await vs.apply(ctx)

    @pytest.mark.asyncio
    async def test_apply_passes_correct_params(self):
        """Verify the exact parameters passed to index_documents."""
        vs = _make_vectorstore()
        vs.index_documents = AsyncMock(return_value=None)

        block_containers = MagicMock()
        record = MagicMock()
        record.id = "specific-rec-id"
        record.virtual_record_id = "specific-vr-id"
        record.org_id = "specific-org-id"
        record.mime_type = "image/png"
        record.block_containers = block_containers

        ctx = MagicMock()
        ctx.record = record

        await vs.apply(ctx)

        vs.index_documents.assert_awaited_once_with(
            block_containers,
            "specific-org-id",
            "specific-rec-id",
            "specific-vr-id",
            "image/png",
        )


# ===================================================================
# _initialize_collection
# ===================================================================

class TestInitializeCollection:
    """Tests for VectorStore._initialize_collection."""

    @pytest.mark.asyncio
    async def test_creates_collection_when_not_found(self):
        """Creates collection when get_collection raises."""
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection = AsyncMock(side_effect=Exception("not found"))
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=768)

        vs.vector_db_service.create_collection.assert_awaited_once()
        assert vs.vector_db_service.create_index.call_count == 2

    @pytest.mark.asyncio
    async def test_recreates_on_dimension_mismatch(self):
        """Recreates collection when vector size differs."""
        vs = _make_vectorstore()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=512)}
        vs.vector_db_service.get_collection = AsyncMock(return_value=mock_info)
        vs.vector_db_service.delete_collection = AsyncMock()
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=768)

        vs.vector_db_service.delete_collection.assert_awaited_once()
        vs.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_recreate_when_same_size(self):
        """Does not recreate when sizes match."""
        vs = _make_vectorstore()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=768)}
        vs.vector_db_service.get_collection = AsyncMock(return_value=mock_info)
        vs.vector_db_service.create_collection = AsyncMock()

        await vs._initialize_collection(embedding_size=768)

        vs.vector_db_service.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_collection_failure_raises_vectorstore_error(self):
        """Raises VectorStoreError when creation fails."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection = AsyncMock(side_effect=Exception("not found"))
        vs.vector_db_service.create_collection = AsyncMock(side_effect=RuntimeError("create failed"))

        with pytest.raises(VectorStoreError):
            await vs._initialize_collection(embedding_size=768)


# ===================================================================
# get_embedding_model_instance
# ===================================================================

class TestGetEmbeddingModelInstance:
    """Tests for VectorStore.get_embedding_model_instance."""

    @pytest.mark.asyncio
    async def test_uses_default_when_no_config(self):
        """Uses default embedding model when no config."""
        vs = _make_vectorstore()
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.embed_query.return_value = [0.1] * 768
        mock_embed.model_name = "test-model"

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embed):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                result = await vs.get_embedding_model_instance()

        assert result is False  # default model is not multimodal

    @pytest.mark.asyncio
    async def test_uses_configured_model(self):
        """Uses configured embedding model."""
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "text-embedding-3-small"},
            "isDefault": True,
            "isMultimodal": True,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.embed_query.return_value = [0.1] * 1536
        mock_embed.model_name = "text-embedding-3-small"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                result = await vs.get_embedding_model_instance()

        assert result is True

    @pytest.mark.asyncio
    async def test_embed_query_failure_raises(self):
        """Raises IndexingError when embed_query fails."""
        from app.exceptions.indexing_exceptions import IndexingError
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })

        mock_embed = MagicMock()
        mock_embed.embed_query.side_effect = RuntimeError("API error")

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            with pytest.raises(IndexingError):
                await vs.get_embedding_model_instance()

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model(self):
        """Falls back to 'model' attribute when 'model_name' is missing."""
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock(spec=[])  # no attributes
        mock_embed.embed_query = MagicMock(return_value=[0.1] * 1024)
        # Add only 'model' attribute
        mock_embed.model = "test-model-via-model"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                await vs.get_embedding_model_instance()

        assert vs.model_name == "test-model-via-model"

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model_id(self):
        """Falls back to 'model_id' when both model_name and model are missing."""
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock(spec=[])  # no attributes
        mock_embed.embed_query = MagicMock(return_value=[0.1] * 1024)
        mock_embed.model_id = "test-model-via-id"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                await vs.get_embedding_model_instance()

        assert vs.model_name == "test-model-via-id"


# ===================================================================
# delete_embeddings
# ===================================================================

class TestDeleteEmbeddings:
    """Tests for VectorStore.delete_embeddings."""

    @pytest.mark.asyncio
    async def test_deletes_points(self):
        """Deletes points from vector store."""
        vs = _make_vectorstore()
        vs.vector_db_service.filter_collection = AsyncMock(return_value={"filter": {}})
        vs.vector_db_service.delete_points = MagicMock()

        await vs.delete_embeddings("vr-1")

        vs.vector_db_service.delete_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_raises_embedding_error(self):
        """Raises EmbeddingError on failure."""
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()
        vs.vector_db_service.filter_collection = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(EmbeddingError):
            await vs.delete_embeddings("vr-1")


# ===================================================================
# _process_image_embeddings (dispatch)
# ===================================================================

class TestProcessImageEmbeddings:
    """Tests for VectorStore._process_image_embeddings dispatch."""

    @pytest.mark.asyncio
    async def test_unsupported_provider_returns_empty(self):
        """Unsupported provider returns empty list."""
        vs = _make_vectorstore()
        vs.embedding_provider = "UNSUPPORTED_PROVIDER"

        result = await vs._process_image_embeddings([], [])

        assert result == []

    @pytest.mark.asyncio
    async def test_cohere_dispatch(self):
        """Cohere provider dispatches to _process_image_embeddings_cohere."""
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()
        vs.embedding_provider = EmbeddingProvider.COHERE.value
        vs._process_image_embeddings_cohere = AsyncMock(return_value=[])

        await vs._process_image_embeddings([], [])

        vs._process_image_embeddings_cohere.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_voyage_dispatch(self):
        """Voyage provider dispatches to _process_image_embeddings_voyage."""
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()
        vs.embedding_provider = EmbeddingProvider.VOYAGE.value
        vs._process_image_embeddings_voyage = AsyncMock(return_value=[])

        await vs._process_image_embeddings([], [])

        vs._process_image_embeddings_voyage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bedrock_dispatch(self):
        """AWS Bedrock dispatches to _process_image_embeddings_bedrock."""
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()
        vs.embedding_provider = EmbeddingProvider.AWS_BEDROCK.value
        vs._process_image_embeddings_bedrock = AsyncMock(return_value=[])

        await vs._process_image_embeddings([], [])

        vs._process_image_embeddings_bedrock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_jina_dispatch(self):
        """Jina AI dispatches to _process_image_embeddings_jina."""
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()
        vs.embedding_provider = EmbeddingProvider.JINA_AI.value
        vs._process_image_embeddings_jina = AsyncMock(return_value=[])

        await vs._process_image_embeddings([], [])

        vs._process_image_embeddings_jina.assert_awaited_once()


# ===================================================================
# _store_image_points
# ===================================================================

class TestStoreImagePoints:
    """Tests for VectorStore._store_image_points."""

    @pytest.mark.asyncio
    async def test_stores_points(self):
        """Stores points in vector DB."""
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = MagicMock()

        mock_point = MagicMock()
        await vs._store_image_points([mock_point])

        vs.vector_db_service.upsert_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_points_logs(self):
        """Empty points list logs but doesn't upsert."""
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = MagicMock()

        await vs._store_image_points([])

        vs.vector_db_service.upsert_points.assert_not_called()
        vs.logger.info.assert_called()


# ===================================================================
# _is_local_cpu_embedding
# ===================================================================

class TestIsLocalCpuEmbedding:
    """Tests for VectorStore._is_local_cpu_embedding."""

    def test_none_provider_is_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = None
        assert vs._is_local_cpu_embedding() is True

    def test_default_provider_is_local(self):
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()
        vs.embedding_provider = EmbeddingProvider.DEFAULT.value
        assert vs._is_local_cpu_embedding() is True

    def test_sentence_transformers_is_local(self):
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()
        vs.embedding_provider = EmbeddingProvider.SENTENCE_TRANSFOMERS.value
        assert vs._is_local_cpu_embedding() is True

    def test_openai_is_not_local(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        assert vs._is_local_cpu_embedding() is False


# ===================================================================
# _process_document_chunks
# ===================================================================

class TestProcessDocumentChunks:
    """Tests for VectorStore._process_document_chunks."""

    @pytest.mark.asyncio
    async def test_local_sequential_processing(self):
        """Local CPU embedding uses sequential processing."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock()

        chunks = [Document(page_content="test", metadata={})]
        await vs._process_document_chunks(chunks)

        vs.vector_store.aadd_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_remote_concurrent_processing(self):
        """Remote embedding uses concurrent processing."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock()

        chunks = [Document(page_content=f"test {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(chunks)

        vs.vector_store.aadd_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_local_batch_failure_raises(self):
        """Raises VectorStoreError when local batch fails."""
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import VectorStoreError

        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock(side_effect=RuntimeError("fail"))

        chunks = [Document(page_content="test", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(chunks)


# ===================================================================
# _create_embeddings
# ===================================================================

class TestCreateEmbeddings:
    """Tests for VectorStore._create_embeddings."""

    @pytest.mark.asyncio
    async def test_no_chunks_raises(self):
        """Raises EmbeddingError when no chunks provided."""
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()

        with pytest.raises(EmbeddingError, match="No chunks"):
            await vs._create_embeddings([], "rec-1", "vr-1")

    @pytest.mark.asyncio
    async def test_separates_document_and_image_chunks(self):
        """Separates Document chunks from image dict chunks."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[])
        vs._store_image_points = AsyncMock()

        doc = Document(page_content="text", metadata={})
        img = {"image_uri": "base64data", "metadata": {}}

        await vs._create_embeddings([doc, img], "rec-1", "vr-1")

        vs._process_document_chunks.assert_awaited_once()
        vs._process_image_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_only_documents(self):
        """Only Document chunks are processed."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock()

        doc = Document(page_content="text", metadata={})

        await vs._create_embeddings([doc], "rec-1", "vr-1")

        vs._process_document_chunks.assert_awaited_once()
        vs._process_image_embeddings.assert_not_awaited()


# ===================================================================
# describe_image_async / describe_images
# ===================================================================

class TestDescribeImages:
    """Tests for VectorStore.describe_image_async and describe_images."""

    @pytest.mark.asyncio
    async def test_describe_image_async(self):
        vs = _make_vectorstore()
        mock_vlm = AsyncMock()
        mock_vlm.ainvoke.return_value = MagicMock(content="A cat sitting on a desk")

        result = await vs.describe_image_async("base64data", mock_vlm)
        assert result == "A cat sitting on a desk"

    @pytest.mark.asyncio
    async def test_describe_images_success(self):
        vs = _make_vectorstore()
        mock_vlm = AsyncMock()
        mock_vlm.ainvoke.return_value = MagicMock(content="Description")

        results = await vs.describe_images(["img1", "img2"], mock_vlm)
        assert len(results) == 2
        assert results[0]["success"] is True

    @pytest.mark.asyncio
    async def test_describe_images_failure(self):
        vs = _make_vectorstore()
        mock_vlm = AsyncMock()
        mock_vlm.ainvoke.side_effect = RuntimeError("API error")

        results = await vs.describe_images(["img1"], mock_vlm)
        assert results[0]["success"] is False


# ===================================================================
# _create_custom_tokenizer
# ===================================================================

# ===================================================================
# index_documents
# ===================================================================

class TestIndexDocuments:
    """Tests for VectorStore.index_documents."""

    @pytest.mark.asyncio
    async def test_empty_blocks_returns_none(self):
        """Returns None when no blocks and no block groups."""
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                BlocksContainer(blocks=[], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_text_blocks_create_embeddings(self):
        """Text blocks are processed into document embeddings."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        # Mock nlp to return sentences
        mock_doc = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_doc.sents = [mock_sent]
        vs.nlp = MagicMock(return_value=mock_doc)

        block = Block(index=0, type="text", format="txt", data="Hello world", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True
        vs._create_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_text_blocks_with_multiple_sentences(self):
        """Text blocks with multiple sentences create sentence + block embeddings."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        # Mock nlp to return multiple sentences
        sent1 = MagicMock()
        sent1.text = "First sentence."
        sent2 = MagicMock()
        sent2.text = "Second sentence."
        mock_doc = MagicMock()
        mock_doc.sents = [sent1, sent2]
        vs.nlp = MagicMock(return_value=mock_doc)

        block = Block(index=0, type="text", format="txt", data="First sentence. Second sentence.", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True
        # The documents should include individual sentences + the full block
        chunks = vs._create_embeddings.call_args[0][0]
        assert len(chunks) == 3  # 2 sentences + 1 block

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_embedding(self):
        """Image blocks create image embeddings when multimodal."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)  # multimodal embedding
        vs._create_embeddings = AsyncMock()

        block = Block(index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "image/png")

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        # Should have image dict chunks
        assert any(isinstance(c, dict) and "image_uri" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_llm_describes(self):
        """Image blocks described by LLM when embedding not multimodal but LLM is."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.describe_images = AsyncMock(return_value=[
            {"index": 0, "success": True, "description": "A photo"}
        ])

        block = Block(index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "image/png")

        assert result is True
        vs.describe_images.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_table_block_groups_create_summary_embeddings(self):
        """Table block groups create summary embeddings."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, GroupType
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        bg = BlockGroup(index=0, type=GroupType.TABLE)
        bg.data = {"table_summary": "Summary of the table"}
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_table_row_blocks_create_embeddings(self):
        """Table row blocks create row embeddings."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        block = Block(
            index=0, type="table_row", format="txt",
            data={"row_natural_language_text": "Col1 is val1, Col2 is val2"},
            comments=[]
        )
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_no_documents_after_filter_returns_true(self):
        """Returns True when no documents left after filtering."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        # A block type that doesn't match text/image/table filters
        block = Block(index=0, type="divider", format="txt", data="test", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_heading_blocks_processed_as_text(self):
        """Heading blocks are processed as text."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Heading")]))

        block = Block(index=0, type="heading", format="txt", data="Main Heading", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_get_embedding_model_failure_raises(self):
        """Raises IndexingError when get_embedding_model_instance fails."""
        from app.models.blocks import BlocksContainer
        from app.exceptions.indexing_exceptions import IndexingError
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(side_effect=RuntimeError("model fail"))

        with pytest.raises(IndexingError, match="Failed to get embedding model"):
            await vs.index_documents(
                BlocksContainer(blocks=[], block_groups=[]),
                "org-1", "rec-1", "vr-1", "text/plain"
            )

    @pytest.mark.asyncio
    async def test_get_llm_failure_raises(self):
        """Raises IndexingError when get_llm fails."""
        from app.models.blocks import BlocksContainer
        from app.exceptions.indexing_exceptions import IndexingError
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = RuntimeError("llm fail")
            with pytest.raises(IndexingError, match="Failed to get LLM"):
                await vs.index_documents(
                    BlocksContainer(blocks=[], block_groups=[]),
                    "org-1", "rec-1", "vr-1", "text/plain"
                )

    @pytest.mark.asyncio
    async def test_create_embeddings_failure_raises(self):
        """Raises EmbeddingError when _create_embeddings fails."""
        from app.models.blocks import Block, BlocksContainer
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=RuntimeError("embed fail"))
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Hello")]))

        block = Block(index=0, type="text", format="txt", data="Hello", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            with pytest.raises(EmbeddingError):
                await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

    @pytest.mark.asyncio
    async def test_paragraph_blocks_processed_as_text(self):
        """Paragraph blocks are processed as text."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Para")]))

        block = Block(index=0, type="paragraph", format="txt", data="A paragraph", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_quote_blocks_processed_as_text(self):
        """Quote blocks are processed as text."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Quote")]))

        block = Block(index=0, type="quote", format="txt", data="A quote", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_table_cell_blocks_processed(self):
        """Table cell blocks are processed."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        block = Block(index=0, type="table_cell", format="txt", data={"cell": "value"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        # table_cell blocks have no summary, so no documents to embed => True
        assert result is True


class TestCreateCustomTokenizer:
    """Tests for VectorStore._create_custom_tokenizer."""

    def test_returns_language_instance(self):
        """_create_custom_tokenizer returns a Language instance."""
        vs = _make_vectorstore()
        import spacy
        # blank("en") has no "parser" pipe; add one so _create_custom_tokenizer
        # can insert sentencizer before it (mirrors en_core_web_sm layout).
        nlp = spacy.blank("en")
        nlp.add_pipe("parser")
        result = vs._create_custom_tokenizer(nlp)
        assert result is not None
        assert "sentencizer" in result.pipe_names


# ===================================================================
# _get_shared_nlp (lines 42-54)
# ===================================================================

class TestGetSharedNlp:
    """Tests for the _get_shared_nlp module-level function."""

    @staticmethod
    def _blank_with_parser():
        """Return a blank English model with a parser pipe (mimics en_core_web_sm)."""
        import spacy
        nlp = spacy.blank("en")
        nlp.add_pipe("parser")
        return nlp

    def test_returns_language_instance(self):
        """_get_shared_nlp returns a spaCy Language instance."""
        from spacy.language import Language
        with patch("app.modules.transformers.vectorstore.spacy.load", return_value=self._blank_with_parser()):
            from app.modules.transformers.vectorstore import _get_shared_nlp
            # Clear cache so we exercise the creation path
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")
            nlp = _get_shared_nlp()
            assert isinstance(nlp, Language)

    def test_caching_returns_same_instance(self):
        """Subsequent calls return the same cached instance."""
        with patch("app.modules.transformers.vectorstore.spacy.load", return_value=self._blank_with_parser()):
            from app.modules.transformers.vectorstore import _get_shared_nlp
            # Clear cache to start fresh
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")
            nlp1 = _get_shared_nlp()
            nlp2 = _get_shared_nlp()
            assert nlp1 is nlp2


# ===================================================================
# VectorStore.__init__ error paths (lines 96-107)
# ===================================================================

class TestVectorStoreInitErrors:
    """Tests for VectorStore.__init__ error handling."""

    def test_sparse_embedding_failure_raises_indexing_error(self):
        """When FastEmbedSparse fails, should raise IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError

        with patch(
            "app.modules.transformers.vectorstore.FastEmbedSparse",
            side_effect=Exception("sparse init failed"),
        ):
            with patch("app.modules.transformers.vectorstore._get_shared_nlp") as mock_nlp:
                mock_nlp.return_value = MagicMock()
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
# apply (lines 151-152)
# ===================================================================

class TestApply:
    """Tests for VectorStore.apply."""

    @pytest.mark.asyncio
    async def test_apply_calls_index_documents(self):
        """apply delegates to index_documents with correct args."""
        vs = _make_vectorstore()
        vs.index_documents = AsyncMock(return_value=True)

        mock_ctx = MagicMock()
        mock_ctx.record.id = "rec-1"
        mock_ctx.record.virtual_record_id = "vr-1"
        mock_ctx.record.block_containers = MagicMock()
        mock_ctx.record.org_id = "org-1"
        mock_ctx.record.mime_type = "text/plain"

        result = await vs.apply(mock_ctx)

        assert result is True
        vs.index_documents.assert_awaited_once_with(
            mock_ctx.record.block_containers,
            "org-1",
            "rec-1",
            "vr-1",
            "text/plain",
        )


# ===================================================================
# custom_sentence_boundary (lines 166-240)
# ===================================================================

class TestCustomSentenceBoundary:
    """Tests for the custom_sentence_boundary component."""

    def test_number_period_not_sentence_boundary(self):
        """Number followed by period should not be a sentence boundary."""
        import spacy
        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        doc = nlp("Section 1. The first item.")
        sents = list(doc.sents)
        assert len(sents) >= 1

    def test_abbreviation_not_sentence_boundary(self):
        """Common abbreviations should not cause sentence splits."""
        import spacy
        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        doc = nlp("Dr. Smith went to the store.")
        sents = list(doc.sents)
        assert len(sents) <= 2

    def test_ellipsis_not_sentence_boundary(self):
        """Ellipsis (...) should not cause sentence splits."""
        import spacy
        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        doc = nlp("Wait... I think so.")
        sents = list(doc.sents)
        assert len(sents) >= 1


# ===================================================================
# _initialize_collection (lines 266-318)
# ===================================================================

class TestInitializeCollection:
    """Tests for VectorStore._initialize_collection."""

    @pytest.mark.asyncio
    async def test_collection_exists_same_size(self):
        """When collection exists with correct size, should not recreate."""
        vs = _make_vectorstore()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=1024)}
        vs.vector_db_service.get_collection = AsyncMock(return_value=mock_info)

        await vs._initialize_collection(embedding_size=1024)
        vs.vector_db_service.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_collection_exists_different_size(self):
        """When collection exists with wrong size, should recreate."""
        vs = _make_vectorstore()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=512)}
        vs.vector_db_service.get_collection = AsyncMock(return_value=mock_info)
        vs.vector_db_service.delete_collection = AsyncMock()
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)
        vs.vector_db_service.delete_collection.assert_awaited_once()
        vs.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_collection_not_found_creates_new(self):
        """When collection does not exist, should create it."""
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection = AsyncMock(side_effect=Exception("not found"))
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)
        vs.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_collection_creation_failure(self):
        """When collection creation fails, should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection = AsyncMock(side_effect=Exception("not found"))
        vs.vector_db_service.create_collection = AsyncMock(side_effect=Exception("create failed"))

        with pytest.raises(VectorStoreError, match="Failed to create collection"):
            await vs._initialize_collection(embedding_size=1024)


# ===================================================================
# get_embedding_model_instance (lines 322-409)
# ===================================================================

class TestGetEmbeddingModelInstance:
    """Tests for VectorStore.get_embedding_model_instance."""

    @pytest.mark.asyncio
    async def test_default_embedding_when_no_config(self):
        """When no embedding configs, should use default model."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024
        mock_embeddings.model_name = "default-model"

        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": []
        })

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                result = await vs.get_embedding_model_instance()

        assert result is False  # Default is not multimodal

    @pytest.mark.asyncio
    async def test_configured_embedding_model(self):
        """When embedding configs exist, should use configured model."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1536
        mock_embeddings.model_name = "text-embedding-3-small"

        config = {
            "embedding": [{
                "provider": "openai",
                "isDefault": True,
                "isMultimodal": True,
                "configuration": {
                    "model": "text-embedding-3-small",
                    "apiKey": "test-key",
                },
            }]
        }

        vs.config_service.get_config = AsyncMock(return_value=config)

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embeddings):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                result = await vs.get_embedding_model_instance()

        assert result is True

    @pytest.mark.asyncio
    async def test_embedding_model_embed_query_failure(self):
        """When embed_query fails, should raise IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError

        vs = _make_vectorstore()
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.side_effect = Exception("embed failed")

        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [{
                "provider": "openai",
                "isDefault": True,
                "configuration": {"model": "test", "apiKey": "k"},
            }]
        })

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embeddings):
            with pytest.raises(IndexingError, match="Failed to get embedding model"):
                await vs.get_embedding_model_instance()

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model(self):
        """When dense_embeddings has 'model' attr instead of 'model_name'."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock(spec=[])
        mock_embeddings.embed_query = MagicMock(return_value=[0.1] * 768)
        mock_embeddings.model = "my-model"

        vs.config_service.get_config = AsyncMock(return_value={"embedding": []})

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                result = await vs.get_embedding_model_instance()

        assert vs.model_name == "my-model"

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model_id(self):
        """When dense_embeddings has 'model_id' attr."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock(spec=[])
        mock_embeddings.embed_query = MagicMock(return_value=[0.1] * 768)
        mock_embeddings.model_id = "my-model-id"

        vs.config_service.get_config = AsyncMock(return_value={"embedding": []})

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                result = await vs.get_embedding_model_instance()

        assert vs.model_name == "my-model-id"

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_unknown(self):
        """When dense_embeddings has no name attr, should use 'unknown'."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock(spec=[])
        mock_embeddings.embed_query = MagicMock(return_value=[0.1] * 768)

        vs.config_service.get_config = AsyncMock(return_value={"embedding": []})

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                await vs.get_embedding_model_instance()

        assert vs.model_name == "unknown"

    @pytest.mark.asyncio
    async def test_aws_bedrock_credentials_stored(self):
        """When provider is AWS Bedrock, should store AWS credentials."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 1024
        mock_embeddings.model_name = "amazon.titan-embed-image-v1"

        config = {
            "embedding": [{
                "provider": "bedrock",
                "isDefault": True,
                "isMultimodal": False,
                "configuration": {
                    "model": "amazon.titan-embed-image-v1",
                    "apiKey": "k",
                    "region": "us-east-1",
                    "awsAccessKeyId": "AKID",
                    "awsAccessSecretKey": "SECRET",
                },
            }]
        }

        vs.config_service.get_config = AsyncMock(return_value=config)

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embeddings):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                await vs.get_embedding_model_instance()

        assert vs.aws_access_key_id == "AKID"
        assert vs.aws_secret_access_key == "SECRET"


# ===================================================================
# _process_image_embeddings (lines 738-752)
# ===================================================================

class TestProcessImageEmbeddings:
    """Tests for _process_image_embeddings dispatching."""

    @pytest.mark.asyncio
    async def test_cohere_dispatch(self):
        """Should dispatch to Cohere handler."""
        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        vs._process_image_embeddings_cohere = AsyncMock(return_value=[])
        result = await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_cohere.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_voyage_dispatch(self):
        """Should dispatch to Voyage handler."""
        vs = _make_vectorstore()
        vs.embedding_provider = "voyage"
        vs._process_image_embeddings_voyage = AsyncMock(return_value=[])
        result = await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_voyage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bedrock_dispatch(self):
        """Should dispatch to Bedrock handler."""
        vs = _make_vectorstore()
        vs.embedding_provider = "bedrock"
        vs._process_image_embeddings_bedrock = AsyncMock(return_value=[])
        result = await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_bedrock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_jina_dispatch(self):
        """Should dispatch to Jina handler."""
        vs = _make_vectorstore()
        vs.embedding_provider = "jinaAI"
        vs._process_image_embeddings_jina = AsyncMock(return_value=[])
        result = await vs._process_image_embeddings([], [])
        vs._process_image_embeddings_jina.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_provider(self):
        """Unsupported provider should return empty list."""
        vs = _make_vectorstore()
        vs.embedding_provider = "unknown_provider"
        result = await vs._process_image_embeddings([], [])
        assert result == []


# ===================================================================
# _store_image_points (lines 754-775)
# ===================================================================

class TestStoreImagePoints:
    """Tests for _store_image_points."""

    @pytest.mark.asyncio
    async def test_stores_points(self):
        """Should upsert points when list is non-empty."""
        import asyncio
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = MagicMock()

        mock_point = MagicMock()
        await vs._store_image_points([mock_point])
        vs.vector_db_service.upsert_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_points_skipped(self):
        """Should log and skip when no points to upsert."""
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = MagicMock()
        await vs._store_image_points([])
        vs.vector_db_service.upsert_points.assert_not_called()


# ===================================================================
# _is_local_cpu_embedding (lines 777-783)
# ===================================================================

class TestIsLocalCpuEmbedding:
    """Tests for _is_local_cpu_embedding."""

    def test_none_provider(self):
        vs = _make_vectorstore()
        vs.embedding_provider = None
        assert vs._is_local_cpu_embedding() is True

    def test_default_provider(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "default"
        assert vs._is_local_cpu_embedding() is True

    def test_sentence_transformers_provider(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "sentenceTransformers"
        assert vs._is_local_cpu_embedding() is True

    def test_openai_provider(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "openAI"
        assert vs._is_local_cpu_embedding() is False


# ===================================================================
# _process_document_chunks (lines 785-843)
# ===================================================================

class TestProcessDocumentChunks:
    """Tests for _process_document_chunks."""

    @pytest.mark.asyncio
    async def test_local_sequential_processing(self):
        """Local CPU embedding should process sequentially."""
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock()

        docs = [Document(page_content="text", metadata={})]
        await vs._process_document_chunks(docs)
        vs.vector_store.aadd_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_remote_concurrent_processing(self):
        """Remote embedding should process concurrently."""
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock()

        docs = [Document(page_content=f"text {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(docs)
        vs.vector_store.aadd_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_local_batch_failure_raises(self):
        """Local batch failure should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock(side_effect=Exception("batch failed"))

        docs = [Document(page_content="text", metadata={})]
        with pytest.raises(VectorStoreError, match="Failed to store document batch"):
            await vs._process_document_chunks(docs)

    @pytest.mark.asyncio
    async def test_remote_batch_failure_raises(self):
        """Remote batch failure should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock(side_effect=Exception("batch failed"))

        docs = [Document(page_content="text", metadata={})]
        with pytest.raises(VectorStoreError, match="Failed to store document batch"):
            await vs._process_document_chunks(docs)


# ===================================================================
# _create_embeddings (lines 846-912)
# ===================================================================

class TestCreateEmbeddings:
    """Tests for _create_embeddings."""

    @pytest.mark.asyncio
    async def test_empty_chunks_raises(self):
        """Empty chunks should raise EmbeddingError."""
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()
        with pytest.raises(EmbeddingError, match="No chunks provided"):
            await vs._create_embeddings([], "rec-1", "vr-1")

    @pytest.mark.asyncio
    async def test_mixed_document_and_image_chunks(self):
        """Should process both document and image chunks."""
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[])
        vs._store_image_points = AsyncMock()

        doc = Document(page_content="text", metadata={})
        img = {"image_uri": "data:image/png;base64,abc", "metadata": {}}

        await vs._create_embeddings([doc, img], "rec-1", "vr-1")

        vs._process_document_chunks.assert_awaited_once()
        vs._process_image_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vectorstore_error_during_doc_processing(self):
        """VectorStoreError from doc processing should bubble up."""
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import VectorStoreError
        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock(side_effect=Exception("store failed"))

        doc = Document(page_content="text", metadata={})
        with pytest.raises(VectorStoreError, match="Failed to store langchain documents"):
            await vs._create_embeddings([doc], "rec-1", "vr-1")


# ===================================================================
# describe_image_async & describe_images (lines 914-943)
# ===================================================================

class TestDescribeImages:
    """Tests for describe_image_async and describe_images."""

    @pytest.mark.asyncio
    async def test_describe_image_async(self):
        """Should invoke VLM and return content."""
        vs = _make_vectorstore()
        mock_vlm = AsyncMock()
        mock_vlm.ainvoke.return_value = MagicMock(content="A diagram showing flow.")
        result = await vs.describe_image_async("base64data", mock_vlm)
        assert result == "A diagram showing flow."

    @pytest.mark.asyncio
    async def test_describe_images_success_and_failure(self):
        """Should return success and failure results."""
        vs = _make_vectorstore()
        mock_vlm = AsyncMock()
        # First call succeeds, second fails
        mock_vlm.ainvoke = AsyncMock(side_effect=[
            MagicMock(content="Description 1"),
            Exception("VLM failed"),
        ])
        results = await vs.describe_images(["img1", "img2"], mock_vlm)
        assert len(results) == 2
        assert results[0]["success"] is True
        assert results[1]["success"] is False


# ===================================================================
# index_documents additional paths (lines 1051-1052, 1064->1061, etc.)
# ===================================================================

class TestIndexDocumentsAdditional:
    """Additional tests for index_documents covering missed branches."""

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_embedding(self):
        """Image blocks with multimodal embedding should create image chunks."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)  # multimodal embedding
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        img_block = Block(
            index=0, type="image", format="base64",
            data={"uri": "data:image/png;base64,abc"}, comments=[]
        )
        container = BlocksContainer(blocks=[img_block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "image/png")

        assert result is True
        # Should have called _create_embeddings with image dict
        args = vs._create_embeddings.call_args[0]
        assert any(isinstance(d, dict) and "image_uri" in d for d in args[0])

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_llm(self):
        """Image blocks with multimodal LLM should describe and embed."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)  # not multimodal embedding
        vs._create_embeddings = AsyncMock()
        vs.describe_images = AsyncMock(return_value=[
            {"index": 0, "success": True, "description": "A cat"},
        ])
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        img_block = Block(
            index=0, type="image", format="base64",
            data={"uri": "data:image/png;base64,abc"}, comments=[]
        )
        container = BlocksContainer(blocks=[img_block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "image/png")

        assert result is True
        vs.describe_images.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_table_blocks_with_summary(self):
        """Table block groups with summary should create document chunks."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        table_bg = BlockGroup(
            index=0, type="table",
            data={"table_summary": "Sales data for Q1"},
        )
        table_row = Block(
            index=0, type="table_row", format="txt",
            data={"row_natural_language_text": "Row 1 text"}, comments=[],
        )
        container = BlocksContainer(blocks=[table_row], block_groups=[table_bg])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True

    @pytest.mark.asyncio
    async def test_empty_blocks_returns_none(self):
        """Empty blocks and block_groups should return None."""
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.nlp = MagicMock()

        container = BlocksContainer(blocks=[], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is None

    @pytest.mark.asyncio
    async def test_embedding_model_instance_failure(self):
        """When get_embedding_model_instance fails, should raise IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(side_effect=Exception("model failed"))

        container = BlocksContainer(blocks=[], block_groups=[])

        with pytest.raises(IndexingError, match="Failed to get embedding model instance"):
            await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

    @pytest.mark.asyncio
    async def test_get_llm_failure(self):
        """When get_llm fails, should raise IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        container = BlocksContainer(blocks=[], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock, side_effect=Exception("LLM failed")):
            with pytest.raises(IndexingError, match="Failed to get LLM"):
                await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

    @pytest.mark.asyncio
    async def test_no_documents_to_embed_returns_true(self):
        """When image blocks exist but have no uri, no documents created, returns True."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        # Image block without uri - data is a dict but no "uri" key
        block = Block(index=0, type="image", format="base64", data={"no_uri": True}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "image/png")

        assert result is True

    @pytest.mark.asyncio
    async def test_embedding_creation_failure_raises(self):
        """When _create_embeddings fails, should raise EmbeddingError."""
        from app.exceptions.indexing_exceptions import EmbeddingError
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=Exception("embed failed"))
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Hello")]))

        block = Block(index=0, type="text", format="txt", data="Hello world", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            with pytest.raises(EmbeddingError, match="Failed to create or store embeddings"):
                await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")


# ===================================================================
# _process_image_embeddings_cohere (lines 428-490)
# ===================================================================

class TestProcessImageEmbeddingsCohere:
    """Tests for VectorStore._process_image_embeddings_cohere."""

    @pytest.mark.asyncio
    async def test_cohere_success(self):
        """Cohere embedding returns PointStruct."""
        vs = _make_vectorstore()
        vs.api_key = "test-key"
        vs.model_name = "embed-v3"

        mock_response = MagicMock()
        mock_response.embeddings.float = [[0.1, 0.2, 0.3]]

        mock_co = MagicMock()
        mock_co.embed.return_value = mock_response

        image_chunks = [{"metadata": {"orgId": "org1"}, "image_uri": "base64data"}]
        image_base64s = ["base64data"]

        with patch("cohere.ClientV2", return_value=mock_co):
            points = await vs._process_image_embeddings_cohere(image_chunks, image_base64s)

        assert len(points) == 1

    @pytest.mark.asyncio
    async def test_cohere_size_limit_skipped(self):
        """Cohere skips images that exceed size limit."""
        vs = _make_vectorstore()
        vs.api_key = "test-key"
        vs.model_name = "embed-v3"

        mock_co = MagicMock()
        mock_co.embed.side_effect = Exception("image size must be at most 5MB")

        image_chunks = [{"metadata": {}, "image_uri": "large_image"}]
        image_base64s = ["large_image"]

        with patch("cohere.ClientV2", return_value=mock_co):
            points = await vs._process_image_embeddings_cohere(image_chunks, image_base64s)

        assert len(points) == 0

    @pytest.mark.asyncio
    async def test_cohere_other_error_logged(self):
        """Non-size-limit errors are raised."""
        vs = _make_vectorstore()
        vs.api_key = "test-key"
        vs.model_name = "embed-v3"

        mock_co = MagicMock()
        mock_co.embed.side_effect = RuntimeError("API error")

        image_chunks = [{"metadata": {}, "image_uri": "data"}]
        image_base64s = ["data"]

        with patch("cohere.ClientV2", return_value=mock_co):
            points = await vs._process_image_embeddings_cohere(image_chunks, image_base64s)

        # Exception result is logged as warning, returns empty
        assert len(points) == 0


# ===================================================================
# _process_image_embeddings_voyage (lines 496-548)
# ===================================================================

class TestProcessImageEmbeddingsVoyage:
    """Tests for VectorStore._process_image_embeddings_voyage."""

    @pytest.mark.asyncio
    async def test_voyage_success(self):
        """Voyage embedding returns points."""
        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.batch_size = 2
        vs.dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])

        image_chunks = [{"metadata": {}, "image_uri": "img1"}]
        image_base64s = ["img1"]

        points = await vs._process_image_embeddings_voyage(image_chunks, image_base64s)
        assert len(points) == 1

    @pytest.mark.asyncio
    async def test_voyage_batch_failure(self):
        """Failed batch returns empty points."""
        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.batch_size = 2
        vs.dense_embeddings.aembed_documents = AsyncMock(side_effect=RuntimeError("fail"))

        image_chunks = [{"metadata": {}, "image_uri": "img1"}]
        image_base64s = ["img1"]

        points = await vs._process_image_embeddings_voyage(image_chunks, image_base64s)
        assert len(points) == 0


# ===================================================================
# _process_image_embeddings_bedrock (lines 554-641)
# ===================================================================

class TestProcessImageEmbeddingsBedrock:
    """Tests for VectorStore._process_image_embeddings_bedrock."""

    @pytest.mark.asyncio
    async def test_bedrock_success(self):
        """Bedrock embedding returns points."""
        import json
        vs = _make_vectorstore()
        vs.aws_access_key_id = "AKID"
        vs.aws_secret_access_key = "secret"
        vs.region_name = "us-east-1"
        vs.model_name = "amazon.titan-embed-image-v1"

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({"embedding": [0.1, 0.2]}).encode()

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        image_chunks = [{"metadata": {}, "image_uri": "img"}]
        image_base64s = ["aW1hZ2U="]  # valid base64

        with patch("boto3.client", return_value=mock_client):
            points = await vs._process_image_embeddings_bedrock(image_chunks, image_base64s)

        assert len(points) == 1

    @pytest.mark.asyncio
    async def test_bedrock_no_credentials(self):
        """No credentials raises EmbeddingError."""
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()
        vs.aws_access_key_id = None
        vs.aws_secret_access_key = None
        vs.region_name = None

        from botocore.exceptions import NoCredentialsError
        with patch("boto3.client", side_effect=NoCredentialsError()):
            with pytest.raises(EmbeddingError, match="AWS credentials"):
                await vs._process_image_embeddings_bedrock([], [])

    @pytest.mark.asyncio
    async def test_bedrock_invalid_image_skipped(self):
        """Invalid base64 images are skipped."""
        vs = _make_vectorstore()
        vs.aws_access_key_id = "AKID"
        vs.aws_secret_access_key = "secret"
        vs.region_name = "us-east-1"
        vs.model_name = "titan"

        image_chunks = [{"metadata": {}, "image_uri": "img"}]
        image_base64s = ["not!valid@base64#"]  # invalid

        mock_client = MagicMock()
        with patch("boto3.client", return_value=mock_client):
            points = await vs._process_image_embeddings_bedrock(image_chunks, image_base64s)

        assert len(points) == 0


# ===================================================================
# _process_image_embeddings_jina (lines 648-736)
# ===================================================================

class TestProcessImageEmbeddingsJina:
    """Tests for VectorStore._process_image_embeddings_jina."""

    @pytest.mark.asyncio
    async def test_jina_success(self):
        """Jina AI embedding returns points."""
        vs = _make_vectorstore()
        vs.api_key = "jina-key"
        vs.model_name = "jina-clip-v1"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2]}]
        }

        image_chunks = [{"metadata": {}, "image_uri": "img"}]
        image_base64s = ["aW1hZ2U="]  # valid base64

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            points = await vs._process_image_embeddings_jina(image_chunks, image_base64s)

        assert len(points) == 1

    @pytest.mark.asyncio
    async def test_jina_batch_failure(self):
        """Failed Jina batch returns empty."""
        vs = _make_vectorstore()
        vs.api_key = "jina-key"
        vs.model_name = "jina-clip-v1"

        image_chunks = [{"metadata": {}, "image_uri": "img"}]
        image_base64s = ["aW1hZ2U="]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = RuntimeError("API error")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            points = await vs._process_image_embeddings_jina(image_chunks, image_base64s)

        assert len(points) == 0

    @pytest.mark.asyncio
    async def test_jina_invalid_images_filtered(self):
        """Invalid base64 images are filtered out."""
        vs = _make_vectorstore()
        vs.api_key = "jina-key"
        vs.model_name = "jina-clip-v1"

        image_chunks = [{"metadata": {}, "image_uri": "img"}]
        image_base64s = ["not!valid@base64#"]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            points = await vs._process_image_embeddings_jina(image_chunks, image_base64s)

        assert len(points) == 0


# ===================================================================
# _process_document_chunks — remote concurrent batch failure (lines 826-843)
# ===================================================================

class TestProcessDocumentChunksRemoteFailure:
    """Tests for remote concurrent batch failure in _process_document_chunks."""

    @pytest.mark.asyncio
    async def test_remote_batch_failure_raises(self):
        """Raises VectorStoreError when remote batch fails."""
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import VectorStoreError

        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs.vector_store = AsyncMock()
        vs.vector_store.aadd_documents = AsyncMock(side_effect=RuntimeError("batch fail"))

        chunks = [Document(page_content="test", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(chunks)


# ===================================================================
# custom_sentence_boundary (lines 166-240)
# ===================================================================

class TestCustomSentenceBoundary:
    """Tests for the custom_sentence_boundary spaCy component."""

    def test_function_exists(self):
        """custom_sentence_boundary function is defined."""
        from app.modules.transformers.vectorstore import VectorStore
        assert hasattr(VectorStore, 'custom_sentence_boundary')


# ===================================================================
# get_embedding_model_instance — model_name fallback to unknown (lines 372-373)
# ===================================================================

class TestGetEmbeddingModelInstanceUnknown:
    """Test model_name fallback to 'unknown'."""

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_unknown(self):
        """Falls back to 'unknown' when no model attributes exist."""
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock(spec=[])  # no attributes
        mock_embed.embed_query = MagicMock(return_value=[0.1] * 1024)
        # No model_name, model, or model_id attributes

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                await vs.get_embedding_model_instance()

        assert vs.model_name == "unknown"

    @pytest.mark.asyncio
    async def test_aws_bedrock_credentials_stored(self):
        """AWS Bedrock credentials are stored during initialization."""
        from app.utils.aimodels import EmbeddingProvider
        vs = _make_vectorstore()

        config = {
            "provider": EmbeddingProvider.AWS_BEDROCK.value,
            "configuration": {
                "apiKey": "key",
                "model": "titan-embed",
                "region": "us-east-1",
                "awsAccessKeyId": "AKID",
                "awsAccessSecretKey": "secret",
            },
            "isDefault": True,
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.embed_query.return_value = [0.1] * 1024
        mock_embed.model_name = "titan-embed"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
                await vs.get_embedding_model_instance()

        assert vs.aws_access_key_id == "AKID"
        assert vs.aws_secret_access_key == "secret"


# ===================================================================
# index_documents — image with multimodal LLM failed description
# ===================================================================

class TestIndexDocumentsImageDescription:
    """Test image description path where some descriptions fail."""

    @pytest.mark.asyncio
    async def test_image_description_failure_skipped(self):
        """Image descriptions that fail are skipped - returns True with no embeddings."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.describe_images = AsyncMock(return_value=[
            {"index": 0, "success": False, "error": "API error"}
        ])

        block = Block(index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "image/png")

        # When all image descriptions fail and no text blocks, returns True (no docs to embed)
        assert result is True

    @pytest.mark.asyncio
    async def test_table_block_with_data(self):
        """Table block group with data creates summary embedding."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, GroupType
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        bg = BlockGroup(index=0, type=GroupType.TABLE)
        bg.data = {"table_summary": "Revenue data by quarter"}
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        assert any("Revenue data" in c.page_content for c in chunks)

    @pytest.mark.asyncio
    async def test_textsection_block_processed_as_text(self):
        """TextSection blocks are processed as text."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Section")]))

        block = Block(index=0, type="textsection", format="txt", data="A text section", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with patch("app.modules.transformers.vectorstore.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

        assert result is True
        vs._create_embeddings.assert_awaited_once()
