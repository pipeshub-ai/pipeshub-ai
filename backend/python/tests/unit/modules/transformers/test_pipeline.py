"""Unit tests for app.modules.transformers.pipeline.IndexingPipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockType,
    DataFormat,
    GroupType,
    SemanticMetadata,
)
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import ReconciliationContext, TransformContext

_SENTINEL = object()


def _valid_text_block(index: int = 0) -> Block:
    return Block(index=index, type=BlockType.TEXT, data="sample text", format=DataFormat.TXT)


def _valid_text_section_group(index: int = 0) -> BlockGroup:
    return BlockGroup(index=index, type=GroupType.TEXT_SECTION)


def _make_semantic_metadata(summary: str | None = "A concise summary") -> SemanticMetadata:
    return SemanticMetadata(
        summary=summary,
        departments=["Engineering"],
        languages=["en"],
        topics=["testing"],
        categories=["Software"],
        sub_category_level_1="Backend",
        sub_category_level_2="Indexing",
        sub_category_level_3="Pipeline",
    )


def _make_record(blocks=_SENTINEL, block_groups=_SENTINEL, record_id="rec-123"):
    """Create a mock Record with the given blocks/block_groups.

    By default (sentinel), blocks and block_groups are set to empty lists.
    Pass None explicitly to set them to None.
    """
    record = MagicMock()
    record.id = record_id
    record.org_id = "org-1"
    record.virtual_record_id = "vrid-1"
    record.semantic_metadata = None
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
    de.graph_provider.update_node = AsyncMock(return_value=True)
    return de


@pytest.fixture
def sink_orchestrator():
    sink = AsyncMock()
    sink.blob_storage = MagicMock()
    sink.blob_storage.apply = AsyncMock()
    sink.vector_store = MagicMock()
    sink.vector_store.index_record_summary = AsyncMock()
    return sink


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

        doc_extraction.graph_provider.update_node.assert_awaited_once()
        fields = doc_extraction.graph_provider.update_node.await_args.args[2]
        assert fields["indexingStatus"] == ProgressStatus.EMPTY.value
        assert fields["isDirty"] is False
        assert fields["extractionStatus"] == ProgressStatus.NOT_STARTED.value

        # Should NOT call document_extraction or sink index/enrich
        doc_extraction.apply.assert_not_awaited()
        sink_orchestrator.index.assert_not_awaited()
        sink_orchestrator.enrich.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_blocks_update_failure_raises(self, pipeline, doc_extraction, sink_orchestrator):
        doc_extraction.graph_provider.update_node = AsyncMock(return_value=False)
        record = _make_record(blocks=[], block_groups=[], record_id="rec-fail")
        ctx = _make_ctx(record)

        with pytest.raises(RuntimeError, match="Failed to persist EMPTY status"):
            await pipeline.apply(ctx)

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


# ---------------------------------------------------------------------------
# _enrich -- blob rewrite + summary indexing after extraction
# ---------------------------------------------------------------------------
class TestEnrich:
    @pytest.mark.asyncio
    async def test_enrich_with_summary_rewrites_blob_and_indexes_summary(
        self, pipeline, doc_extraction, sink_orchestrator
    ):
        metadata = _make_semantic_metadata(summary="Ticket summary text")

        async def set_metadata(ctx):
            ctx.record.semantic_metadata = metadata

        doc_extraction.apply = AsyncMock(side_effect=set_metadata)
        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline._enrich(ctx)

        doc_extraction.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.blob_storage.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.vector_store.index_record_summary.assert_awaited_once_with(
            "rec-123",
            "vrid-1",
            "org-1",
            metadata,
        )
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_enrich_with_empty_summary_rewrites_blob_skips_summary_vector(
        self, pipeline, doc_extraction, sink_orchestrator
    ):
        metadata = _make_semantic_metadata(summary="   ")

        async def set_metadata(ctx):
            ctx.record.semantic_metadata = metadata

        doc_extraction.apply = AsyncMock(side_effect=set_metadata)
        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline._enrich(ctx)

        sink_orchestrator.blob_storage.apply.assert_awaited_once_with(ctx)
        sink_orchestrator.vector_store.index_record_summary.assert_not_awaited()
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_enrich_without_semantic_metadata_skips_blob_and_summary(
        self, pipeline, doc_extraction, sink_orchestrator
    ):
        async def clear_metadata(ctx):
            ctx.record.semantic_metadata = None

        doc_extraction.apply = AsyncMock(side_effect=clear_metadata)
        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline._enrich(ctx)

        sink_orchestrator.blob_storage.apply.assert_not_awaited()
        sink_orchestrator.vector_store.index_record_summary.assert_not_awaited()
        sink_orchestrator.enrich.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_enrich_call_order_matches_service_path(
        self, pipeline, doc_extraction, sink_orchestrator
    ):
        """extraction → blob rewrite → summary vector → graph enrich."""
        metadata = _make_semantic_metadata(summary="Ordered summary")
        call_order = []

        async def track_extraction(ctx):
            call_order.append("extraction")
            ctx.record.semantic_metadata = metadata

        async def track_blob(ctx):
            call_order.append("blob")

        async def track_summary(*_args, **_kwargs):
            call_order.append("summary")

        async def track_enrich(ctx):
            call_order.append("enrich")

        doc_extraction.apply = AsyncMock(side_effect=track_extraction)
        sink_orchestrator.blob_storage.apply = AsyncMock(side_effect=track_blob)
        sink_orchestrator.vector_store.index_record_summary = AsyncMock(
            side_effect=track_summary
        )
        sink_orchestrator.enrich = AsyncMock(side_effect=track_enrich)

        record = _make_record(blocks=[_valid_text_block()], block_groups=[])
        ctx = _make_ctx(record)

        await pipeline._enrich(ctx)

        assert call_order == ["extraction", "blob", "summary", "enrich"]
