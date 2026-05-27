"""Tests for uncovered paths in SinkOrchestrator: the SQL row-limiting branch
and reconciliation-metadata save helper."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupSubType,
    GroupType,
    IndexRange,
)
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import (
    ReconciliationContext,
    TransformContext,
)


def _make_orchestrator(*, graph_doc=None, vector_result=None):
    graphdb = AsyncMock()
    blob_storage = AsyncMock()
    vector_store = AsyncMock()
    vector_store.apply = AsyncMock(return_value=vector_result)
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value=graph_doc)
    orch = SinkOrchestrator(
        graphdb=graphdb,
        blob_storage=blob_storage,
        vector_store=vector_store,
        graph_provider=graph_provider,
        logger=logging.getLogger("test-sink-sql"),
    )
    orch.logger = MagicMock()
    return orch


# ---------------------------------------------------------------------------
# _build_limited_sql_block_container
# ---------------------------------------------------------------------------


def _make_row_block(i):
    return Block(index=i, type=BlockType.TABLE_ROW, format="txt", data={"row": i})


class TestBuildLimitedSqlBlockContainer:
    def test_no_limit_when_row_count_below_threshold(self):
        orch = _make_orchestrator()
        blocks = [_make_row_block(i) for i in range(3)]
        container = BlocksContainer(blocks=blocks, block_groups=[])

        result = orch._build_limited_sql_block_container(container, limit=10)
        # When rows <= limit the container is returned unchanged
        assert result is container

    def test_truncates_rows_and_filters_block_group_children(self):
        orch = _make_orchestrator()
        row_blocks = [_make_row_block(i) for i in range(15)]
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=14)],
                block_group_ranges=[],
            ),
        )
        container = BlocksContainer(blocks=row_blocks, block_groups=[bg])

        result = orch._build_limited_sql_block_container(container, limit=5)

        # Only the first 5 row blocks are kept
        assert len(result.blocks) == 5
        # Their indices are the ones that were kept
        kept = {b.index for b in result.blocks}
        assert kept == {0, 1, 2, 3, 4}
        # The block group's children ranges should be rewritten to reflect truncation
        child_indices = []
        for r in result.block_groups[0].children.block_ranges:
            child_indices.extend(range(r.start, r.end + 1))
        assert set(child_indices) == kept

    def test_block_group_with_block_group_ranges_preserved(self):
        orch = _make_orchestrator()
        row_blocks = [_make_row_block(i) for i in range(12)]
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=11)],
                block_group_ranges=[IndexRange(start=0, end=2)],
            ),
        )
        container = BlocksContainer(blocks=row_blocks, block_groups=[bg])

        result = orch._build_limited_sql_block_container(container, limit=2)

        # The sub-block-group ranges should be preserved (though flattened)
        bg_indices = []
        for r in result.block_groups[0].children.block_group_ranges:
            bg_indices.extend(range(r.start, r.end + 1))
        assert set(bg_indices) == {0, 1, 2}


# ---------------------------------------------------------------------------
# apply — SQL branch that triggers the limiter
# ---------------------------------------------------------------------------


class TestApplySqlBranch:
    @pytest.mark.asyncio
    async def test_sql_group_triggers_row_limit_and_restores_container(self):
        """The full (un-limited) container must be restored after blob storage."""
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})

        row_blocks = [_make_row_block(i) for i in range(25)]
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=24)],
                block_group_ranges=[],
            ),
        )
        full_container = BlocksContainer(blocks=row_blocks, block_groups=[bg])

        record = MagicMock()
        record.id = "rec-sql"
        record.block_containers = full_container

        ctx = MagicMock(spec=TransformContext)
        ctx.record = record
        ctx.settings = {}

        async def capture_blob_apply(c):
            # At this point the container should be the limited version
            assert len(c.record.block_containers.blocks) == orch.LIMIT_SQL_ROW_BLOCKS_TO

        orch.blob_storage.apply = AsyncMock(side_effect=capture_blob_apply)

        await orch.apply(ctx)

        # After apply, the original (full) container is restored
        assert record.block_containers is full_container
        assert len(record.block_containers.blocks) == 25


# ---------------------------------------------------------------------------
# _save_reconciliation_metadata
# ---------------------------------------------------------------------------


class TestSaveReconciliationMetadata:
    @pytest.mark.asyncio
    async def test_saves_when_reconciliation_context_has_metadata(self):
        orch = _make_orchestrator()
        record = MagicMock()
        record.id = "rec-rc"
        record.org_id = "org-rc"
        record.virtual_record_id = "vr-rc"

        ctx = MagicMock(spec=TransformContext)
        ctx.record = record
        ctx.reconciliation_context = ReconciliationContext(
            new_metadata={"hash_to_block_ids": {"h": ["b"]}, "block_id_to_index": {"b": 0}}
        )

        await orch._save_reconciliation_metadata(ctx)

        orch.blob_storage.save_reconciliation_metadata.assert_awaited_once_with(
            "org-rc", "rec-rc", "vr-rc",
            ctx.reconciliation_context.new_metadata,
        )

    @pytest.mark.asyncio
    async def test_skips_when_reconciliation_context_missing(self):
        orch = _make_orchestrator()
        ctx = MagicMock(spec=TransformContext)
        ctx.reconciliation_context = None

        await orch._save_reconciliation_metadata(ctx)

        orch.blob_storage.save_reconciliation_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_new_metadata_empty(self):
        orch = _make_orchestrator()
        ctx = MagicMock(spec=TransformContext)
        ctx.reconciliation_context = ReconciliationContext(new_metadata={})
        ctx.record = MagicMock()

        await orch._save_reconciliation_metadata(ctx)

        orch.blob_storage.save_reconciliation_metadata.assert_not_awaited()


# ---------------------------------------------------------------------------
# apply — ensure reconciliation metadata is persisted after successful indexing
# ---------------------------------------------------------------------------


class TestApplyPersistsReconciliation:
    @pytest.mark.asyncio
    async def test_not_completed_path_saves_reconciliation_metadata(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "IN_PROGRESS"}, vector_result=True
        )

        record = MagicMock()
        record.id = "rec-x"
        record.org_id = "org-x"
        record.virtual_record_id = "vr-x"
        record.block_containers = BlocksContainer(blocks=[], block_groups=[])

        ctx = MagicMock(spec=TransformContext)
        ctx.record = record
        ctx.settings = {}
        ctx.reconciliation_context = ReconciliationContext(
            new_metadata={"hash_to_block_ids": {}, "block_id_to_index": {}}
        )

        await orch.apply(ctx)

        orch.blob_storage.save_reconciliation_metadata.assert_awaited_once()
