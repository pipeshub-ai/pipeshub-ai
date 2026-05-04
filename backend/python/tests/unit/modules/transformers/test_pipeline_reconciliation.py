"""Unit tests for IndexingPipeline._build_reconciliation_context and other
uncovered branches in app.modules.transformers.pipeline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    GroupType,
)
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import (
    ReconciliationContext,
    TransformContext,
)


def _record(
    *,
    virtual_record_id="vr-1",
    org_id="org-1",
    record_id="rec-1",
    blocks=None,
    block_groups=None,
):
    rec = MagicMock()
    rec.id = record_id
    rec.virtual_record_id = virtual_record_id
    rec.org_id = org_id
    rec.block_containers = BlocksContainer(
        blocks=blocks or [],
        block_groups=block_groups or [],
    )
    return rec


def _ctx(record, *, event_type=None, prev_vrid=None, reconciliation_context=None):
    ctx = MagicMock(spec=TransformContext)
    ctx.record = record
    ctx.event_type = event_type
    ctx.prev_virtual_record_id = prev_vrid
    ctx.reconciliation_context = reconciliation_context
    return ctx


def _pipeline(blob_meta=None):
    doc_extraction = AsyncMock()
    doc_extraction.graph_provider = AsyncMock()
    doc_extraction.graph_provider.get_document = AsyncMock(return_value={})
    doc_extraction.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

    sink = AsyncMock()
    sink.blob_storage = AsyncMock()
    sink.blob_storage.get_reconciliation_metadata = AsyncMock(return_value=blob_meta)
    sink.blob_storage.save_reconciliation_metadata = AsyncMock()
    sink.vector_store = AsyncMock()
    sink.vector_store.delete_embeddings = AsyncMock()

    return IndexingPipeline(doc_extraction, sink)


class TestBuildReconciliationContextEmpty:
    @pytest.mark.asyncio
    async def test_returns_none_when_block_containers_missing(self):
        pipeline = _pipeline()
        # record.block_containers is falsy -> returns None
        record = MagicMock()
        record.block_containers = None
        ctx = _ctx(record)

        result = await pipeline._build_reconciliation_context(ctx)
        assert result is None


class TestBuildReconciliationContextNoReconciliation:
    """When no diff work should happen, returns a ReconciliationContext with just new_metadata."""

    @pytest.mark.asyncio
    async def test_non_update_event_returns_new_metadata_only(self):
        pipeline = _pipeline()
        block = Block(index=0, type=BlockType.TEXT, data="hello")
        record = _record(blocks=[block])
        ctx = _ctx(record, event_type="newRecord")

        result = await pipeline._build_reconciliation_context(ctx)

        assert isinstance(result, ReconciliationContext)
        assert "hash_to_block_ids" in result.new_metadata
        assert result.blocks_to_index_ids is None
        assert result.block_ids_to_delete is None

    @pytest.mark.asyncio
    async def test_update_event_no_prev_vrid_returns_new_metadata_only(self):
        pipeline = _pipeline()
        block = Block(index=0, type=BlockType.TEXT, data="hi")
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid=None,
        )

        result = await pipeline._build_reconciliation_context(ctx)
        assert isinstance(result, ReconciliationContext)
        # get_reconciliation_metadata must not be called when prev_vrid is missing
        pipeline.sink_orchestrator.blob_storage.get_reconciliation_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_n_to_1_branch_index_all_blocks(self):
        pipeline = _pipeline()
        block = Block(index=0, type=BlockType.TEXT, data="hi")
        record = _record(blocks=[block], virtual_record_id="new-vr")
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="old-vr",
        )

        result = await pipeline._build_reconciliation_context(ctx)
        assert isinstance(result, ReconciliationContext)
        pipeline.sink_orchestrator.blob_storage.get_reconciliation_metadata.assert_not_awaited()


class TestBuildReconciliationContext1to1:
    @pytest.mark.asyncio
    async def test_no_old_metadata_purges_stale_vectors_and_returns_new_metadata_only(self):
        """First reconciliation pass (record indexed before recon was enabled):
        there is no prior metadata, so stale vectors must be purged before full reindex
        to avoid leaving orphans in the vector store."""
        pipeline = _pipeline(blob_meta=None)  # no prior metadata
        block = Block(index=0, type=BlockType.TEXT, data="hi")
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="vr-1",
        )

        result = await pipeline._build_reconciliation_context(ctx)
        assert isinstance(result, ReconciliationContext)
        # Fall-through context: no diff results
        assert result.blocks_to_index_ids is None
        assert result.block_ids_to_delete is None
        pipeline.sink_orchestrator.blob_storage.get_reconciliation_metadata.assert_awaited_once()
        # Stale vectors must be purged on first reconciliation pass
        pipeline.sink_orchestrator.vector_store.delete_embeddings.assert_awaited_once_with(
            record.virtual_record_id
        )

    @pytest.mark.asyncio
    async def test_no_old_metadata_delete_failure_is_swallowed(self):
        """If the stale-vector purge fails, the context must still be returned
        (best-effort cleanup — worst case falls back to the old orphan behavior)."""
        pipeline = _pipeline(blob_meta=None)
        pipeline.sink_orchestrator.vector_store.delete_embeddings = AsyncMock(
            side_effect=RuntimeError("qdrant down")
        )
        block = Block(index=0, type=BlockType.TEXT, data="hi")
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="vr-1",
        )

        # Must not raise
        result = await pipeline._build_reconciliation_context(ctx)
        assert isinstance(result, ReconciliationContext)
        assert result.blocks_to_index_ids is None
        assert result.block_ids_to_delete is None
        pipeline.sink_orchestrator.vector_store.delete_embeddings.assert_awaited_once_with(
            record.virtual_record_id
        )

    @pytest.mark.asyncio
    async def test_n_to_1_branch_does_not_purge_vectors(self):
        """N:1 case must NOT call delete_embeddings — old vrid's vectors may still
        serve other records that share it."""
        pipeline = _pipeline()
        block = Block(index=0, type=BlockType.TEXT, data="hi")
        record = _record(blocks=[block], virtual_record_id="new-vr")
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="old-vr",
        )

        await pipeline._build_reconciliation_context(ctx)
        pipeline.sink_orchestrator.vector_store.delete_embeddings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_diff_path_does_not_purge_vectors(self):
        """When diff runs successfully, delete_embeddings must not be called —
        the diff handles partial deletion via block_ids_to_delete instead."""
        old_metadata = {
            "hash_to_block_ids": {"old-hash": ["old-block-id"]},
            "block_id_to_index": {"old-block-id": 0},
        }
        pipeline = _pipeline(blob_meta=old_metadata)
        block = Block(index=0, type=BlockType.TEXT, data="fresh content")
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="vr-1",
        )

        await pipeline._build_reconciliation_context(ctx)
        pipeline.sink_orchestrator.vector_store.delete_embeddings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_diff_with_changes_produces_index_and_delete_ids(self):
        # Build an "old" metadata dict referencing a hash that no longer exists
        old_metadata = {
            "hash_to_block_ids": {"old-hash": ["old-block-id"]},
            "block_id_to_index": {"old-block-id": 0},
        }
        pipeline = _pipeline(blob_meta=old_metadata)
        block = Block(index=0, type=BlockType.TEXT, data="fresh content")
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="vr-1",
        )

        result = await pipeline._build_reconciliation_context(ctx)

        assert isinstance(result, ReconciliationContext)
        # A new block exists, so something should be indexed
        assert result.blocks_to_index_ids is not None
        # The old block-id no longer exists, so it should be marked for deletion
        assert result.block_ids_to_delete == {"old-block-id"}

    @pytest.mark.asyncio
    async def test_unchanged_blocks_have_ids_preserved(self):
        """When old and new share a content hash, old IDs must be preserved."""
        import hashlib
        import json as _json

        block = Block(index=0, type=BlockType.TEXT, data="stable-text")
        # Reuse the same hashing formula that build_metadata applies
        payload = _json.dumps(block.data, sort_keys=True).encode("utf-8")
        content_hash = (
            hashlib.sha256(payload).hexdigest()
            + ":"
            + hashlib.md5(payload).hexdigest()
        )

        old_metadata = {
            "hash_to_block_ids": {content_hash: ["old-id-xyz"]},
            "block_id_to_index": {"old-id-xyz": 0},
        }
        pipeline = _pipeline(blob_meta=old_metadata)
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid="vr-1",
        )

        result = await pipeline._build_reconciliation_context(ctx)

        # The new block should have taken over the old id
        assert block.id == "old-id-xyz"
        assert result.block_ids_to_delete == set()
        assert result.blocks_to_index_ids == set()

    @pytest.mark.asyncio
    async def test_reindex_event_uses_diff_path(self):
        old_metadata = {"hash_to_block_ids": {}, "block_id_to_index": {}}
        pipeline = _pipeline(blob_meta=old_metadata)
        block = Block(index=0, type=BlockType.TEXT, data="a")
        record = _record(blocks=[block])
        ctx = _ctx(
            record,
            event_type=EventTypes.REINDEX_RECORD.value,
            prev_vrid="vr-1",
        )

        result = await pipeline._build_reconciliation_context(ctx)
        assert isinstance(result, ReconciliationContext)
        pipeline.sink_orchestrator.blob_storage.get_reconciliation_metadata.assert_awaited_once()


class TestApplyEmptyCleanup:
    """Covers the 1:1 update cleanup branch for empty block containers."""

    @pytest.mark.asyncio
    async def test_empty_update_with_same_vrid_cleans_vectors_and_saves_metadata(self):
        pipeline = _pipeline()
        record = _record(blocks=[], block_groups=[])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid=record.virtual_record_id,
        )

        await pipeline.apply(ctx)

        pipeline.sink_orchestrator.vector_store.delete_embeddings.assert_awaited_once_with(
            record.virtual_record_id
        )
        pipeline.sink_orchestrator.blob_storage.save_reconciliation_metadata.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_update_cleanup_exception_is_swallowed(self):
        pipeline = _pipeline()
        pipeline.sink_orchestrator.vector_store.delete_embeddings = AsyncMock(
            side_effect=RuntimeError("delete boom")
        )
        record = _record(blocks=[], block_groups=[])
        ctx = _ctx(
            record,
            event_type=EventTypes.UPDATE_RECORD.value,
            prev_vrid=record.virtual_record_id,
        )

        # Should NOT raise -- the cleanup is best-effort.
        await pipeline.apply(ctx)

        # The regular "mark EMPTY" batch upsert still runs
        pipeline.document_extraction.graph_provider.batch_upsert_nodes.assert_awaited_once()


class TestApplyReconciliationContextBuildsWhenNone:
    """Line 128-129: when ctx.reconciliation_context is None, it is built."""

    @pytest.mark.asyncio
    async def test_reconciliation_context_built_when_none(self):
        pipeline = _pipeline()
        block = Block(index=0, type=BlockType.TEXT, data="hello")
        record = _record(blocks=[block])
        ctx = _ctx(record, event_type="newRecord", reconciliation_context=None)

        await pipeline.apply(ctx)

        # Reconciliation context should now be populated
        assert ctx.reconciliation_context is not None
        assert isinstance(ctx.reconciliation_context, ReconciliationContext)
        pipeline.document_extraction.apply.assert_awaited_once_with(ctx)
        pipeline.sink_orchestrator.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_existing_reconciliation_context_not_rebuilt(self):
        pipeline = _pipeline()
        existing = ReconciliationContext(new_metadata={"keep": "me"})
        block = Block(index=0, type=BlockType.TEXT, data="hello")
        record = _record(blocks=[block])
        ctx = _ctx(record, event_type="newRecord", reconciliation_context=existing)

        await pipeline.apply(ctx)

        assert ctx.reconciliation_context is existing
