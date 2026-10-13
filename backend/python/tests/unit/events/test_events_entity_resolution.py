"""Service-path enrichment: entity resolution runs right after classification."""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import SemanticMetadata
from app.utils import indexing_timing as timing
from unit.events.test_orchestrator_flow import (
    _make_event_data,
    _make_event_processor,
    _make_parse_result,
)


def _metadata() -> SemanticMetadata:
    return SemanticMetadata(
        summary="A summary", categories=["QA"], topics=["Bug bash"], languages=["en"], departments=[],
    )


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_resolution_runs_before_summary_enrich_and_blob() -> None:
    order: list[str] = []
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=_metadata())

    sink = MagicMock()
    sink.index = AsyncMock()
    sink.resolve_entities = AsyncMock(side_effect=lambda ctx: order.append("resolve"))
    sink.vector_store.index_record_summary = AsyncMock(side_effect=lambda *a, **k: order.append("summary"))
    sink.blob_storage.apply = AsyncMock(side_effect=lambda ctx: order.append("blob"))
    sink.enrich = AsyncMock(side_effect=lambda ctx: order.append("enrich"))
    sink.extract_named_entities = AsyncMock(return_value=None)
    sink.persist_named_entities = AsyncMock(side_effect=lambda ctx, extraction: order.append("entities"))

    transform_pipeline = MagicMock()
    transform_pipeline.build_reconciliation_context = AsyncMock(return_value=None)

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink,
        transform_pipeline=transform_pipeline,
    )
    async for _event in ep.on_event(_make_event_data()):
        pass

    # The blob rewrite follows enrichment so the stored record carries its output.
    assert order == ["resolve", "summary", "enrich", "entities", "blob"]
    ctx = sink.resolve_entities.await_args.args[0]
    assert ctx.record.semantic_metadata is extraction_client.classify.return_value


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_no_metadata_means_no_resolution_call() -> None:
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())
    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)
    sink = MagicMock()
    sink.index = AsyncMock()
    sink.resolve_entities = AsyncMock()
    sink.enrich = AsyncMock()
    sink.extract_named_entities = AsyncMock(return_value=None)
    sink.persist_named_entities = AsyncMock()
    transform_pipeline = MagicMock()
    transform_pipeline.build_reconciliation_context = AsyncMock(return_value=None)

    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=extraction_client,
        sink_orchestrator=sink, transform_pipeline=transform_pipeline,
    )
    async for _event in ep.on_event(_make_event_data()):
        pass
    sink.resolve_entities.assert_not_awaited()
    sink.enrich.assert_awaited_once()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_summary_embedding_time_is_counted_once() -> None:
    """index_record_summary times its own embed and upsert steps, so a stage
    wrapped around the whole call would count that time twice."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())
    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=_metadata())

    async def embed_summary(*_args, **_kwargs) -> None:
        with timing.stage("embed.dense"):
            await asyncio.sleep(0.05)

    sink = MagicMock()
    sink.index = AsyncMock()
    sink.resolve_entities = AsyncMock()
    sink.vector_store.index_record_summary = AsyncMock(side_effect=embed_summary)
    sink.blob_storage.apply = AsyncMock()
    sink.enrich = AsyncMock()
    sink.extract_named_entities = AsyncMock(return_value=None)
    sink.persist_named_entities = AsyncMock()
    transform_pipeline = MagicMock()
    transform_pipeline.build_reconciliation_context = AsyncMock(return_value=None)

    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=extraction_client,
        sink_orchestrator=sink, transform_pipeline=transform_pipeline,
    )
    timeline, token = timing.begin_record("rec-1")
    started = time.perf_counter()
    try:
        async for _event in ep.on_event(_make_event_data()):
            pass
    finally:
        wall = time.perf_counter() - started
        timing.end_record(timeline, token, outcome="ok")

    sink.vector_store.index_record_summary.assert_awaited_once()
    # Every step here runs one after another, so busy time cannot exceed
    # the wall time of the call.
    busy = sum(total for _count, total, _max in timeline.stage_totals().values())
    assert busy <= wall
