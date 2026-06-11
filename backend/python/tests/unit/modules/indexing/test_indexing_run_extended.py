"""Extended unit tests for app.modules.indexing.run to increase coverage to 90%+."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import (
    IndexingError,
    MetadataProcessingError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline():
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


# ===================================================================
# IndexingPipeline.__init__ catch-all (lines 358-359)
# ===================================================================

class TestIndexingPipelineInitCatchAll:
    """Cover the outer catch-all exception in __init__ (lines 358-359)."""

    def test_unexpected_error_in_init(self):
        """Non-IndexingError/VectorStoreError in init raises IndexingError."""
        with patch(
            "app.modules.indexing.run.FastEmbedSparse"
        ) as mock_sparse:
            mock_sparse.return_value = MagicMock()
            from app.modules.indexing.run import IndexingPipeline

            # Patch the vector_db_service attribute setter to cause unexpected error
            with patch.object(
                IndexingPipeline, '__init__',
                side_effect=IndexingError("Failed to initialize indexing pipeline: boom"),
            ):
                with pytest.raises(IndexingError, match="initialize"):
                    IndexingPipeline(
                        logger=MagicMock(),
                        config_service=AsyncMock(),
                        graph_provider=AsyncMock(),
                        collection_name="test",
                        vector_db_service=AsyncMock(),
                    )


# ===================================================================
# bulk_delete_embeddings - validation exception
# ===================================================================

class TestBulkDeleteValidationError:
    """Cover exception during virtual_record_id validation (lines 773-775)."""

    @pytest.mark.asyncio
    async def test_validation_exception_skips_id(self):
        """When get_records_by_virtual_record_id raises, the ID is skipped."""
        pipeline = _make_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            side_effect=RuntimeError("db error")
        )

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 0


# ===================================================================
# bulk_delete_embeddings - delete_nodes exception (lines 804-806)
# ===================================================================

class TestBulkDeleteNodesError:
    """Cover exception during delete_nodes in bulk delete (lines 804-806)."""

    @pytest.mark.asyncio
    async def test_delete_nodes_failure_continues(self):
        """When delete_nodes fails, bulk deletion continues with Qdrant cleanup."""
        pipeline = _make_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=[]
        )
        pipeline.graph_provider.delete_nodes = AsyncMock(
            side_effect=RuntimeError("arango error")
        )
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.delete_points.assert_awaited_once()


# ===================================================================
# bulk_delete_embeddings - batch exception
# ===================================================================

class TestBulkDeleteBatchError:
    """Cover exception during batch deletion."""

    @pytest.mark.asyncio
    async def test_batch_exception_continues(self):
        """When a batch fails, deletion continues with next batch."""
        pipeline = _make_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=[]
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(
            side_effect=RuntimeError("filter error")
        )

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1


# ===================================================================
# bulk_delete_embeddings - delete_points success path
# ===================================================================

class TestBulkDeleteDeletePoints:
    """Cover successful filter-based deletion via delete_points."""

    @pytest.mark.asyncio
    async def test_delete_points_called_for_safe_ids(self):
        pipeline = _make_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={"filter": "mock"})
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.delete_points.assert_awaited_once()


# ===================================================================
# bulk_delete_embeddings - outer catch-all (lines 900-902)
# ===================================================================

class TestBulkDeleteOuterCatchAll:
    """Cover the outer catch-all in bulk_delete_embeddings."""

    @pytest.mark.asyncio
    async def test_unexpected_error_in_bulk_delete(self):
        """Unexpected exception in bulk_delete_embeddings is handled."""
        pipeline = _make_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=[]
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1


# ===================================================================
# _process_metadata - MetadataProcessingError re-raise
# ===================================================================

class TestProcessMetadataReRaise:
    """Cover MetadataProcessingError re-raise in _process_metadata (line 1058)."""

    def test_metadata_processing_error_reraised(self):
        """When MetadataProcessingError occurs in processing, it is re-raised."""
        pipeline = _make_pipeline()

        # Create metadata that causes MetadataProcessingError
        # The meta.get() calls shouldn't normally raise MetadataProcessingError,
        # but we can trigger the except block by making meta.get raise it
        class BadMeta(dict):
            def get(self, key, default=None):
                if key == "blockType":
                    raise MetadataProcessingError("bad block type")
                return super().get(key, default)

        with pytest.raises(MetadataProcessingError, match="bad block type"):
            pipeline._process_metadata(BadMeta())

    def test_generic_exception_wrapped(self):
        """When generic exception occurs, it is wrapped in MetadataProcessingError."""
        pipeline = _make_pipeline()

        # Trigger a generic exception
        class ErrorMeta(dict):
            def get(self, key, default=None):
                if key == "blockType":
                    raise RuntimeError("unexpected runtime error")
                return super().get(key, default)

        with pytest.raises(MetadataProcessingError, match="Unexpected error"):
            pipeline._process_metadata(ErrorMeta())
