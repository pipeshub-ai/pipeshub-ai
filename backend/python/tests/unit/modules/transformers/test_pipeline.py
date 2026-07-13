"""Unit tests for app.modules.transformers.pipeline.IndexingPipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import Block, BlockGroup, BlockType, DataFormat, GroupType
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import ReconciliationContext, TransformContext

_SENTINEL = object()


def _valid_text_block(index: int = 0) -> Block:
    return Block(index=index, type=BlockType.TEXT, data="sample text", format=DataFormat.TXT)


def _valid_text_section_group(index: int = 0) -> BlockGroup:
    return BlockGroup(index=index, type=GroupType.TEXT_SECTION)


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
    ctx.settings = {}
    ctx.event_type = None
    ctx.reconciliation_context = None
    ctx.prev_virtual_record_id = None
    return ctx


@pytest.fixture
def doc_extraction():
    de = AsyncMock()
    de.graph_provider = AsyncMock()
    de.graph_provider.get_document = AsyncMock(return_value={})
    de.graph_provider.batch_update_nodes = AsyncMock(return_value=True)
    return de


@pytest.fixture
def sink_orchestrator():
    return AsyncMock()


@pytest.fixture
def pipeline(doc_extraction, sink_orchestrator):
    pipe = IndexingPipeline(doc_extraction, sink_orchestrator)
    pipe.logger = MagicMock()  # Replace real logger with mock for assertion support
    return pipe


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
        doc_extraction.graph_provider.batch_update_nodes.assert_awaited_once()
        call_args = doc_extraction.graph_provider.batch_update_nodes.call_args
        docs = call_args[0][0]
        assert docs[0]["indexingStatus"] == ProgressStatus.EMPTY.value
        assert docs[0]["isDirty"] is False
        assert docs[0]["extractionStatus"] == ProgressStatus.NOT_STARTED.value

        # Should NOT call document_extraction or sink index/enrich
        doc_extraction.apply.assert_not_awaited()
        sink_orchestrator.index.assert_not_awaited()
        sink_orchestrator.enrich.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_blocks_update_failure_logs_warning(self, pipeline, doc_extraction, sink_orchestrator):
        doc_extraction.graph_provider.batch_update_nodes = AsyncMock(return_value=False)
        record = _make_record(blocks=[], block_groups=[], record_id="rec-fail")
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        pipeline.logger.warning.assert_called()
        doc_extraction.apply.assert_not_awaited()
        sink_orchestrator.apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocks_none_does_not_take_empty_path(self, pipeline, doc_extraction, sink_orchestrator):
        """When blocks is None (not an empty list), the empty check should not trigger."""
        record = _make_record(blocks=None, block_groups=None)
        ctx = _make_ctx(record)
        ctx.reconciliation_context = ReconciliationContext(new_metadata={})

        await pipeline.apply(ctx)

        # Should go through the normal path since None != len==0
        sink_orchestrator.index.assert_awaited_once_with(ctx)
        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_block_containers_none_skips_validation(self, pipeline, doc_extraction, sink_orchestrator):
        """When block_containers is None, apply should not crash before extraction."""
        record = _make_record()
        record.block_containers = None
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)


# ---------------------------------------------------------------------------
# apply -- empty blocks, 1:1 update: reconciliation metadata document_path
# ---------------------------------------------------------------------------
class TestApplyEmptyMetadataPath:
    @pytest.mark.asyncio
    async def test_saves_metadata_at_content_actual_current_path(
        self, pipeline, doc_extraction, sink_orchestrator
    ):
        """document_path passed to save_reconciliation_metadata must come
        from content's actual current location, not be omitted (which would
        silently default metadata to the flat records/<vrid> form regardless
        of where content really lives)."""
        record = _make_record(blocks=[], block_groups=[], record_id="rec-1")
        record.virtual_record_id = "vr-1"
        record.org_id = "org-1"
        ctx = _make_ctx(record)
        ctx.event_type = EventTypes.UPDATE_RECORD.value
        ctx.prev_virtual_record_id = "vr-1"

        sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(
            return_value="records/conn-1/Finance/doc.pdf"
        )

        await pipeline.apply(ctx)

        sink_orchestrator.blob_storage.get_actual_content_path.assert_awaited_once_with(
            "org-1", "vr-1"
        )
        sink_orchestrator.blob_storage.save_reconciliation_metadata.assert_awaited_once()
        call_args = sink_orchestrator.blob_storage.save_reconciliation_metadata.call_args
        assert call_args.kwargs["document_path"] == "records/conn-1/Finance/doc.pdf"

    @pytest.mark.asyncio
    async def test_passes_none_path_when_no_existing_content(
        self, pipeline, doc_extraction, sink_orchestrator
    ):
        """When content doesn't exist yet (or the path can't be determined),
        document_path is passed as None -- save_reconciliation_metadata's own
        existing fallback (flat records/<vrid>) handles that safely."""
        record = _make_record(blocks=[], block_groups=[], record_id="rec-2")
        record.virtual_record_id = "vr-2"
        record.org_id = "org-1"
        ctx = _make_ctx(record)
        ctx.event_type = EventTypes.REINDEX_RECORD.value
        ctx.prev_virtual_record_id = "vr-2"

        sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)

        await pipeline.apply(ctx)

        call_args = sink_orchestrator.blob_storage.save_reconciliation_metadata.call_args
        assert call_args.kwargs["document_path"] is None


# ---------------------------------------------------------------------------
# apply -- non-empty blocks
# ---------------------------------------------------------------------------
class TestApplyNonEmpty:
    @pytest.mark.asyncio
    async def test_non_empty_calls_extraction_then_sink(self, pipeline, doc_extraction, sink_orchestrator):
        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_non_empty_block_groups_calls_extraction_then_sink(self, pipeline, doc_extraction, sink_orchestrator):
        record = _make_record(blocks=[], block_groups=[_valid_text_section_group()])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_both_blocks_and_groups_calls_extraction_then_sink(self, pipeline, doc_extraction, sink_orchestrator):
        record = _make_record(
            blocks=[_valid_text_block()],
            block_groups=[_valid_text_section_group()],
        )
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_index_called_before_enrich(self, pipeline, doc_extraction, sink_orchestrator):
        """Verify ordering: index runs before extraction and enrich."""
        call_order = []

        async def track_index(ctx):
            call_order.append("index")

        async def track_extraction(ctx):
            call_order.append("extraction")

        async def track_enrich(ctx):
            call_order.append("enrich")

        sink_orchestrator.index = track_index
        doc_extraction.apply = track_extraction
        sink_orchestrator.enrich = track_enrich

        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        assert call_order == ["index", "extraction", "enrich"]

    @pytest.mark.asyncio
    async def test_exception_in_extraction_propagates(self, pipeline, doc_extraction, sink_orchestrator):
        doc_extraction.apply = AsyncMock(side_effect=RuntimeError("extraction boom"))
        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        with pytest.raises(RuntimeError, match="extraction boom"):
            await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        sink_orchestrator.enrich.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_enrich_propagates(self, pipeline, doc_extraction, sink_orchestrator):
        sink_orchestrator.enrich = AsyncMock(side_effect=RuntimeError("enrich boom"))
        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        with pytest.raises(RuntimeError, match="enrich boom"):
            await pipeline.apply(ctx)
