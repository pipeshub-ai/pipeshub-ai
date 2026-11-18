"""D-180: a user's Force reindex re-embeds every block; a plain reindex reconciles by diff and re-keys."""
from __future__ import annotations

import hashlib
import json

import pytest

from app.config.constants.arangodb import EventTypes
from app.models.blocks import Block, BlockType
from app.modules.transformers.pipeline import IndexingPipeline
from app.services.paid_cache.run import begin_run, clear_run
from tests.unit.modules.transformers.test_pipeline_reconciliation import _ctx, _pipeline, _record

ORG, REC = "org-1", "rec-1"


def _unchanged_block_and_metadata(old_id: str = "old-id-xyz") -> tuple[Block, dict]:
    block = Block(index=0, type=BlockType.TEXT, data="stable-text")
    payload = json.dumps(block.data, sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(payload).hexdigest() + ":" + hashlib.md5(payload).hexdigest()
    return block, {"hash_to_block_ids": {content_hash: [old_id]}, "block_id_to_index": {old_id: 0}}


@pytest.fixture(autouse=True)
def _no_run() -> None:
    clear_run()
    yield
    clear_run()


async def test_a_plain_reindex_keeps_an_unchanged_block_and_moves_a_rekeyed_one() -> None:
    block, old = _unchanged_block_and_metadata()
    pipeline = _pipeline(blob_meta=old)
    ctx = _ctx(_record(blocks=[block]), event_type=EventTypes.REINDEX_RECORD.value, prev_vrid="vr-1")

    result = await IndexingPipeline.build_reconciliation_context(ctx, pipeline.logger, pipeline.sink_orchestrator)

    assert result.rekey_ids == {"old-id-xyz": block.id}              # moved, not re-embedded
    assert result.blocks_to_index_ids == set()
    pipeline.sink_orchestrator.vector_store.purge_record_vectors.assert_not_awaited()


async def test_a_forced_reindex_purges_the_points_and_indexes_every_block_with_no_move() -> None:
    block, old = _unchanged_block_and_metadata()
    pipeline = _pipeline(blob_meta=old)
    begin_run(ORG, REC, None, "index", refresh=True)                    # what a user's Force reindex opens
    ctx = _ctx(_record(blocks=[block]), event_type=EventTypes.REINDEX_RECORD.value, prev_vrid="vr-1")

    result = await IndexingPipeline.build_reconciliation_context(ctx, pipeline.logger, pipeline.sink_orchestrator)

    pipeline.sink_orchestrator.vector_store.purge_record_vectors.assert_awaited_once()
    assert not result.rekey_ids                                         # no re-key or move
    assert not result.block_ids_to_delete
    assert not result.blocks_to_index_ids                               # no diff: the whole record is indexed
    pipeline.sink_orchestrator.blob_storage.get_reconciliation_metadata.assert_not_awaited()


async def test_a_forced_reindex_whose_purge_fails_fails_the_delivery_rather_than_double_indexing() -> None:
    block, old = _unchanged_block_and_metadata()
    pipeline = _pipeline(blob_meta=old)
    pipeline.sink_orchestrator.vector_store.purge_record_vectors.side_effect = RuntimeError("vector store down")
    begin_run(ORG, REC, None, "index", refresh=True)
    ctx = _ctx(_record(blocks=[block]), event_type=EventTypes.REINDEX_RECORD.value, prev_vrid="vr-1")

    with pytest.raises(RuntimeError):
        await IndexingPipeline.build_reconciliation_context(ctx, pipeline.logger, pipeline.sink_orchestrator)
