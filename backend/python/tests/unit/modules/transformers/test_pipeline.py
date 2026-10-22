"""Unit tests for app.modules.transformers.pipeline.IndexingPipeline.

The pipeline validates and indexes a record, then hands the searchable record to the
stage runtime; classification is a pipeline stage and never runs inline.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import (
    ENRICHMENT_FOLLOWS,
    ReconciliationContext,
)

_SENTINEL = object()


def _valid_text_block(index: int = 0) -> Block:
    return Block(index=index, type=BlockType.TEXT, data="sample text", format=DataFormat.TXT)


def _valid_text_section_group(index: int = 0) -> BlockGroup:
    return BlockGroup(index=index, type=GroupType.TEXT_SECTION)


def _make_record(blocks=_SENTINEL, block_groups=_SENTINEL, record_id="rec-123"):
    """A mock Record; blocks and block_groups default to empty lists, None is kept as None."""
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


def _make_ctx(record, event_type=None):
    ctx = MagicMock()
    ctx.record = record
    ctx.settings = {}
    ctx.event_type = event_type
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
def stage_ingress() -> MagicMock:
    ingress = MagicMock()
    ingress.on_indexed = AsyncMock(return_value=["vrid-1:rev:classify@1"])
    return ingress


@pytest.fixture
def pipeline(doc_extraction, sink_orchestrator, stage_ingress):
    pipe = IndexingPipeline(doc_extraction, sink_orchestrator, stage_ingress=stage_ingress)
    pipe.logger = MagicMock()
    return pipe


# ---------------------------------------------------------------------------
# apply -- empty blocks and block_groups
# ---------------------------------------------------------------------------
class TestApplyEmpty:
    @pytest.mark.asyncio
    async def test_empty_blocks_marks_empty_and_returns(self, pipeline, doc_extraction, sink_orchestrator, stage_ingress) -> None:
        ctx = _make_ctx(_make_record(blocks=[], block_groups=[], record_id="rec-1"))

        await pipeline.apply(ctx)

        doc_extraction.graph_provider.update_node.assert_awaited_once()
        fields = doc_extraction.graph_provider.update_node.await_args.args[2]
        assert fields["indexingStatus"] == ProgressStatus.EMPTY.value
        assert fields["isDirty"] is False
        assert fields["extractionStatus"] == ProgressStatus.NOT_STARTED.value
        # The in-process path has no other write that ends the parse step.
        assert fields["parsingStatus"] == ProgressStatus.COMPLETED.value
        # An empty record has nothing left to serve from an earlier version.
        assert fields["hasIndexedCopy"] is False
        sink_orchestrator.index.assert_not_awaited()
        stage_ingress.on_indexed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_blocks_update_failure_logs_and_returns(
        self, pipeline, doc_extraction, sink_orchestrator, stage_ingress
    ) -> None:
        doc_extraction.graph_provider.update_node = AsyncMock(return_value=False)
        ctx = _make_ctx(_make_record(blocks=[], block_groups=[], record_id="rec-fail"))

        await pipeline.apply(ctx)

        pipeline.logger.warning.assert_called()
        assert "Failed to update indexing status" in pipeline.logger.warning.call_args.args[0]
        sink_orchestrator.index.assert_not_awaited()
        stage_ingress.on_indexed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocks_none_does_not_take_empty_path(self, pipeline, sink_orchestrator, stage_ingress) -> None:
        """None is not an empty list: the record takes the normal path."""
        ctx = _make_ctx(_make_record(blocks=None, block_groups=None))
        ctx.reconciliation_context = ReconciliationContext(new_metadata={})

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        stage_ingress.on_indexed.assert_awaited_once_with(ctx.record, trigger=None)

    @pytest.mark.asyncio
    async def test_block_containers_none_skips_validation(self, pipeline, sink_orchestrator, stage_ingress) -> None:
        record = _make_record()
        record.block_containers = None
        ctx = _make_ctx(record)

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        stage_ingress.on_indexed.assert_awaited_once()


# ---------------------------------------------------------------------------
# apply -- non-empty records are indexed, then handed to the stage runtime
# ---------------------------------------------------------------------------
class TestApplyNonEmpty:
    @pytest.mark.parametrize(
        ("blocks", "groups"),
        [
            ([_valid_text_block()], []),
            ([], [_valid_text_section_group()]),
            ([_valid_text_block()], [_valid_text_section_group()]),
        ],
    )
    @pytest.mark.asyncio
    async def test_indexes_then_hands_off(self, pipeline, sink_orchestrator, stage_ingress, blocks, groups) -> None:
        ctx = _make_ctx(_make_record(blocks=blocks, block_groups=groups))

        await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        stage_ingress.on_indexed.assert_awaited_once_with(ctx.record, trigger=None)

    @pytest.mark.asyncio
    async def test_classification_never_runs_inline(self, pipeline, doc_extraction, sink_orchestrator) -> None:
        ctx = _make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[]))

        await pipeline.apply(ctx)

        doc_extraction.apply.assert_not_awaited()
        sink_orchestrator.write_blob.assert_not_awaited()
        sink_orchestrator.vector_store.index_record_summary.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_index_runs_before_the_hand_off(self, pipeline, sink_orchestrator, stage_ingress) -> None:
        call_order = []

        async def track_index(ctx) -> None:
            call_order.append("index")

        async def track_hand_off(record, *, trigger) -> list[str]:
            call_order.append("hand-off")
            return []

        sink_orchestrator.index = track_index
        stage_ingress.on_indexed = track_hand_off
        await pipeline.apply(_make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[])))

        assert call_order == ["index", "hand-off"]

    @pytest.mark.asyncio
    async def test_the_hand_off_carries_the_event_type(self, pipeline, stage_ingress) -> None:
        ctx = _make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[]), event_type="reindexRecord")

        await pipeline.apply(ctx)

        assert stage_ingress.on_indexed.await_args.kwargs == {"trigger": "reindexRecord"}

    @pytest.mark.asyncio
    async def test_an_index_failure_propagates_and_hands_nothing_off(self, pipeline, sink_orchestrator, stage_ingress) -> None:
        sink_orchestrator.index = AsyncMock(side_effect=RuntimeError("index boom"))

        with pytest.raises(RuntimeError, match="index boom"):
            await pipeline.apply(_make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[])))

        stage_ingress.on_indexed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_hand_off_failure_propagates(self, pipeline, stage_ingress) -> None:
        stage_ingress.on_indexed = AsyncMock(side_effect=ConnectionError("graph unavailable"))

        with pytest.raises(ConnectionError, match="graph unavailable"):
            await pipeline.apply(_make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[])))

    @pytest.mark.asyncio
    async def test_without_a_stage_runtime_the_pipeline_refuses(self, doc_extraction, sink_orchestrator) -> None:
        pipe = IndexingPipeline(doc_extraction, sink_orchestrator)

        with pytest.raises(RuntimeError, match="stage runtime is not wired"):
            await pipe.apply(_make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[])))

        sink_orchestrator.index.assert_awaited_once()


# ---------------------------------------------------------------------------
# apply -- empty blocks, 1:1 update: reconciliation metadata document_path
# ---------------------------------------------------------------------------
class TestApplyEmptyMetadataPath:
    @pytest.mark.asyncio
    async def test_saves_metadata_at_content_actual_current_path(self, pipeline, sink_orchestrator) -> None:
        """document_path passed to save_reconciliation_metadata must come
        from content's actual current location, not be omitted."""
        record = _make_record(blocks=[], block_groups=[], record_id="rec-1")
        record.virtual_record_id = "vr-1"
        record.org_id = "org-1"
        ctx = _make_ctx(record, event_type=EventTypes.UPDATE_RECORD.value)
        ctx.prev_virtual_record_id = "vr-1"

        sink_orchestrator.vector_store.purge_record_vectors = AsyncMock()
        sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(
            return_value="records/conn-1/Finance/doc.pdf"
        )
        sink_orchestrator.blob_storage.save_reconciliation_metadata = AsyncMock()

        await pipeline.apply(ctx)

        sink_orchestrator.blob_storage.get_actual_content_path.assert_awaited_once_with("org-1", "vr-1")
        call_args = sink_orchestrator.blob_storage.save_reconciliation_metadata.call_args
        assert call_args.kwargs["document_path"] == "records/conn-1/Finance/doc.pdf"

    @pytest.mark.asyncio
    async def test_passes_none_path_when_no_existing_content(self, pipeline, sink_orchestrator) -> None:
        record = _make_record(blocks=[], block_groups=[], record_id="rec-2")
        record.virtual_record_id = "vr-2"
        record.org_id = "org-1"
        ctx = _make_ctx(record, event_type=EventTypes.REINDEX_RECORD.value)
        ctx.prev_virtual_record_id = "vr-2"

        sink_orchestrator.vector_store.purge_record_vectors = AsyncMock()
        sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)
        sink_orchestrator.blob_storage.save_reconciliation_metadata = AsyncMock()

        await pipeline.apply(ctx)

        call_args = sink_orchestrator.blob_storage.save_reconciliation_metadata.call_args
        assert call_args.kwargs["document_path"] is None


@pytest.mark.asyncio
async def test_the_index_phase_is_told_enrichment_follows(pipeline, sink_orchestrator) -> None:
    """The indexed write marks extraction QUEUED in the same write, so no reader sees the
    record indexed with an extractionStatus a duplicate would copy as final."""
    ctx = _make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[]))
    seen: dict = {}

    async def index(c) -> None:
        seen.update(c.settings)

    sink_orchestrator.index = AsyncMock(side_effect=index)

    await pipeline.apply(ctx)

    assert seen.get(ENRICHMENT_FOLLOWS) is True
