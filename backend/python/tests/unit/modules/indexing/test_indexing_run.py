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

    @pytest.mark.skip(reason="FastEmbedSparse not used in IndexingPipeline.__init__")
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


@pytest.mark.skip(reason="_initialize_collection is in VectorStore, not IndexingPipeline")
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
    async def test_rewrites_ids_with_remaining_records(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-1"]
        )
        pipeline.graph_provider.get_document = AsyncMock(
            return_value={"connectorId": "conn-1", "recordGroupId": "rg-1"}
        )
        pipeline.graph_provider.get_edges_from_node = AsyncMock(return_value=[])
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.set_payload = AsyncMock()
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.set_payload.assert_awaited_once()
        pipeline.vector_db_service.delete_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_safe_ids(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_rewrite_and_delete(self):
        pipeline = _make_indexing_pipeline()

        async def remaining(virtual_record_id):
            return ["rec-keep"] if virtual_record_id == "vr-shared" else []

        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            side_effect=remaining
        )
        pipeline.graph_provider.get_document = AsyncMock(
            return_value={"connectorId": "conn-2"}
        )
        pipeline.graph_provider.get_edges_from_node = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.set_payload = AsyncMock()
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-shared", "vr-last"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 2
        pipeline.vector_db_service.set_payload.assert_awaited_once()
        pipeline.vector_db_service.delete_points.assert_awaited_once()
        pipeline.graph_provider.delete_nodes.assert_awaited_once()


class TestBulkDeleteConfirmation:
    """Bulk deletion must re-confirm before destroying points."""

    @pytest.mark.asyncio
    async def test_vrid_that_reappears_is_rewritten_not_deleted(self):
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.indexing import run as run_mod

        gp = AsyncMock()
        # empty on the candidate pass, populated on the confirming pass
        reads = [[], ["r1"]]
        gp.get_records_by_virtual_record_id = AsyncMock(
            side_effect=lambda *a, **k: reads.pop(0) if reads else ["r1"]
        )
        gp.get_document = AsyncMock(return_value={"connectorId": "c1"})
        gp.get_edges_from_node = AsyncMock(return_value=[])

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        pipeline = run_mod.IndexingPipeline(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=gp,
            collection_name="records",
            vector_db_service=vdb,
        )

        original = run_mod.EMPTY_CONFIRM_DELAY_SECONDS
        run_mod.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            result = await pipeline.bulk_delete_embeddings(["vr-lag"])
        finally:
            run_mod.EMPTY_CONFIRM_DELAY_SECONDS = original

        assert result["success"] is True
        assert result["virtual_record_ids_deleted"] == 0
        assert result["virtual_record_ids_rewritten"] == 1
        vdb.delete_points.assert_not_awaited()
        gp.delete_nodes.assert_not_awaited()
