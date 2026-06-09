"""Unit tests for app.modules.indexing.run.IndexingPipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import MetadataProcessingError


# ===================================================================
# IndexingPipeline
# ===================================================================


def _make_indexing_pipeline():
    """Create an IndexingPipeline with all dependencies mocked."""
    with patch(
        "app.modules.indexing.run.FastEmbedSparse"
    ) as mock_sparse:
        mock_sparse.return_value = MagicMock()
        from app.modules.indexing.run import IndexingPipeline

        pipeline = IndexingPipeline(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test_collection",
            vector_db_service=AsyncMock(),
        )
        return pipeline


class TestIndexingPipelineInit:
    """Tests for IndexingPipeline.__init__."""

    def test_stores_all_deps(self):
        pipeline = _make_indexing_pipeline()
        assert pipeline.collection_name == "test_collection"
        assert pipeline.vector_store is None

    def test_sparse_embed_failure_raises(self):
        """Raises IndexingError when sparse embed init fails."""
        from app.exceptions.indexing_exceptions import IndexingError
        with patch(
            "app.modules.indexing.run.FastEmbedSparse",
            side_effect=RuntimeError("sparse fail"),
        ):
            with pytest.raises(IndexingError):
                from app.modules.indexing.run import IndexingPipeline
                IndexingPipeline(
                    logger=MagicMock(),
                    config_service=AsyncMock(),
                    graph_provider=AsyncMock(),
                    collection_name="test",
                    vector_db_service=AsyncMock(),
                )


class TestIndexingPipelineInitializeCollection:
    """Tests for IndexingPipeline._initialize_collection."""

    @pytest.mark.asyncio
    async def test_creates_collection_when_not_found(self):
        pipeline = _make_indexing_pipeline()
        pipeline.vector_db_service.get_collection = AsyncMock(return_value=None)
        pipeline.vector_db_service.create_collection = AsyncMock()
        pipeline.vector_db_service.create_index = AsyncMock()

        await pipeline._initialize_collection(embedding_size=768)

        pipeline.vector_db_service.create_collection.assert_awaited_once()
        assert pipeline.vector_db_service.create_index.call_count == 2

    @pytest.mark.asyncio
    async def test_recreates_on_mismatch(self):
        pipeline = _make_indexing_pipeline()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=512)}
        pipeline.vector_db_service.get_collection = AsyncMock(return_value=mock_info)
        pipeline.vector_db_service.delete_collection = AsyncMock()
        pipeline.vector_db_service.create_collection = AsyncMock()
        pipeline.vector_db_service.create_index = AsyncMock()

        await pipeline._initialize_collection(embedding_size=768)

        pipeline.vector_db_service.delete_collection.assert_awaited_once()
        pipeline.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_recreate_when_same_size(self):
        pipeline = _make_indexing_pipeline()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=768)}
        pipeline.vector_db_service.get_collection = AsyncMock(return_value=mock_info)

        await pipeline._initialize_collection(embedding_size=768)

        pipeline.vector_db_service.create_collection.assert_not_awaited()


class TestIndexingPipelineGetEmbeddingModelInstance:
    """Tests for IndexingPipeline.get_embedding_model_instance."""

    @pytest.mark.asyncio
    async def test_default_model_when_no_config(self):
        pipeline = _make_indexing_pipeline()
        pipeline.config_service.get_config = AsyncMock(return_value={"embedding": []})
        pipeline._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.embed_query.return_value = [0.1] * 768
        mock_embed.model_name = "default-model"

        with patch("app.modules.indexing.run.get_default_embedding_model", return_value=mock_embed):
            with patch("app.modules.indexing.run.QdrantVectorStore"):
                result = await pipeline.get_embedding_model_instance()

        assert result is True

    @pytest.mark.asyncio
    async def test_configured_default_model(self):
        pipeline = _make_indexing_pipeline()
        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
        }
        pipeline.config_service.get_config = AsyncMock(return_value={"embedding": [config]})
        pipeline._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.embed_query.return_value = [0.1] * 1536
        mock_embed.model_name = "test-model"

        with patch("app.modules.indexing.run.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.indexing.run.QdrantVectorStore"):
                result = await pipeline.get_embedding_model_instance()

        assert result is True

    @pytest.mark.asyncio
    async def test_no_default_falls_back_to_first(self):
        pipeline = _make_indexing_pipeline()
        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": False,
        }
        pipeline.config_service.get_config = AsyncMock(return_value={"embedding": [config]})
        pipeline._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.embed_query.return_value = [0.1] * 1024
        mock_embed.model_name = "fallback-model"

        with patch("app.modules.indexing.run.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.indexing.run.QdrantVectorStore"):
                result = await pipeline.get_embedding_model_instance()

        assert result is True

    @pytest.mark.asyncio
    async def test_embed_query_failure_raises(self):
        from app.exceptions.indexing_exceptions import IndexingError
        pipeline = _make_indexing_pipeline()
        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
        }
        pipeline.config_service.get_config = AsyncMock(return_value={"embedding": [config]})

        mock_embed = MagicMock()
        mock_embed.embed_query.side_effect = RuntimeError("API error")

        with patch("app.modules.indexing.run.get_embedding_model", return_value=mock_embed):
            with pytest.raises(IndexingError):
                await pipeline.get_embedding_model_instance()

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model(self):
        pipeline = _make_indexing_pipeline()
        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
        }
        pipeline.config_service.get_config = AsyncMock(return_value={"embedding": [config]})
        pipeline._initialize_collection = AsyncMock()

        mock_embed = MagicMock(spec=[])
        mock_embed.embed_query = MagicMock(return_value=[0.1] * 1024)
        mock_embed.model = "via-model-attr"

        with patch("app.modules.indexing.run.get_embedding_model", return_value=mock_embed):
            with patch("app.modules.indexing.run.QdrantVectorStore"):
                await pipeline.get_embedding_model_instance()


class TestIndexingPipelineCreateEmbeddings:
    """Tests for IndexingPipeline._create_embeddings."""

    @pytest.mark.asyncio
    async def test_no_chunks_raises(self):
        from app.exceptions.indexing_exceptions import EmbeddingError
        pipeline = _make_indexing_pipeline()
        with pytest.raises(EmbeddingError, match="No chunks"):
            await pipeline._create_embeddings([])

    @pytest.mark.asyncio
    async def test_successful_embedding(self):
        from langchain_core.documents import Document
        pipeline = _make_indexing_pipeline()
        pipeline.vector_store = AsyncMock()
        pipeline.vector_store.aadd_documents = AsyncMock()
        pipeline.graph_provider.get_document = AsyncMock(return_value={
            "_key": "rec-1",
            "recordId": "rec-1",
        })
        pipeline.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        chunks = [
            Document(
                page_content="test",
                metadata={
                    "virtualRecordId": "vr-1",
                    "recordId": "rec-1",
                    "blockType": "text",
                },
            )
        ]

        await pipeline._create_embeddings(chunks)

        pipeline.vector_store.aadd_documents.assert_awaited()

    @pytest.mark.asyncio
    async def test_record_not_found_raises(self):
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        pipeline = _make_indexing_pipeline()
        pipeline.vector_store = AsyncMock()
        pipeline.vector_store.aadd_documents = AsyncMock()
        pipeline.graph_provider.get_document = AsyncMock(return_value=None)

        chunks = [
            Document(
                page_content="test",
                metadata={
                    "virtualRecordId": "vr-1",
                    "recordId": "rec-1",
                    "blockType": "text",
                },
            )
        ]

        with pytest.raises(DocumentProcessingError, match="Record not found"):
            await pipeline._create_embeddings(chunks)

    @pytest.mark.asyncio
    async def test_upsert_failure_raises(self):
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        pipeline = _make_indexing_pipeline()
        pipeline.vector_store = AsyncMock()
        pipeline.vector_store.aadd_documents = AsyncMock()
        pipeline.graph_provider.get_document = AsyncMock(return_value={
            "_key": "rec-1",
            "recordId": "rec-1",
        })
        pipeline.graph_provider.batch_upsert_nodes = AsyncMock(return_value=False)

        chunks = [
            Document(
                page_content="test",
                metadata={
                    "virtualRecordId": "vr-1",
                    "recordId": "rec-1",
                    "blockType": "text",
                },
            )
        ]

        with pytest.raises(DocumentProcessingError, match="Failed to update"):
            await pipeline._create_embeddings(chunks)

    @pytest.mark.asyncio
    async def test_vectorstore_failure_raises(self):
        from langchain_core.documents import Document
        from app.exceptions.indexing_exceptions import VectorStoreError
        pipeline = _make_indexing_pipeline()
        pipeline.vector_store = AsyncMock()
        pipeline.vector_store.aadd_documents = AsyncMock(side_effect=RuntimeError("store fail"))

        chunks = [
            Document(
                page_content="test",
                metadata={
                    "virtualRecordId": "vr-1",
                    "recordId": "rec-1",
                    "blockType": "text",
                },
            )
        ]

        with pytest.raises(VectorStoreError):
            await pipeline._create_embeddings(chunks)


class TestIndexingPipelineDeleteEmbeddings:
    """Tests for IndexingPipeline.delete_embeddings."""

    @pytest.mark.asyncio
    async def test_no_record_id_raises(self):
        from app.exceptions.indexing_exceptions import EmbeddingDeletionError
        pipeline = _make_indexing_pipeline()
        with pytest.raises(EmbeddingDeletionError, match="No record ID"):
            await pipeline.delete_embeddings("", "vr-1")

    @pytest.mark.asyncio
    async def test_no_virtual_record_id_returns(self):
        pipeline = _make_indexing_pipeline()
        await pipeline.delete_embeddings("rec-1", "")
        # Should return early without error

    @pytest.mark.asyncio
    async def test_other_records_exist_skips_deletion(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-2"]
        )

        await pipeline.delete_embeddings("rec-1", "vr-1")

        # Should not try to delete from vector store

    @pytest.mark.asyncio
    async def test_deletes_when_only_record(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-1"]
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.scroll = AsyncMock(return_value=([MagicMock(id="pt-1")], None))
        pipeline.get_embedding_model_instance = AsyncMock()
        pipeline.vector_store = AsyncMock()
        pipeline.vector_store.adelete = AsyncMock()

        await pipeline.delete_embeddings("rec-1", "vr-1")

        pipeline.vector_store.adelete.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_embeddings_found_returns(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-1"]
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.scroll = AsyncMock(return_value=None)

        await pipeline.delete_embeddings("rec-1", "vr-1")
        # No error, returns early


class TestIndexingPipelineIndexDocuments:
    """Tests for IndexingPipeline.index_documents."""

    @pytest.mark.asyncio
    async def test_empty_sentences_marks_empty(self):
        from app.config.constants.arangodb import ProgressStatus
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})
        pipeline.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await pipeline.index_documents([], "rec-1")

        assert result == []
        call_args = pipeline.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert call_args["indexingStatus"] == ProgressStatus.EMPTY.value

    @pytest.mark.asyncio
    async def test_none_sentences_marks_empty(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})
        pipeline.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await pipeline.index_documents(None, "rec-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_filters_empty_text(self):
        """Sentences with empty text are filtered out."""
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})
        pipeline.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        sentences = [
            {"text": "  ", "metadata": {}},  # whitespace only
            {"text": None, "metadata": {}},  # None
        ]

        result = await pipeline.index_documents(sentences, "rec-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_indexing(self):
        pipeline = _make_indexing_pipeline()
        pipeline.get_embedding_model_instance = AsyncMock()
        pipeline._create_embeddings = AsyncMock()

        sentences = [
            {"text": "Hello world", "metadata": {"virtualRecordId": "vr-1"}},
        ]

        result = await pipeline.index_documents(sentences, "rec-1")
        assert len(result) == 1
        pipeline._create_embeddings.assert_awaited()

    @pytest.mark.asyncio
    async def test_record_not_found_for_empty_raises(self):
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_document = AsyncMock(return_value=None)

        with pytest.raises(DocumentProcessingError, match="Record not found"):
            await pipeline.index_documents([], "rec-1")


class TestIndexingPipelineProcessMetadata:
    """Tests for IndexingPipeline._process_metadata."""

    def test_basic_metadata(self):
        pipeline = _make_indexing_pipeline()
        meta = {
            "orgId": "org-1",
            "virtualRecordId": "vr-1",
            "recordName": "test.pdf",
            "blockType": "text",
        }
        result = pipeline._process_metadata(meta)
        assert result["orgId"] == "org-1"
        assert result["virtualRecordId"] == "vr-1"
        assert result["recordName"] == "test.pdf"
        assert result["blockType"] == "text"

    def test_block_type_list_takes_first(self):
        pipeline = _make_indexing_pipeline()
        meta = {"blockType": ["heading", "text"]}
        result = pipeline._process_metadata(meta)
        assert result["blockType"] == "heading"

    def test_optional_fields(self):
        pipeline = _make_indexing_pipeline()
        meta = {
            "bounding_box": [{"x": 0, "y": 0}],
            "sheetName": "Sheet1",
            "sheetNum": 1,
            "pageNum": 3,
        }
        result = pipeline._process_metadata(meta)
        assert result["bounding_box"] == [{"x": 0, "y": 0}]
        assert result["sheetName"] == "Sheet1"
        assert result["sheetNum"] == 1
        assert result["pageNum"] == 3

    def test_defaults_for_missing_fields(self):
        pipeline = _make_indexing_pipeline()
        meta = {}
        result = pipeline._process_metadata(meta)
        assert result["orgId"] == ""
        assert result["virtualRecordId"] == ""
        assert result["blockType"] == "text"
        assert result["blockNum"] == [0]


class TestIndexingPipelineBulkDelete:
    """Tests for IndexingPipeline.bulk_delete_embeddings."""

    @pytest.mark.asyncio
    async def test_empty_list_returns_success(self):
        pipeline = _make_indexing_pipeline()
        result = await pipeline.bulk_delete_embeddings([])
        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 0

    @pytest.mark.asyncio
    async def test_filters_empty_ids(self):
        pipeline = _make_indexing_pipeline()
        result = await pipeline.bulk_delete_embeddings(["", "  "])
        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 0

    @pytest.mark.asyncio
    async def test_skips_ids_with_remaining_records(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-1"]
        )

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["virtual_record_ids_processed"] == 0

    @pytest.mark.asyncio
    async def test_deletes_safe_ids(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.get_embedding_model_instance = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.scroll = AsyncMock(return_value=([MagicMock(id="pt-1")], None))
        pipeline.vector_store = AsyncMock()
        pipeline.vector_store.adelete = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
