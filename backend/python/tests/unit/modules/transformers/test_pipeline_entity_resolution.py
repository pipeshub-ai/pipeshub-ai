"""Entity resolution runs in the classify stage, after classification and before every
write; the record path (IndexingPipeline) never classifies or resolves inline.

The ordering itself is asserted at the stage: tests/unit/events/test_events_entity_resolution.py
and tests/unit/modules/pipeline/test_classify_io_writes.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.indexing_exceptions import EntityLookupUnavailableError
from app.models.blocks import Block, BlockType, DataFormat, SemanticMetadata
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.sink_orchestrator import SinkOrchestrator


def _metadata() -> SemanticMetadata:
    return SemanticMetadata(summary="s", categories=["QA"], topics=["Bug bash"], languages=[], departments=[])


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.settings = {}
    ctx.reconciliation_context = MagicMock()
    ctx.record.semantic_metadata = None
    ctx.record.id = "rec-1"
    ctx.record.virtual_record_id = "vr-1"
    ctx.record.org_id = "org-1"
    ctx.record.block_containers.blocks = [Block(index=0, type=BlockType.TEXT, data="x", format=DataFormat.TXT)]
    ctx.record.block_containers.block_groups = []
    return ctx


async def test_the_record_path_neither_classifies_nor_resolves() -> None:
    doc_extraction = MagicMock(apply=AsyncMock())
    sink = MagicMock(index=AsyncMock(), resolve_entities=AsyncMock(), prepare_enrichment=AsyncMock())
    ingress = MagicMock(on_indexed=AsyncMock(return_value=[]))

    await IndexingPipeline(doc_extraction, sink, stage_ingress=ingress).apply(_ctx())

    doc_extraction.apply.assert_not_awaited()
    sink.resolve_entities.assert_not_awaited()
    sink.prepare_enrichment.assert_not_awaited()
    ingress.on_indexed.assert_awaited_once()


def _sink() -> SinkOrchestrator:
    sink = SinkOrchestrator.__new__(SinkOrchestrator)
    sink.logger = MagicMock()
    return sink


async def test_resolution_runs_on_the_classification() -> None:
    sink = _sink()
    sink.entity_resolver = MagicMock(resolve=AsyncMock())
    ctx = _ctx()
    ctx.record.semantic_metadata = _metadata()
    await sink.prepare_enrichment(ctx)
    sink.entity_resolver.resolve.assert_awaited_once_with(ctx)


async def test_no_metadata_skips_resolution() -> None:
    sink = _sink()
    sink.entity_resolver = MagicMock(resolve=AsyncMock())
    await sink.prepare_enrichment(_ctx())
    sink.entity_resolver.resolve.assert_not_awaited()


async def test_a_deferred_resolution_reaches_the_stage_before_any_write() -> None:
    sink = _sink()
    sink.entity_resolver = MagicMock(resolve=AsyncMock(side_effect=EntityLookupUnavailableError("lookup could not run")))
    ctx = _ctx()
    ctx.record.semantic_metadata = _metadata()
    with pytest.raises(EntityLookupUnavailableError):
        await sink.prepare_enrichment(ctx)
