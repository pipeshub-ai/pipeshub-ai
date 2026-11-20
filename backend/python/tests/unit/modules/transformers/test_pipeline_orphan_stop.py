"""``IndexingPipeline.apply`` checks that the record is still there before it embeds and writes (QA-G32B-4, M10).

Nothing of a record deleted while its run was in flight reaches the sink (blob write, embeddings, vector upsert) or the
stages, and nothing is written to the record. The same guard on a live record lets the run through.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.transformers.pipeline import IndexingPipeline
from app.services.paid_cache.orphan import (
    DeliveryGuard,
    ProbeResult,
    RecordGone,
    bind_guard,
)
from tests.unit.modules.transformers.test_pipeline import (
    _make_ctx,
    _make_record,
    _valid_text_block,
)


def _pipeline() -> tuple[IndexingPipeline, AsyncMock, MagicMock]:
    extraction = AsyncMock()
    extraction.graph_provider = AsyncMock()
    sink = AsyncMock()
    sink.blob_storage = MagicMock()
    sink.vector_store = MagicMock()
    ingress = MagicMock()
    ingress.on_indexed = AsyncMock(return_value=[])
    pipe = IndexingPipeline(extraction, sink, stage_ingress=ingress)
    pipe.logger = MagicMock()
    return pipe, sink, ingress


async def _bind(*, gone: bool) -> DeliveryGuard:
    async def probe() -> ProbeResult:
        return ProbeResult(found=False) if gone else ProbeResult(True, True, None)

    guard = DeliveryGuard(org_id="org-1", record_id="rec-123", probe=probe, confirm_first=True)
    bind_guard(guard)
    return guard


@pytest.mark.parametrize("blocks", [[_valid_text_block()], []])
async def test_a_record_deleted_before_the_embed_stage_reaches_neither_the_sink_nor_the_stages(blocks: list[Any]) -> None:
    pipe, sink, ingress = _pipeline()
    guard = await _bind(gone=True)
    try:
        with pytest.raises(RecordGone):
            await pipe.apply(_make_ctx(_make_record(blocks=blocks, block_groups=[])))
    finally:
        guard.finish()
    sink.index.assert_not_awaited()
    ingress.on_indexed.assert_not_awaited()
    pipe.document_extraction.graph_provider.update_node.assert_not_awaited()


async def test_a_live_record_goes_through_the_same_check() -> None:
    pipe, sink, ingress = _pipeline()
    guard = await _bind(gone=False)
    try:
        await pipe.apply(_make_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[])))
    finally:
        guard.finish()
    sink.index.assert_awaited_once()
    ingress.on_indexed.assert_awaited_once()
