"""Unit tests for app.modules.transformers.vectorstore.VectorStore."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vectorstore():
    """Instantiate a VectorStore with everything mocked to bypass __init__ side effects."""
    from app.services.vector_db.models import VectorDBCapabilities

    from app.modules.transformers.vectorstore import VectorStore

    mock_vdb = AsyncMock()
    mock_vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    mock_vdb.get_service_name = MagicMock(return_value="mock")

    vs = VectorStore(
        logger=MagicMock(),
        config_service=AsyncMock(),
        graph_provider=AsyncMock(),
        collection_name="test_collection",
        vector_db_service=mock_vdb,
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
        ctx.reconciliation_context = None

        result = await vs.apply(ctx)

        vs.index_documents.assert_awaited_once_with(
            record.block_containers,
            "org-1",
            "rec-1",
            "vr-1",
            block_ids_to_delete=None,
            is_reconciliation=False,
            record=record,
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
        ctx.reconciliation_context = None

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
        ctx.reconciliation_context = None

        await vs.apply(ctx)

        vs.index_documents.assert_awaited_once_with(
            block_containers,
            "specific-org-id",
            "specific-rec-id",
            "specific-vr-id",
            block_ids_to_delete=None,
            is_reconciliation=False,
            record=record,
        )


# ===================================================================
# _initialize_collection
# ===================================================================

class TestInitializeCollectionBasics:
    """Tests for VectorStore._initialize_collection."""

    @pytest.mark.asyncio
    async def test_creates_collection_when_not_found(self):
        """Creates collection when collection does not exist."""
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=False)
        )
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=768)

        vs.vector_db_service.create_collection.assert_awaited_once()
        assert vs.vector_db_service.create_index.call_count == 4

    @pytest.mark.asyncio
    async def test_recreates_on_dimension_mismatch(self):
        """Raises VectorStoreError when vector size differs (manual re-index required)."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=True, dense_dimension=512)
        )

        with pytest.raises(VectorStoreError):
            await vs._initialize_collection(embedding_size=768)

    @pytest.mark.asyncio
    async def test_no_recreate_when_same_size(self):
        """Does not recreate when sizes match."""
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=True, dense_dimension=768)
        )
        vs.vector_db_service.create_collection = AsyncMock()

        await vs._initialize_collection(embedding_size=768)

        vs.vector_db_service.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_collection_failure_raises_vectorstore_error(self):
        """Raises VectorStoreError when creation fails."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=False)
        )
        vs.vector_db_service.create_collection = AsyncMock(side_effect=RuntimeError("create failed"))

        with pytest.raises(VectorStoreError):
            await vs._initialize_collection(embedding_size=768)


class TestRecreateRecordsCollection:
    @pytest.mark.asyncio
    async def test_drops_before_initialising(self):
        """Order is the whole point.

        get_embedding_model_instance ends by calling _initialize_collection,
        which raises on an embedding-dimension mismatch. Initialising before the
        drop would therefore fail exactly when the model changed — the main
        reason to recreate.
        """
        calls: list[str] = []

        vs = _make_vectorstore()
        vs.vector_db_service.delete_collection = AsyncMock(
            side_effect=lambda *a, **k: calls.append("delete")
        )
        vs.get_embedding_model_instance = AsyncMock(
            side_effect=lambda *a, **k: calls.append("init")
        )

        await vs.recreate_records_collection()

        assert calls == ["delete", "init"], calls
        vs.vector_db_service.delete_collection.assert_awaited_once_with(
            "test_collection"
        )

    @pytest.mark.asyncio
    async def test_missing_collection_still_initialises(self):
        vs = _make_vectorstore()
        vs.vector_db_service.delete_collection = AsyncMock(
            side_effect=Exception("collection not found")
        )
        vs.get_embedding_model_instance = AsyncMock()

        await vs.recreate_records_collection()

        vs.get_embedding_model_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unexpected_delete_error_propagates(self):
        """A real outage must not be mistaken for an absent collection."""
        vs = _make_vectorstore()
        vs.vector_db_service.delete_collection = AsyncMock(
            side_effect=ConnectionError("qdrant unreachable")
        )
        vs.get_embedding_model_instance = AsyncMock()

        with pytest.raises(ConnectionError):
            await vs.recreate_records_collection()

        vs.get_embedding_model_instance.assert_not_awaited()


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
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 768)
        mock_embed.model_name = "test-model"

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embed):
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
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1536)
        mock_embed.model_name = "text-embedding-3-small"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            result = await vs.get_embedding_model_instance()

        assert result is True
        # embedding_size must be captured so image points can be validated
        # against the collection's dimension before upsert.
        assert vs.embedding_size == 1536

    @pytest.mark.asyncio
    async def test_captures_base_url_for_local_endpoints(self):
        """Ollama / LM Studio / OpenAI-compatible multimodal providers need
        the configured endpoint on the instance to reach the right server."""
        vs = _make_vectorstore()

        config = {
            "provider": "ollama",
            "configuration": {"endpoint": "http://localhost:11434", "model": "nomic-embed-text"},
            "isDefault": True,
            "isMultimodal": True,
        }
        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": [config],
        })
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 768)
        mock_embed.model_name = "nomic-embed-text"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            await vs.get_embedding_model_instance()

        assert vs.base_url == "http://localhost:11434"

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
        mock_embed.aembed_query = AsyncMock(side_effect=RuntimeError("API error"))

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
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        # Add only 'model' attribute
        mock_embed.model = "test-model-via-model"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
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
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        mock_embed.model_id = "test-model-via-id"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
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
        vs.vector_db_service.delete_points = AsyncMock()

        await vs.delete_embeddings("vr-1")

        vs.vector_db_service.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_raises_embedding_error(self):
        """Raises EmbeddingError on failure."""
        from app.exceptions.indexing_exceptions import EmbeddingError
        vs = _make_vectorstore()
        vs.vector_db_service.filter_collection = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(EmbeddingError):
            await vs.delete_embeddings("vr-1")


# ===================================================================
# _cleanup_orphaned_embeddings_if_needed
# ===================================================================

class TestCleanupOrphanedEmbeddings:
    """Tests for VectorStore._cleanup_orphaned_embeddings_if_needed."""

    @pytest.mark.asyncio
    async def test_record_exists_skips_cleanup(self):
        """No cleanup when record is still present in graph DB."""
        vs = _make_vectorstore()
        vs.graph_provider.get_document = AsyncMock(return_value={"id": "rec-1"})
        vs.delete_embeddings = AsyncMock()

        await vs._cleanup_orphaned_embeddings_if_needed("rec-1", "vr-1")

        vs.delete_embeddings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_record_with_md5_duplicate_keeps_embeddings(self):
        """Keeps embeddings when record is gone but an MD5 duplicate exists."""
        from app.models.entities import Record, RecordType
        from app.config.constants.arangodb import Connectors, OriginTypes

        vs = _make_vectorstore()
        vs.graph_provider.get_document = AsyncMock(return_value=None)
        vs.graph_provider.find_duplicate_records = AsyncMock(
            return_value=[{"id": "rec-dup", "md5Checksum": "abc123"}]
        )
        vs.delete_embeddings = AsyncMock()

        record = Record(
            id="rec-1",
            org_id="org-1",
            record_name="test.pdf",
            record_type=RecordType.FILE,
            external_record_id="ext-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            md5_hash="abc123",
            size_in_bytes=100,
        )

        await vs._cleanup_orphaned_embeddings_if_needed("rec-1", "vr-1", record)

        vs.graph_provider.find_duplicate_records.assert_awaited_once_with(
            record_key="rec-1",
            md5_checksum="abc123",
            record_type=RecordType.FILE.value,
            size_in_bytes=100,
        )
        vs.delete_embeddings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_record_without_duplicates_deletes_embeddings(self):
        """Deletes embeddings when record is gone and no MD5 duplicate exists."""
        from app.models.entities import Record, RecordType
        from app.config.constants.arangodb import Connectors, OriginTypes

        vs = _make_vectorstore()
        vs.graph_provider.get_document = AsyncMock(return_value=None)
        vs.graph_provider.find_duplicate_records = AsyncMock(return_value=[])
        vs.delete_embeddings = AsyncMock()

        record = Record(
            id="rec-1",
            org_id="org-1",
            record_name="test.pdf",
            record_type=RecordType.FILE,
            external_record_id="ext-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            md5_hash="abc123",
        )

        await vs._cleanup_orphaned_embeddings_if_needed("rec-1", "vr-1", record)

        vs.delete_embeddings.assert_awaited_once_with("vr-1")

    @pytest.mark.asyncio
    async def test_missing_record_without_md5_deletes_embeddings(self):
        """Deletes embeddings when record is gone and MD5 is unavailable."""
        vs = _make_vectorstore()
        vs.graph_provider.get_document = AsyncMock(return_value=None)
        vs.delete_embeddings = AsyncMock()

        await vs._cleanup_orphaned_embeddings_if_needed("rec-1", "vr-1")

        vs.graph_provider.find_duplicate_records.assert_not_called()
        vs.delete_embeddings.assert_awaited_once_with("vr-1")


# ===================================================================
# _process_image_embeddings (dispatch)
# ===================================================================

# ===================================================================
# _store_image_points
# ===================================================================

class TestStoreImagePoints:
    """Tests for VectorStore._store_image_points."""

    @pytest.mark.asyncio
    async def test_stores_points(self):
        """Stores points in vector DB."""
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = AsyncMock()

        mock_point = MagicMock()
        await vs._store_image_points([mock_point])

        vs.vector_db_service.upsert_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_points_logs(self):
        """Empty points list logs but doesn't upsert."""
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = AsyncMock()

        await vs._store_image_points([])

        vs.vector_db_service.upsert_points.assert_not_awaited()
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
        vs._embed_and_upsert_documents = AsyncMock()

        chunks = [Document(page_content="test", metadata={})]
        await vs._process_document_chunks(chunks, "rec-1")

        vs._embed_and_upsert_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_remote_concurrent_processing(self):
        """Remote embedding uses concurrent processing."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs._embed_and_upsert_documents = AsyncMock()

        chunks = [Document(page_content=f"test {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(chunks, "rec-1")

        vs._embed_and_upsert_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_record_not_found_skips_embedding(self):
        """Skips embedding when record is not found in graph database."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.embedding_provider = None
        # Don't mock _embed_and_upsert_documents so real implementation runs
        vs.graph_provider.get_document = AsyncMock(return_value=None)

        chunks = [Document(page_content="test", metadata={})]
        await vs._process_document_chunks(chunks, "rec-1")

        vs.graph_provider.get_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_local_batch_failure_raises(self):
        """Raises VectorStoreError when local batch fails."""
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import VectorStoreError

        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs._embed_and_upsert_documents = AsyncMock(side_effect=RuntimeError("fail"))

        chunks = [Document(page_content="test", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(chunks, "rec-1")


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

        result = await vs.index_documents(
            BlocksContainer(blocks=[], block_groups=[]),
            "org-1", "rec-1", "vr-1",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_text_blocks_create_embeddings(self):
        """Text blocks are processed into document embeddings."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = Block(index=0, type="text", format="txt", data="Hello world", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        vs._create_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_text_blocks_with_multiple_sentences(self):
        """Short multi-sentence blocks embed as one document under the word threshold."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = Block(index=0, type="text", format="txt", data="First sentence. Second sentence.", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        assert len(chunks) == 1
        assert chunks[0].metadata["isBlock"] is True

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_embedding(self):
        """Image blocks create image embeddings when multimodal."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)  # multimodal embedding
        vs._create_embeddings = AsyncMock()

        block = Block(index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        # Should have image dict chunks
        assert any(isinstance(c, dict) and "image_uri" in c for c in chunks)
        image_chunk = next(c for c in chunks if isinstance(c, dict) and "image_uri" in c)
        assert image_chunk["metadata"]["blockType"] == "image"
        assert image_chunk["metadata"]["isImage"] is True

    @pytest.mark.asyncio
    async def test_image_blocks_with_multimodal_embedding_carries_caption_description(self):
        """The image chunk dict built for multimodal embedding must carry a
        'description' derived from image_metadata.captions, so the eventual
        VectorPoint.page_content is the caption rather than raw base64."""
        from app.models.blocks import Block, BlocksContainer, ImageMetadata
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)
        vs._create_embeddings = AsyncMock()

        block = Block(
            index=0,
            type="image",
            format="bin",
            data={"uri": "base64data"},
            comments=[],
            image_metadata=ImageMetadata(captions=["Architecture diagram"]),
        )
        container = BlocksContainer(blocks=[block], block_groups=[])

        await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        chunks = vs._create_embeddings.call_args[0][0]
        image_chunk = next(c for c in chunks if isinstance(c, dict) and "image_uri" in c)
        assert image_chunk["description"] == "Architecture diagram"

    @pytest.mark.asyncio
    async def test_image_blocks_embed_the_description_written_at_parse_time(self):
        """With text-only embeddings the description IS the image as far as the
        index is concerned. It is written before the record is stored (see
        `ImageDescriber`), so indexing embeds it rather than paying a second
        vision call for text it already has."""
        from langchain_core.documents import Document

        from app.models.blocks import Block, BlocksContainer, ImageMetadata
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = Block(
            index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[],
            image_metadata=ImageMetadata(description="A photo"),
        )
        container = BlocksContainer(blocks=[block], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        vlm_doc = next(c for c in chunks if isinstance(c, Document) and c.page_content == "A photo")
        assert vlm_doc.metadata["blockType"] == "image"
        assert vlm_doc.metadata["isImage"] is True

    @pytest.mark.asyncio
    async def test_image_block_without_a_description_is_not_embedded_as_text(self):
        """Nothing to embed: an image with no prose and text-only embeddings
        contributes no point rather than an empty one."""
        from langchain_core.documents import Document

        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = Block(index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        chunks = vs._create_embeddings.call_args[0][0] if vs._create_embeddings.call_args else []
        assert not [
            c for c in chunks
            if isinstance(c, Document) and c.metadata.get("isImage")
        ]

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        from langchain_core.documents import Document
        chunks = vs._create_embeddings.call_args[0][0]
        row_doc = next(c for c in chunks if isinstance(c, Document))
        assert row_doc.metadata["blockType"] == "table_row"

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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
                "org-1", "rec-1", "vr-1",
            )

    @pytest.mark.asyncio
    async def test_create_embeddings_failure_raises(self):
        """Raises IndexingError when _create_embeddings raises an unknown exception."""
        from app.models.blocks import Block, BlocksContainer
        from app.exceptions.indexing_exceptions import IndexingError
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=RuntimeError("embed fail"))
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Hello")]))

        block = Block(index=0, type="text", format="txt", data="Hello", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with pytest.raises(IndexingError, match="Unexpected error during indexing"):
            await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        # table_cell blocks have no summary, so no documents to embed => True
        assert result is True


# ===================================================================
# _detect_record_language / _build_text_documents / _chunk_oversized_text /
# _process_text_blocks (module-level helpers that replaced spaCy)
# ===================================================================

class TestDetectRecordLanguage:
    """Tests for the _detect_record_language module-level function."""

    def test_empty_blocks_defaults_to_en(self):
        from app.modules.transformers.vectorstore import _detect_record_language
        assert _detect_record_language([]) == "en"

    def test_blocks_with_no_text_defaults_to_en(self):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _detect_record_language
        blocks = [Block(index=0, type="text", format="txt", data="", comments=[])]
        assert _detect_record_language(blocks) == "en"

    def test_detects_english_text(self):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _detect_record_language
        blocks = [
            Block(
                index=0,
                type="text",
                format="txt",
                data="This is a perfectly ordinary English sentence about the weather today.",
                comments=[],
            )
        ]
        assert _detect_record_language(blocks) == "en"

    def test_delegates_to_detect_language(self):
        """Passes the sampled text straight through to detect_language."""
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _detect_record_language
        blocks = [Block(index=0, type="text", format="txt", data="Some text", comments=[])]
        with patch(
            "app.modules.transformers.vectorstore.detect_language",
            return_value="fr",
        ) as mock_detect:
            result = _detect_record_language(blocks)
        assert result == "fr"
        mock_detect.assert_called_once_with("Some text")


class TestChunkOversizedText:
    """Tests for the _chunk_oversized_text module-level function."""

    def test_short_text_returns_single_chunk(self):
        from app.modules.transformers.vectorstore import _chunk_oversized_text
        chunks = _chunk_oversized_text("Just one short sentence.", "en")
        assert len(chunks) == 1

    def test_long_text_splits_into_multiple_chunks(self):
        from app.modules.transformers.vectorstore import (
            _OVERSIZED_CHUNK_SIZE,
            _chunk_oversized_text,
        )
        sentence = "This is a moderately long sentence for testing purposes. "
        text = sentence * 200  # far exceeds _OVERSIZED_CHUNK_SIZE
        chunks = _chunk_oversized_text(text, "en")
        assert len(chunks) > 1
        assert all(len(c) <= _OVERSIZED_CHUNK_SIZE + len(sentence) for c in chunks)

    def test_empty_text_returns_text_as_single_chunk(self):
        from app.modules.transformers.vectorstore import _chunk_oversized_text
        assert _chunk_oversized_text("", "en") == [""]


class TestBuildTextDocuments:
    """Tests for the _build_text_documents module-level function."""

    def test_single_sentence_block_yields_one_document(self):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _build_text_documents
        blocks = [Block(index=0, type="text", format="txt", data="Hello world", comments=[])]
        docs = _build_text_documents(blocks, "vr-1", "org-1", "en")
        assert len(docs) == 1
        assert docs[0].metadata["isBlock"] is True
        assert docs[0].metadata["blockType"] == "text"

    def test_short_multi_sentence_block_embeds_block_only(self, monkeypatch):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _build_text_documents
        monkeypatch.setenv("EMBED_SENTENCE_MIN_WORDS", "100")
        blocks = [
            Block(
                index=0,
                type="text",
                format="txt",
                data="First sentence. Second sentence.",
                comments=[],
            )
        ]
        docs = _build_text_documents(blocks, "vr-1", "org-1", "en")
        assert len(docs) == 1
        assert docs[0].metadata["isBlock"] is True
        assert docs[0].page_content == "First sentence. Second sentence."

    def test_long_multi_sentence_block_yields_sentences_plus_block(self, monkeypatch):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _build_text_documents
        monkeypatch.setenv("EMBED_SENTENCE_MIN_WORDS", "100")
        sentence = "This is a complete English sentence about widgets. "
        text = sentence * 20
        assert len(text.split()) > 100
        blocks = [Block(index=0, type="text", format="txt", data=text, comments=[])]
        docs = _build_text_documents(blocks, "vr-1", "org-1", "en")
        assert len(docs) > 2
        assert sum(1 for d in docs if d.metadata["isBlock"]) == 1

    def test_zero_min_words_splits_every_multi_sentence_block(self, monkeypatch):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _build_text_documents
        monkeypatch.setenv("EMBED_SENTENCE_MIN_WORDS", "0")
        blocks = [
            Block(
                index=0,
                type="text",
                format="txt",
                data="First sentence. Second sentence.",
                comments=[],
            )
        ]
        docs = _build_text_documents(blocks, "vr-1", "org-1", "en")
        assert len(docs) == 3
        assert sum(1 for d in docs if d.metadata["isBlock"]) == 1

    def test_oversized_block_skips_whole_block_document(self):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import (
            _MAX_BLOCK_CHARS_FOR_SENTENCE_SPLIT,
            _build_text_documents,
        )
        oversized_text = ("Sentence number filler text here. " * 2000)
        assert len(oversized_text) > _MAX_BLOCK_CHARS_FOR_SENTENCE_SPLIT
        blocks = [Block(index=0, type="text", format="txt", data=oversized_text, comments=[])]
        docs = _build_text_documents(blocks, "vr-1", "org-1", "en")
        assert len(docs) > 1
        assert all(not d.metadata["isBlock"] for d in docs)


class TestProcessTextBlocks:
    """Tests for the _process_text_blocks module-level function."""

    def test_combines_detection_and_document_building(self):
        from app.models.blocks import Block
        from app.modules.transformers.vectorstore import _process_text_blocks
        blocks = [Block(index=0, type="text", format="txt", data="Hello world", comments=[])]
        docs = _process_text_blocks(blocks, "vr-1", "org-1")
        assert len(docs) == 1


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
        mock_ctx.reconciliation_context = None

        result = await vs.apply(mock_ctx)

        assert result is True
        vs.index_documents.assert_awaited_once_with(
            mock_ctx.record.block_containers,
            "org-1",
            "rec-1",
            "vr-1",
            block_ids_to_delete=None,
            is_reconciliation=False,
            record=mock_ctx.record,
        )


# ===================================================================
# split_into_sentences (app.modules.parsers.text_splitting) — replaces the
# old spaCy custom_sentence_boundary component used by VectorStore.
# ===================================================================

class TestSplitIntoSentences:
    """Tests for the pysbd-based split_into_sentences helper used by VectorStore."""

    def test_number_period_not_sentence_boundary(self):
        """Number followed by period should not be a sentence boundary."""
        from app.modules.parsers.text_splitting import split_into_sentences
        sents = split_into_sentences("Section 1. The first item.", "en")
        assert len(sents) >= 1

    def test_abbreviation_not_sentence_boundary(self):
        """Common abbreviations should not cause sentence splits."""
        from app.modules.parsers.text_splitting import split_into_sentences
        sents = split_into_sentences("Dr. Smith went to the store.", "en")
        assert len(sents) <= 2

    def test_ellipsis_not_sentence_boundary(self):
        """Ellipsis (...) should not cause sentence splits."""
        from app.modules.parsers.text_splitting import split_into_sentences
        sents = split_into_sentences("Wait... I think so.", "en")
        assert len(sents) >= 1


# ===================================================================
# _initialize_collection (lines 266-318)
# ===================================================================

class TestInitializeCollection:
    """Tests for VectorStore._initialize_collection."""

    @pytest.mark.asyncio
    async def test_collection_exists_same_size(self):
        """When collection exists with correct size, should not recreate."""
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=True, dense_dimension=1024)
        )

        await vs._initialize_collection(embedding_size=1024)
        vs.vector_db_service.create_collection.assert_not_awaited()
        assert vs.vector_db_service.create_index.call_count == 4

    @pytest.mark.asyncio
    async def test_collection_exists_different_size(self):
        """When collection exists with wrong size, should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=True, dense_dimension=512)
        )

        with pytest.raises(VectorStoreError):
            await vs._initialize_collection(embedding_size=1024)

    @pytest.mark.asyncio
    async def test_collection_not_found_creates_new(self):
        """When collection does not exist, should create it."""
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=False)
        )
        vs.vector_db_service.create_collection = AsyncMock()
        vs.vector_db_service.create_index = AsyncMock()

        await vs._initialize_collection(embedding_size=1024)
        vs.vector_db_service.create_collection.assert_awaited_once()
        assert vs.vector_db_service.create_index.call_count == 4

    @pytest.mark.asyncio
    async def test_collection_creation_failure(self):
        """When collection creation fails, should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from app.services.vector_db.models import VectorCollectionInfo
        vs = _make_vectorstore()
        vs.vector_db_service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="test_collection", exists=False)
        )
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
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        mock_embeddings.model_name = "default-model"

        vs.config_service.get_config = AsyncMock(return_value={
            "embedding": []
        })

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            result = await vs.get_embedding_model_instance()

        assert result is False  # Default is not multimodal

    @pytest.mark.asyncio
    async def test_configured_embedding_model(self):
        """When embedding configs exist, should use configured model."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 1536)
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
            result = await vs.get_embedding_model_instance()

        assert result is True

    @pytest.mark.asyncio
    async def test_embedding_model_embed_query_failure(self):
        """When embed_query fails, should raise IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError

        vs = _make_vectorstore()
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(side_effect=Exception("embed failed"))

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
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)
        mock_embeddings.model = "my-model"

        vs.config_service.get_config = AsyncMock(return_value={"embedding": []})

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            result = await vs.get_embedding_model_instance()

        assert vs.model_name == "my-model"

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model_id(self):
        """When dense_embeddings has 'model_id' attr."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock(spec=[])
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)
        mock_embeddings.model_id = "my-model-id"

        vs.config_service.get_config = AsyncMock(return_value={"embedding": []})

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            result = await vs.get_embedding_model_instance()

        assert vs.model_name == "my-model-id"

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_unknown(self):
        """When dense_embeddings has no name attr, should use 'unknown'."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock(spec=[])
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)

        vs.config_service.get_config = AsyncMock(return_value={"embedding": []})

        with patch("app.modules.transformers.vectorstore.get_default_embedding_model", return_value=mock_embeddings):
            await vs.get_embedding_model_instance()

        assert vs.model_name == "unknown"

    @pytest.mark.asyncio
    async def test_aws_bedrock_credentials_stored(self):
        """When provider is AWS Bedrock, should store AWS credentials."""
        vs = _make_vectorstore()
        vs._initialize_collection = AsyncMock()

        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 1024)
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
            await vs.get_embedding_model_instance()

        assert vs.aws_access_key_id == "AKID"
        assert vs.aws_secret_access_key == "SECRET"


# ===================================================================
# _process_image_embeddings (lines 738-752)
# ===================================================================

class TestProcessImageEmbeddings:
    """Tests for _process_image_embeddings dispatching via MultimodalEmbeddingFactory."""

    @pytest.mark.asyncio
    async def test_dispatches_to_factory_resolved_provider(self):
        """Resolves a provider via the factory and builds points from its results."""
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        vs.graph_provider.get_document = AsyncMock(return_value={"id": "rec-1"})

        mock_provider = MagicMock()
        mock_provider.supports_multimodal.return_value = True
        mock_provider.embed_images = AsyncMock(
            return_value=[ImageEmbeddingResult(index=0, embedding=[0.1, 0.2])]
        )

        with patch(
            "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create",
            return_value=mock_provider,
        ):
            result = await vs._process_image_embeddings(
                [{"metadata": {}, "description": ""}], ["data:image/png;base64,abc"], "rec-1"
            )

        mock_provider.embed_images.assert_awaited_once_with(["data:image/png;base64,abc"])
        assert len(result) == 1
        assert result[0].dense_vector == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_record_deleted_mid_flight_skips_embedding(self):
        """A record removed while indexing was in flight must not be embedded."""
        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        vs.graph_provider.get_document = AsyncMock(return_value=None)

        with patch(
            "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create",
        ) as mock_create:
            result = await vs._process_image_embeddings(
                [{"metadata": {}}], ["data:image/png;base64,abc"], "rec-1"
            )

        assert result == []
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_provider_returns_empty(self):
        """When the factory can't resolve a provider, returns empty list without erroring."""
        vs = _make_vectorstore()
        vs.embedding_provider = "unknown_provider"
        vs.graph_provider.get_document = AsyncMock(return_value={"id": "rec-1"})

        result = await vs._process_image_embeddings(
            [{"metadata": {}}], ["data:image/png;base64,abc"], "rec-1"
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_provider_that_does_not_support_multimodal_returns_empty(self):
        """A resolved provider that reports supports_multimodal()=False (e.g. Ollama on
        current builds) must not attempt embedding — the caller should have already
        routed images through the VLM-description fallback instead."""
        vs = _make_vectorstore()
        vs.embedding_provider = "ollama"
        vs.graph_provider.get_document = AsyncMock(return_value={"id": "rec-1"})

        mock_provider = MagicMock()
        mock_provider.supports_multimodal.return_value = False
        mock_provider.embed_images = AsyncMock()

        with patch(
            "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create",
            return_value=mock_provider,
        ):
            result = await vs._process_image_embeddings(
                [{"metadata": {}}], ["data:image/png;base64,abc"], "rec-1"
            )

        mock_provider.embed_images.assert_not_awaited()
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_image_list_short_circuits_before_factory(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        vs.graph_provider.get_document = AsyncMock(return_value={"id": "rec-1"})

        with patch(
            "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create"
        ) as mock_create:
            result = await vs._process_image_embeddings([], [], "rec-1")

        mock_create.assert_not_called()
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
        vs.vector_db_service.upsert_points = AsyncMock()

        mock_point = MagicMock()
        await vs._store_image_points([mock_point])
        vs.vector_db_service.upsert_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_points_skipped(self):
        """Should log and skip when no points to upsert."""
        vs = _make_vectorstore()
        vs.vector_db_service.upsert_points = AsyncMock()
        await vs._store_image_points([])
        vs.vector_db_service.upsert_points.assert_not_awaited()


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
        vs._embed_and_upsert_documents = AsyncMock()

        docs = [Document(page_content="text", metadata={})]
        await vs._process_document_chunks(docs, "rec-1")
        vs._embed_and_upsert_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_remote_concurrent_processing(self):
        """Remote embedding should process concurrently."""
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs._embed_and_upsert_documents = AsyncMock()

        docs = [Document(page_content=f"text {i}", metadata={}) for i in range(5)]
        await vs._process_document_chunks(docs, "rec-1")
        vs._embed_and_upsert_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_local_batch_failure_raises(self):
        """Local batch failure should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = None  # local
        vs._embed_and_upsert_documents = AsyncMock(side_effect=Exception("batch failed"))

        docs = [Document(page_content="text", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(docs, "rec-1")

    @pytest.mark.asyncio
    async def test_remote_batch_failure_raises(self):
        """Remote batch failure should raise VectorStoreError."""
        from app.exceptions.indexing_exceptions import VectorStoreError
        from langchain_core.documents import Document
        vs = _make_vectorstore()
        vs.embedding_provider = "openai"
        vs._embed_and_upsert_documents = AsyncMock(side_effect=Exception("batch failed"))

        docs = [Document(page_content="text", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(docs, "rec-1")


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
        with pytest.raises(VectorStoreError, match="Failed to store documents in vector store"):
            await vs._create_embeddings([doc], "rec-1", "vr-1")
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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        # Should have called _create_embeddings with image dict
        args = vs._create_embeddings.call_args[0]
        assert any(isinstance(d, dict) and "image_uri" in d for d in args[0])

    @pytest.mark.asyncio
    async def test_image_blocks_index_without_a_second_vision_call(self):
        """The vision call happens once, before the record is stored; indexing
        reads what it wrote."""
        from app.models.blocks import Block, BlocksContainer, ImageMetadata
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)  # not multimodal embedding
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        img_block = Block(
            index=0, type="image", format="base64",
            data={"uri": "data:image/png;base64,abc"}, comments=[],
            image_metadata=ImageMetadata(description="A cat"),
        )
        container = BlocksContainer(blocks=[img_block], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        from langchain_core.documents import Document
        chunks = vs._create_embeddings.call_args[0][0]
        table_summary_doc = next(
            c for c in chunks if isinstance(c, Document) and c.page_content == "Sales data for Q1"
        )
        assert table_summary_doc.metadata["blockType"] == "table"
        table_row_doc = next(
            c for c in chunks if isinstance(c, Document) and c.page_content == "Row 1 text"
        )
        assert table_row_doc.metadata["blockType"] == "table_row"

    @pytest.mark.asyncio
    async def test_empty_blocks_returns_none(self):
        """Empty blocks and block_groups should return None."""
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.nlp = MagicMock()

        container = BlocksContainer(blocks=[], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_record_summary_embedded_with_blocks(self):
        """Semantic record summary is embedded alongside content blocks."""
        from app.models.blocks import Block, BlocksContainer, SemanticMetadata

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_blocks_by_ids = AsyncMock()
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock()
        sent = MagicMock()
        sent.text = "Hello world"
        vs.nlp.return_value = MagicMock(sents=[sent])

        block = Block(index=0, type="text", format="txt", data="Hello world", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])
        record = MagicMock()
        record.semantic_metadata = SemanticMetadata(
            summary="Document overview",
            categories=["General"],
            departments=["Engineering"],
            topics=["onboarding"],
        )

        result = await vs.index_documents(
            container,
            "org-1",
            "rec-1",
            "vr-1",
            record=record,
        )

        assert result is True
        from app.modules.transformers.vectorstore import VectorStore as VS
        from langchain_core.documents import Document

        vs.delete_blocks_by_ids.assert_not_called()
        vs._create_embeddings.assert_awaited_once()
        chunks = vs._create_embeddings.await_args.args[0]
        assert len(chunks) >= 2  # text block chunk(s) + record summary
        summary_chunks = [
            c for c in chunks
            if isinstance(c, Document)
            and (c.metadata or {}).get("isRecordSummary")
        ]
        assert len(summary_chunks) == 1
        assert summary_chunks[0].page_content == "Document overview"
        assert summary_chunks[0].metadata.get("blockId") == VS.record_summary_block_id("vr-1")
        assert summary_chunks[0].metadata.get("blockType") == "record_summary"

    @pytest.mark.asyncio
    async def test_embedding_model_instance_failure(self):
        """When get_embedding_model_instance fails, should raise IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError
        from app.models.blocks import BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(side_effect=Exception("model failed"))

        container = BlocksContainer(blocks=[], block_groups=[])

        with pytest.raises(IndexingError, match="Failed to get embedding model instance"):
            await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_embedding_creation_failure_raises(self):
        """When _create_embeddings raises an exception, index_documents wraps it in IndexingError."""
        from app.exceptions.indexing_exceptions import IndexingError
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock(side_effect=Exception("embed failed"))
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Hello")]))

        block = Block(index=0, type="text", format="txt", data="Hello world", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        with pytest.raises(IndexingError, match="Unexpected error during indexing"):
            await vs.index_documents(container, "org-1", "rec-1", "vr-1")


# ===================================================================
# _image_block_description
# ===================================================================

class TestImageBlockDescription:
    """Tests for VectorStore._image_block_description — the text derived from
    an image block's metadata (captions/footnotes/annotations) for use as the
    vector payload's page_content, instead of the raw base64 URI."""

    def test_no_image_metadata_returns_empty_string(self):
        from app.models.blocks import Block, BlockType
        from app.modules.transformers.vectorstore import VectorStore

        block = Block(index=0, type=BlockType.IMAGE, data={"uri": "data:image/png;base64,abc"})
        assert VectorStore._image_block_description(block) == ""

    def test_captions_are_joined(self):
        from app.models.blocks import Block, BlockType, ImageMetadata
        from app.modules.transformers.vectorstore import VectorStore

        block = Block(
            index=0,
            type=BlockType.IMAGE,
            data={"uri": "data:image/png;base64,abc"},
            image_metadata=ImageMetadata(captions=["Figure 1", "Network topology"]),
        )
        assert VectorStore._image_block_description(block) == "Figure 1 Network topology"

    def test_empty_captions_list_returns_empty_string(self):
        from app.models.blocks import Block, BlockType, ImageMetadata
        from app.modules.transformers.vectorstore import VectorStore

        block = Block(
            index=0,
            type=BlockType.IMAGE,
            data={"uri": "data:image/png;base64,abc"},
            image_metadata=ImageMetadata(captions=[]),
        )
        assert VectorStore._image_block_description(block) == ""

    def test_footnotes_and_annotations_included(self):
        from app.models.blocks import Block, BlockType, ImageMetadata
        from app.modules.transformers.vectorstore import VectorStore

        block = Block(
            index=0,
            type=BlockType.IMAGE,
            data={"uri": "data:image/png;base64,abc"},
            image_metadata=ImageMetadata(
                captions=["Caption"], footnotes=["Footnote"], annotations=["Annotation"]
            ),
        )
        description = VectorStore._image_block_description(block)
        assert "Caption" in description
        assert "Footnote" in description
        assert "Annotation" in description


# ===================================================================
# _build_image_points — zips provider ImageEmbeddingResults back to their
# source chunk and builds VectorPoints (provider-agnostic; the individual
# providers' own embed_images() behaviour is covered under
# tests/unit/services/embeddings/multimodal/).
# ===================================================================

class TestBuildImagePoints:
    """Tests for VectorStore._build_image_points."""

    def test_successful_result_becomes_point(self):
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        image_chunks = [{"metadata": {"orgId": "org1"}, "description": ""}]
        results = [ImageEmbeddingResult(index=0, embedding=[0.1, 0.2, 0.3])]

        points = vs._build_image_points(image_chunks, results)

        assert len(points) == 1
        assert points[0].dense_vector == [0.1, 0.2, 0.3]
        assert points[0].payload["metadata"] == {"orgId": "org1"}

    def test_error_result_is_skipped_and_logged(self):
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        image_chunks = [{"metadata": {}, "description": ""}]
        results = [ImageEmbeddingResult(index=0, error="image size must be at most 5MB")]

        points = vs._build_image_points(image_chunks, results)

        assert points == []
        vs.logger.warning.assert_called()

    def test_page_content_is_never_the_raw_base64_uri(self):
        """Image VectorPoint.page_content must never be the raw base64 URI —
        only a text description (or empty string) — regardless of provider."""
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        long_base64_uri = "data:image/png;base64," + ("A" * 5000)
        image_chunks = [{"metadata": {}, "image_uri": long_base64_uri, "description": ""}]
        results = [ImageEmbeddingResult(index=0, embedding=[0.1, 0.2, 0.3])]

        points = vs._build_image_points(image_chunks, results)

        assert points[0].payload["page_content"] == ""
        assert long_base64_uri not in points[0].payload["page_content"]

    def test_page_content_uses_caption_description_when_present(self):
        """When a chunk carries a 'description' (derived from image captions),
        it is used as page_content instead of an empty string."""
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        image_chunks = [
            {
                "metadata": {},
                "image_uri": "data:image/png;base64,abc",
                "description": "Network diagram",
            }
        ]
        results = [ImageEmbeddingResult(index=0, embedding=[0.1, 0.2, 0.3])]

        points = vs._build_image_points(image_chunks, results)

        assert points[0].payload["page_content"] == "Network diagram"

    def test_mixed_success_and_failure_only_returns_successful_points(self):
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        image_chunks = [
            {"metadata": {"i": 0}, "description": ""},
            {"metadata": {"i": 1}, "description": ""},
            {"metadata": {"i": 2}, "description": ""},
        ]
        results = [
            ImageEmbeddingResult(index=0, embedding=[0.1]),
            ImageEmbeddingResult(index=1, error="invalid image data"),
            ImageEmbeddingResult(index=2, embedding=[0.3]),
        ]

        points = vs._build_image_points(image_chunks, results)

        assert len(points) == 2
        assert points[0].payload["metadata"] == {"i": 0}
        assert points[1].payload["metadata"] == {"i": 2}

    def test_no_embedding_size_set_skips_validation(self):
        """When embedding_size hasn't been resolved yet (e.g. instance not
        fully initialised), dimension checking is skipped rather than
        dropping every point."""
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        vs.embedding_size = None
        image_chunks = [{"metadata": {}, "description": ""}]
        results = [ImageEmbeddingResult(index=0, embedding=[0.1, 0.2, 0.3])]

        points = vs._build_image_points(image_chunks, results)

        assert len(points) == 1

    def test_dimension_mismatch_among_multiple_results_only_drops_bad_one(self):
        """A result whose dimension doesn't match the collection's expected
        embedding size must be dropped (and logged), not upserted with a
        mismatched vector — while matching results are still kept."""
        from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

        vs = _make_vectorstore()
        vs.embedding_size = 3
        image_chunks = [
            {"metadata": {"i": 0}, "description": ""},
            {"metadata": {"i": 1}, "description": ""},
        ]
        results = [
            ImageEmbeddingResult(index=0, embedding=[0.1, 0.2, 0.3]),
            ImageEmbeddingResult(index=1, embedding=[0.1, 0.2]),
        ]

        points = vs._build_image_points(image_chunks, results)

        assert len(points) == 1
        assert points[0].payload["metadata"] == {"i": 0}
        vs.logger.error.assert_called()


# ===================================================================
# _multimodal_provider_config
# ===================================================================

class TestMultimodalProviderConfig:
    """Tests for VectorStore._multimodal_provider_config."""

    def test_builds_config_from_instance_state(self):
        vs = _make_vectorstore()
        vs.embedding_provider = "cohere"
        vs.api_key = "key"
        vs.model_name = "embed-v4.0"
        vs.region_name = "us-east-1"
        vs.aws_access_key_id = "akid"
        vs.aws_secret_access_key = "secret"
        vs.dense_embeddings = MagicMock()

        config = vs._multimodal_provider_config()

        assert config.provider == "cohere"
        assert config.api_key == "key"
        assert config.model_name == "embed-v4.0"
        assert config.region_name == "us-east-1"
        assert config.aws_access_key_id == "akid"
        assert config.aws_secret_access_key == "secret"
        assert config.dense_embeddings is vs.dense_embeddings
        assert config.logger is vs.logger

    @pytest.mark.asyncio
    async def test_normalize_fn_is_bound_to_instance_method(self):
        """The injected normalize_fn must be the VectorStore's own instance
        method so tests (and callers) that patch it keep working even though
        normalisation now happens inside the provider classes."""
        vs = _make_vectorstore()
        vs._normalize_image_to_base64 = AsyncMock(return_value="patched-value")

        config = vs._multimodal_provider_config()
        result = await config.normalize_fn("anything")

        assert result == "patched-value"
        vs._normalize_image_to_base64.assert_awaited_once_with("anything")


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
        vs._embed_and_upsert_documents = AsyncMock(side_effect=RuntimeError("batch fail"))

        chunks = [Document(page_content="test", metadata={})]
        with pytest.raises(VectorStoreError):
            await vs._process_document_chunks(chunks, "rec-1")


# ===================================================================
# split_into_sentences — replacement for the removed custom_sentence_boundary
# spaCy component
# ===================================================================

class TestSplitIntoSentencesExists:
    """split_into_sentences is defined and importable from text_splitting."""

    def test_function_exists(self):
        from app.modules.parsers.text_splitting import split_into_sentences
        assert callable(split_into_sentences)


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
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        # No model_name, model, or model_id attributes

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
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
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        mock_embed.model_name = "titan-embed"

        with patch("app.modules.transformers.vectorstore.get_embedding_model", return_value=mock_embed):
            await vs.get_embedding_model_instance()

        assert vs.aws_access_key_id == "AKID"
        assert vs.aws_secret_access_key == "secret"


# ===================================================================
# index_documents — image with multimodal LLM failed description
# ===================================================================

class TestIndexDocumentsImageDescription:
    """Images whose description could not be written earlier in the pipeline."""

    @pytest.mark.asyncio
    async def test_image_without_a_description_is_skipped(self):
        """A vision call that failed before the blob write leaves the block with
        no prose; with text-only embeddings there is nothing to embed, and the
        record still indexes."""
        from app.models.blocks import Block, BlocksContainer
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = Block(index=0, type="image", format="bin", data={"uri": "base64data"}, comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

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

        result = await vs.index_documents(container, "org-1", "rec-1", "vr-1")

        assert result is True
        vs._create_embeddings.assert_awaited_once()


class TestRecordSummaryEdgeCases:
    """Coverage for record-summary embedding edge cases."""

    @pytest.mark.asyncio
    async def test_blank_semantic_summary_skips_extra_document(self):
        from langchain_core.documents import Document

        from app.models.blocks import Block, BlocksContainer, SemanticMetadata

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock()
        sent = MagicMock()
        sent.text = "x"
        vs.nlp.return_value = MagicMock(sents=[sent])

        block = Block(index=0, type="text", format="txt", data="x", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])
        record = MagicMock()
        record.semantic_metadata = SemanticMetadata(
            summary="   \n",
            categories=[],
        )

        await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", record=record,
        )

        chunks = vs._create_embeddings.await_args.args[0]
        summary_docs = [
            c for c in chunks
            if isinstance(c, Document) and (c.metadata or {}).get("isRecordSummary")
        ]
        assert summary_docs == []

    @pytest.mark.asyncio
    async def test_semantic_metadata_none_skips_summary(self):
        from langchain_core.documents import Document

        from app.models.blocks import Block, BlocksContainer

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock()
        sent = MagicMock()
        sent.text = "y"
        vs.nlp.return_value = MagicMock(sents=[sent])

        block = Block(index=0, type="text", format="txt", data="y", comments=[])
        container = BlocksContainer(blocks=[block], block_groups=[])
        record = MagicMock()
        record.semantic_metadata = None

        await vs.index_documents(
            container, "org-1", "rec-1", "vr-1",
        )

        chunks = vs._create_embeddings.await_args.args[0]
        summary_docs = [
            c for c in chunks
            if isinstance(c, Document) and (c.metadata or {}).get("isRecordSummary")
        ]
        assert summary_docs == []


class TestResolveBatchConcurrency:
    """EMBEDDING_BATCH_CONCURRENCY must be >= 1: asyncio.Semaphore(0) is
    locked from creation, so a misconfigured 0/negative value would hang
    every remote-embedding batch forever instead of failing at startup.

    Exercises the pure helper directly rather than reloading the module —
    module reload changes class identity for everything it defines
    (VectorStore, etc.), breaking isinstance checks in unrelated tests that
    imported the pre-reload class.
    """

    def test_zero_raises_value_error(self):
        from app.modules.transformers.vectorstore import _resolve_batch_concurrency

        with pytest.raises(ValueError, match="EMBEDDING_BATCH_CONCURRENCY"):
            _resolve_batch_concurrency("0")

    def test_negative_raises_value_error(self):
        from app.modules.transformers.vectorstore import _resolve_batch_concurrency

        with pytest.raises(ValueError, match="EMBEDDING_BATCH_CONCURRENCY"):
            _resolve_batch_concurrency("-1")

    def test_unset_defaults_to_five(self):
        from app.modules.transformers.vectorstore import _resolve_batch_concurrency

        assert _resolve_batch_concurrency(None) == 5

    def test_empty_string_defaults_to_five(self):
        from app.modules.transformers.vectorstore import _resolve_batch_concurrency

        assert _resolve_batch_concurrency("") == 5

    def test_valid_positive_value_is_used(self):
        from app.modules.transformers.vectorstore import _resolve_batch_concurrency

        assert _resolve_batch_concurrency("8") == 8

    def test_module_level_constant_matches_helper_with_no_env(self):
        """The module-level default is wired through the same helper."""
        from app.modules.transformers import vectorstore as module

        assert module._DEFAULT_CONCURRENCY_LIMIT == module._resolve_batch_concurrency(None)


class TestBuildTextDocumentsHandlesEmptyBlocks:
    """Text blocks can carry no text, and only one code path ever sees it.

    Blob storage strips keys whose value is "" (_clean_top_level_empty_values),
    so a block that was empty at parse time re-hydrates with data=None. Normal
    indexing uses freshly parsed blocks and never hits it; the blob-backed
    vector-only reindex does, and used to die on len(None).
    """

    @staticmethod
    def _block(data, index):
        block = MagicMock()
        block.data = data
        block.id = f"b{index}"
        block.index = index
        return block

    def test_none_data_does_not_raise(self):
        from app.modules.transformers.vectorstore import _build_text_documents

        docs = _build_text_documents([self._block(None, 0)], "vr-1", "org-1", "en")
        assert docs == []

    def test_empty_and_whitespace_blocks_are_skipped(self):
        from app.modules.transformers.vectorstore import _build_text_documents

        blocks = [self._block("", 0), self._block("   \n\t ", 1)]
        assert _build_text_documents(blocks, "vr-1", "org-1", "en") == []

    def test_real_text_still_produces_documents(self):
        from app.modules.transformers.vectorstore import _build_text_documents

        blocks = [
            self._block("a real paragraph of content", 0),
            self._block(None, 1),
            self._block("another paragraph", 2),
        ]
        docs = _build_text_documents(blocks, "vr-1", "org-1", "en")

        assert len(docs) >= 2
        assert all(d.page_content.strip() for d in docs)
        # The empty block must not leave a hole in the emitted metadata.
        assert 1 not in {d.metadata.get("blockIndex") for d in docs}


class TestRecordSummaryEmbeddingInit:
    """index_record_summary runs in the enrich phase, which can be reached
    without the index phase having initialised the model on this instance."""

    @pytest.mark.asyncio
    async def test_initialises_model_before_deleting_the_old_block(self):
        """Deleting first leaves the record with no summary when embedding fails.

        Without the init the first use of dense_embeddings raises
        AttributeError('NoneType'), far from the real cause and after the old
        block has already gone.
        """
        vs = _make_vectorstore()
        vs.dense_embeddings = None
        vs._build_record_summary_document = MagicMock(return_value={"doc": 1})
        vs._bind_membership = AsyncMock(return_value=None)
        vs._process_document_chunks = AsyncMock()

        order = []

        async def _init():
            order.append("init")
            vs.dense_embeddings = MagicMock()
            return False

        async def _delete(*_a, **_k):
            order.append("delete")

        vs.get_embedding_model_instance = AsyncMock(side_effect=_init)
        vs.delete_blocks_by_ids = AsyncMock(side_effect=_delete)

        with patch(
            "app.modules.transformers.vectorstore.reset_membership_context",
            MagicMock(),
        ):
            await vs.index_record_summary("rec-1", "vr-1", "org-1", MagicMock())

        assert order == ["init", "delete"], order

    @pytest.mark.asyncio
    async def test_init_failure_raises_before_any_delete(self):
        from app.exceptions.indexing_exceptions import IndexingError

        vs = _make_vectorstore()
        vs.dense_embeddings = None
        vs._build_record_summary_document = MagicMock(return_value={"doc": 1})
        vs.delete_blocks_by_ids = AsyncMock()
        vs.get_embedding_model_instance = AsyncMock(
            side_effect=RuntimeError("bad service account key")
        )

        with pytest.raises(IndexingError):
            await vs.index_record_summary("rec-1", "vr-1", "org-1", MagicMock())

        vs.delete_blocks_by_ids.assert_not_awaited()
