"""IndexingPipeline: resolution runs after extraction and before every write."""

from unittest.mock import AsyncMock, MagicMock

from app.models.blocks import SemanticMetadata
from app.modules.transformers.pipeline import IndexingPipeline


def _metadata() -> SemanticMetadata:
    return SemanticMetadata(summary="s", categories=["QA"], topics=["Bug bash"], languages=[], departments=[])


def _pipeline(order, metadata) -> tuple:
    doc_extraction = MagicMock()

    async def _apply(ctx) -> None:
        ctx.record.semantic_metadata = metadata
        order.append("extract")

    doc_extraction.apply = AsyncMock(side_effect=_apply)

    sink = MagicMock()
    sink.resolve_entities = AsyncMock(side_effect=lambda ctx: order.append("resolve"))
    sink.blob_storage.apply = AsyncMock(side_effect=lambda ctx: order.append("blob"))
    sink.vector_store.index_record_summary = AsyncMock(side_effect=lambda *a, **k: order.append("summary"))
    sink.enrich = AsyncMock(side_effect=lambda ctx: order.append("enrich"))
    return IndexingPipeline(doc_extraction, sink), sink


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.record.semantic_metadata = None
    ctx.record.id = "rec-1"
    ctx.record.virtual_record_id = "vr-1"
    ctx.record.org_id = "org-1"
    return ctx


async def test_resolution_precedes_blob_summary_and_graph_writes() -> None:
    order: list[str] = []
    pipeline, sink = _pipeline(order, _metadata())
    ctx = _ctx()
    await pipeline._enrich(ctx)
    assert order == ["extract", "resolve", "blob", "summary", "enrich"]
    sink.resolve_entities.assert_awaited_once_with(ctx)


async def test_no_metadata_skips_resolution_and_writes_but_still_enriches() -> None:
    order: list[str] = []
    pipeline, sink = _pipeline(order, None)
    await pipeline._enrich(_ctx())
    assert order == ["extract", "enrich"]
    sink.resolve_entities.assert_not_awaited()
