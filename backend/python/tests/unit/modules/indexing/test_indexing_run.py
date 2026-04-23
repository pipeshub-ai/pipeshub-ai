"""Unit tests for app.modules.indexing.run.CustomChunker and IndexingPipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import MetadataProcessingError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunker():
    """Create a CustomChunker with mocked embedding dependency."""
    with patch(
        "app.modules.indexing.run.get_default_embedding_model"
    ) as mock_embed:
        mock_embed.return_value = (MagicMock(), 1024)
        from app.modules.indexing.run import CustomChunker

        chunker = CustomChunker(
            logger=MagicMock(),
            embeddings=MagicMock(),
        )
        return chunker


# ===================================================================
# _merge_bboxes
# ===================================================================

class TestMergeBboxes:
    """Tests for CustomChunker._merge_bboxes."""

    def test_single_box(self):
        chunker = _make_chunker()
        bbox = [
            {"x": 10, "y": 20},
            {"x": 100, "y": 20},
            {"x": 100, "y": 80},
            {"x": 10, "y": 80},
        ]
        result = chunker._merge_bboxes([bbox])
        assert result == [
            {"x": 10, "y": 20},
            {"x": 100, "y": 20},
            {"x": 100, "y": 80},
            {"x": 10, "y": 80},
        ]

    def test_multiple_boxes_finds_extremes(self):
        chunker = _make_chunker()
        bbox1 = [
            {"x": 10, "y": 20},
            {"x": 50, "y": 20},
            {"x": 50, "y": 60},
            {"x": 10, "y": 60},
        ]
        bbox2 = [
            {"x": 30, "y": 10},
            {"x": 100, "y": 10},
            {"x": 100, "y": 80},
            {"x": 30, "y": 80},
        ]
        result = chunker._merge_bboxes([bbox1, bbox2])
        # Should find min x=10, min y=10, max x=100, max y=80
        assert result == [
            {"x": 10, "y": 10},   # top-left
            {"x": 100, "y": 10},  # top-right
            {"x": 100, "y": 80},  # bottom-right
            {"x": 10, "y": 80},   # bottom-left
        ]

    def test_empty_list_returns_empty(self):
        chunker = _make_chunker()
        result = chunker._merge_bboxes([])
        assert result == []

    def test_three_boxes(self):
        chunker = _make_chunker()
        bbox1 = [{"x": 0, "y": 0}, {"x": 5, "y": 5}]
        bbox2 = [{"x": 3, "y": 3}, {"x": 10, "y": 10}]
        bbox3 = [{"x": -2, "y": 1}, {"x": 8, "y": 15}]
        result = chunker._merge_bboxes([bbox1, bbox2, bbox3])
        assert result == [
            {"x": -2, "y": 0},
            {"x": 10, "y": 0},
            {"x": 10, "y": 15},
            {"x": -2, "y": 15},
        ]

    def test_invalid_bbox_format_raises(self):
        """Non-list bbox should raise MetadataProcessingError."""
        chunker = _make_chunker()
        with pytest.raises(MetadataProcessingError):
            chunker._merge_bboxes(["not_a_list_of_dicts"])

    def test_missing_coordinate_key_raises(self):
        """Bbox points missing x or y should raise MetadataProcessingError."""
        chunker = _make_chunker()
        bbox = [{"x": 10}, {"y": 20}]  # Each point missing one key
        with pytest.raises(MetadataProcessingError):
            chunker._merge_bboxes([bbox])

    def test_identical_boxes(self):
        chunker = _make_chunker()
        bbox = [
            {"x": 5, "y": 5},
            {"x": 15, "y": 5},
            {"x": 15, "y": 15},
            {"x": 5, "y": 15},
        ]
        result = chunker._merge_bboxes([bbox, bbox])
        assert result == [
            {"x": 5, "y": 5},
            {"x": 15, "y": 5},
            {"x": 15, "y": 15},
            {"x": 5, "y": 15},
        ]


# ===================================================================
# _merge_metadata
# ===================================================================

class TestMergeMetadata:
    """Tests for CustomChunker._merge_metadata."""

    def test_empty_list_returns_empty_dict(self):
        chunker = _make_chunker()
        result = chunker._merge_metadata([])
        assert result == {}

    def test_single_metadata(self):
        chunker = _make_chunker()
        meta = {"page": 1, "source": "doc.pdf"}
        result = chunker._merge_metadata([meta])
        assert result["page"] == 1
        assert result["source"] == "doc.pdf"

    def test_identical_values_kept_as_single(self):
        chunker = _make_chunker()
        meta1 = {"source": "doc.pdf", "type": "text"}
        meta2 = {"source": "doc.pdf", "type": "text"}
        result = chunker._merge_metadata([meta1, meta2])
        assert result["source"] == "doc.pdf"
        assert result["type"] == "text"

    def test_different_values_kept_as_list(self):
        chunker = _make_chunker()
        meta1 = {"page": 1}
        meta2 = {"page": 2}
        meta3 = {"page": 3}
        result = chunker._merge_metadata([meta1, meta2, meta3])
        assert result["page"] == [1, 2, 3]

    def test_list_fields_deduplicated(self):
        chunker = _make_chunker()
        meta1 = {"tags": ["a", "b", "c"]}
        meta2 = {"tags": ["b", "c", "d"]}
        result = chunker._merge_metadata([meta1, meta2])
        # Should have unique values: a, b, c, d
        assert set(result["tags"]) == {"a", "b", "c", "d"}
        # Order should be preserved (first appearance)
        assert result["tags"] == ["a", "b", "c", "d"]

    def test_confidence_score_keeps_max(self):
        chunker = _make_chunker()
        meta1 = {"confidence_score": 0.7}
        meta2 = {"confidence_score": 0.9}
        meta3 = {"confidence_score": 0.8}
        result = chunker._merge_metadata([meta1, meta2, meta3])
        assert result["confidence_score"] == 0.9

    def test_none_values_excluded(self):
        chunker = _make_chunker()
        meta1 = {"key": "value", "null_key": None}
        meta2 = {"key": "value", "null_key": None}
        result = chunker._merge_metadata([meta1, meta2])
        assert "null_key" not in result
        assert result["key"] == "value"

    def test_mixed_fields(self):
        """Test multiple field types merged together."""
        chunker = _make_chunker()
        meta1 = {
            "page": 1,
            "source": "same.pdf",
            "tags": ["a"],
            "confidence_score": 0.5,
        }
        meta2 = {
            "page": 2,
            "source": "same.pdf",
            "tags": ["a", "b"],
            "confidence_score": 0.8,
        }
        result = chunker._merge_metadata([meta1, meta2])

        assert result["page"] == [1, 2]  # different values -> list
        assert result["source"] == "same.pdf"  # same value -> single
        assert result["tags"] == ["a", "b"]  # deduplicated list
        assert result["confidence_score"] == 0.8  # max

    def test_different_unique_values_deduplicated(self):
        """When values differ, only unique ones kept."""
        chunker = _make_chunker()
        meta1 = {"author": "Alice"}
        meta2 = {"author": "Bob"}
        meta3 = {"author": "Alice"}  # duplicate
        result = chunker._merge_metadata([meta1, meta2, meta3])
        assert result["author"] == ["Alice", "Bob"]

    def test_non_list_input_raises(self):
        chunker = _make_chunker()
        with pytest.raises(MetadataProcessingError):
            chunker._merge_metadata("not a list")

    def test_partial_fields(self):
        """Metadata dicts with different keys are merged correctly."""
        chunker = _make_chunker()
        meta1 = {"field_a": "val1"}
        meta2 = {"field_b": "val2"}
        result = chunker._merge_metadata([meta1, meta2])
        assert result["field_a"] == "val1"
        assert result["field_b"] == "val2"

    def test_field_in_one_metadata_none_in_another(self):
        """A field present in one dict but None in another should keep the non-None value."""
        chunker = _make_chunker()
        meta1 = {"key": "value"}
        meta2 = {"key": None}
        result = chunker._merge_metadata([meta1, meta2])
        assert result["key"] == "value"


# ===================================================================
# split_text
# ===================================================================

# ===================================================================
# split_documents
# ===================================================================


class TestSplitDocuments:
    """Tests for CustomChunker.split_documents."""

    def test_single_document_returned_as_is(self):
        """Single document is returned without splitting."""
        chunker = _make_chunker()
        from langchain_core.documents import Document
        doc = Document(page_content="Hello world", metadata={})
        result = chunker.split_documents([doc])
        assert result == [doc]

    def test_empty_list_returned_as_is(self):
        """Empty list is returned without splitting."""
        chunker = _make_chunker()
        result = chunker.split_documents([])
        assert result == []


class TestSplitText:
    """Tests for CustomChunker.split_text."""

    def test_returns_text_as_is(self):
        chunker = _make_chunker()
        result = chunker.split_text("hello world")
        assert result == ["hello world"]

    def test_empty_string(self):
        chunker = _make_chunker()
        result = chunker.split_text("")
        assert result == [""]


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
        # 3 indexes: virtualRecordId, orgId, and _kind (sentinel-marker)
        assert pipeline.vector_db_service.create_index.call_count == 3

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
        assert result["deleted_count"] == 0

    @pytest.mark.asyncio
    async def test_filters_empty_ids(self):
        pipeline = _make_indexing_pipeline()
        result = await pipeline.bulk_delete_embeddings(["", "  "])
        assert result["success"] is True
        assert result["deleted_count"] == 0

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
