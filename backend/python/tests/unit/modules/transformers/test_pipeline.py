"""Unit tests for app.modules.transformers.pipeline.IndexingPipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlocksContainer
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import TransformContext

_SENTINEL = object()


def _make_record(blocks=_SENTINEL, block_groups=_SENTINEL, record_id="rec-123"):
    """Create a mock Record with the given blocks/block_groups.

    By default (sentinel), blocks and block_groups are set to empty lists.
    Pass None explicitly to set them to None.
    """
    record = MagicMock()
    record.id = record_id
    container = MagicMock()
    container.blocks = [] if blocks is _SENTINEL else blocks
    container.block_groups = [] if block_groups is _SENTINEL else block_groups
    record.block_containers = container
    return record


def _make_ctx(record):
    """Wrap a record in a mock TransformContext."""
    ctx = MagicMock()
    ctx.record = record
    return ctx


@pytest.fixture
def doc_extraction():
    de = AsyncMock()
    de.graph_provider = AsyncMock()
    de.graph_provider.get_document = AsyncMock(return_value={})
    de.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    return de


@pytest.fixture
def sink_orchestrator():
    return AsyncMock()


@pytest.fixture
def pipeline(doc_extraction, sink_orchestrator):
    return IndexingPipeline(doc_extraction, sink_orchestrator)


# ---------------------------------------------------------------------------
# apply -- empty blocks and block_groups
# ---------------------------------------------------------------------------
class TestApplyEmpty:
    @pytest.mark.asyncio
    async def test_empty_blocks_marks_empty_and_returns(self, pipeline, doc_extraction, sink_orchestrator):
        record = _make_record(blocks=[], block_groups=[], record_id="rec-1")
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        # Should fetch the document from graph
        doc_extraction.graph_provider.get_document.assert_awaited_once_with(
            "rec-1", CollectionNames.RECORDS.value
        )
        # Should batch upsert with EMPTY status
        doc_extraction.graph_provider.batch_upsert_nodes.assert_awaited_once()
        call_args = doc_extraction.graph_provider.batch_upsert_nodes.call_args
        docs = call_args[0][0]
        assert docs[0]["indexingStatus"] == ProgressStatus.EMPTY.value
        assert docs[0]["isDirty"] is False
        assert docs[0]["extractionStatus"] == ProgressStatus.NOT_STARTED.value

        # Should NOT call document_extraction.apply or sink_orchestrator.apply
        doc_extraction.apply.assert_not_awaited()
        sink_orchestrator.apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_blocks_upsert_failure_raises(self, pipeline, doc_extraction):
        doc_extraction.graph_provider.batch_upsert_nodes = AsyncMock(return_value=False)
        record = _make_record(blocks=[], block_groups=[], record_id="rec-fail")
        ctx = _make_ctx(record)

        with pytest.raises(DocumentProcessingError):
            await pipeline.apply(ctx)

    @pytest.mark.asyncio
    async def test_blocks_none_does_not_take_empty_path(self, pipeline, doc_extraction, sink_orchestrator):
        """When blocks is None (not an empty list), the empty check should not trigger."""
        record = _make_record(blocks=None, block_groups=None)
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        # Should go through the normal path since None != len==0
        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.apply.assert_awaited_once_with(ctx)


# ---------------------------------------------------------------------------
# apply -- non-empty blocks
# ---------------------------------------------------------------------------
class TestApplyNonEmpty:
    @pytest.mark.asyncio
    async def test_non_empty_calls_extraction_then_sink(self, pipeline, doc_extraction, sink_orchestrator):
        block = MagicMock()
        record = _make_record(blocks=[block], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_non_empty_block_groups_calls_extraction_then_sink(self, pipeline, doc_extraction, sink_orchestrator):
        bg = MagicMock()
        record = _make_record(blocks=[], block_groups=[bg])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_both_blocks_and_groups_calls_extraction_then_sink(self, pipeline, doc_extraction, sink_orchestrator):
        block = MagicMock()
        bg = MagicMock()
        record = _make_record(blocks=[block], block_groups=[bg])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_extraction_called_before_sink(self, pipeline, doc_extraction, sink_orchestrator):
        """Verify ordering: document_extraction.apply is called before sink_orchestrator.apply."""
        call_order = []

        async def track_extraction(ctx):
            call_order.append("extraction")

        async def track_sink(ctx):
            call_order.append("sink")

        doc_extraction.apply = track_extraction
        sink_orchestrator.apply = track_sink

        block = MagicMock()
        record = _make_record(blocks=[block], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        assert call_order == ["extraction", "sink"]

    @pytest.mark.asyncio
    async def test_exception_in_extraction_propagates(self, pipeline, doc_extraction, sink_orchestrator):
        doc_extraction.apply = AsyncMock(side_effect=RuntimeError("extraction boom"))
        record = _make_record(blocks=[MagicMock()], block_groups=[])
        ctx = _make_ctx(record)

        with pytest.raises(RuntimeError, match="extraction boom"):
            await pipeline.apply(ctx)

        sink_orchestrator.apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_sink_propagates(self, pipeline, doc_extraction, sink_orchestrator):
        sink_orchestrator.apply = AsyncMock(side_effect=RuntimeError("sink boom"))
        record = _make_record(blocks=[MagicMock()], block_groups=[])
        ctx = _make_ctx(record)

        with pytest.raises(RuntimeError, match="sink boom"):
            await pipeline.apply(ctx)
