"""Orchestrator integration tests — full flow with mocked services.

Tests cover:
- Service pipeline enabled / disabled via env var
- Happy path (parse → index → enrich)
- Extraction failure (document still searchable)
- Deferred extraction (env var DEFER_EXTRACTION=true)
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlockType, BlocksContainer, DataFormat, SemanticMetadata
from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData
from app.services.parsing.interface import ParseResult, ParserProvider


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_empty_bc() -> BlocksContainer:
    return BlocksContainer(blocks=[], block_groups=[])


def _make_parse_result(*, empty: bool = False) -> ParseResult:
    if empty:
        block_container = _make_empty_bc()
    else:
        block = Block(index=0, type=BlockType.TEXT, data="hello", format=DataFormat.TXT)
        block_container = BlocksContainer(blocks=[block], block_groups=[])
    return ParseResult(
        block_container=block_container,
        provider_used=ParserProvider.DEFAULT,
        metadata={},
    )


def _make_event_processor(
    parsing_client=None,
    extraction_client=None,
    sink_orchestrator=None,
    transform_pipeline=None,
    graph_provider=None,
    processor=None,
):
    from app.events.events import EventProcessor  # noqa: PLC0415

    if graph_provider is None:
        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(return_value={
            "_key": "rec-1",
            "orgId": "org-1",
            "recordName": "test.pdf",
            "recordType": "FILE",
            "indexingStatus": "NOT_STARTED",
            "mimeType": "application/pdf",
            "connectorName": None,
            "origin": "UPLOAD",
            "externalRecordId": "ext-rec-1",
            "connectorId": "connector-1",
            "createdAtTimestamp": 1000000,
            "updatedAtTimestamp": 1000000,
        })
        graph_provider.get_departments = AsyncMock(return_value=["Engineering"])
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        graph_provider.batch_update_nodes = AsyncMock(return_value=True)
        graph_provider.find_duplicate_records = AsyncMock(return_value=[])

    return EventProcessor(
        logger=logging.getLogger("test"),
        processor=processor or MagicMock(),
        graph_provider=graph_provider,
        config_service=MagicMock(),
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
    )


def _make_event_data(record_id: str = "rec-1", org_id: str = "org-1") -> dict[str, Any]:
    return {
        "eventType": "NEW_RECORD",
        "payload": {
            "recordId": record_id,
            "orgId": org_id,
            "virtualRecordId": "vr-1",
            "extension": "pdf",
            "mimeType": "application/pdf",
            "recordName": "test.pdf",
            "buffer": b"%PDF-1.4",
            "version": 1,
            "connectorName": "",
        },
    }


# ---------------------------------------------------------------------------
# Service pipeline not active (env var not set)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_path_used_when_service_pipeline_disabled() -> None:
    """When USE_PARSING_SERVICE is not set, legacy processor is used."""
    mock_processor = MagicMock()
    mock_processor.process_pdf_with_docling = AsyncMock(return_value=_noop_gen())

    ep = _make_event_processor(processor=mock_processor)
    assert ep._use_service_pipeline() is False


def _noop_gen():
    """Sync wrapper that returns an empty async generator."""
    async def _gen():
        return
        yield  # noqa: unreachable
    return _gen()


# ---------------------------------------------------------------------------
# Service pipeline active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_full_pipeline_happy_path() -> None:
    """parse → index → enrich all succeed."""
    parsing_client = MagicMock()
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)  # no metadata returned

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()
    sink_orchestrator.blob_storage.apply = AsyncMock()

    transform_pipeline = MagicMock()
    transform_pipeline.build_reconciliation_context = AsyncMock(return_value=None)

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        transform_pipeline=transform_pipeline,
    )

    events = []
    async for event in ep.on_event(_make_event_data()):
        events.append(event)

    event_types = [e.event for e in events]
    assert IndexingEvent.PARSING_COMPLETE in event_types
    assert IndexingEvent.INDEXING_COMPLETE in event_types

    parsing_client.parse.assert_awaited_once()
    sink_orchestrator.index.assert_awaited_once()
    sink_orchestrator.enrich.assert_awaited_once()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_enrichment_failure_does_not_block_indexing() -> None:
    """When enrich raises, the INDEXING_COMPLETE event is still yielded."""
    parsing_client = MagicMock()
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(side_effect=RuntimeError("LLM down"))

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()
    sink_orchestrator.blob_storage.apply = AsyncMock()

    transform_pipeline = MagicMock()
    transform_pipeline.build_reconciliation_context = AsyncMock(return_value=None)

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        transform_pipeline=transform_pipeline,
    )

    events = []
    async for event in ep.on_event(_make_event_data()):
        events.append(event)

    # Document is still indexed
    sink_orchestrator.index.assert_awaited_once()
    # INDEXING_COMPLETE is still emitted even though extraction failed
    event_types = [e.event for e in events]
    assert IndexingEvent.INDEXING_COMPLETE in event_types


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true", "DEFER_EXTRACTION": "true"})
async def test_deferred_extraction_skips_extraction_client() -> None:
    """When DEFER_EXTRACTION=true the extraction service is not called inline."""
    parsing_client = MagicMock()
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock()  # should NOT be called

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()
    sink_orchestrator.blob_storage.apply = AsyncMock()

    transform_pipeline = MagicMock()
    transform_pipeline.build_reconciliation_context = AsyncMock(return_value=None)

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        transform_pipeline=transform_pipeline,
    )

    async for _ in ep.on_event(_make_event_data()):
        pass

    # Extraction client should not have been called
    extraction_client.classify.assert_not_awaited()
    # But indexing should have happened
    sink_orchestrator.index.assert_awaited_once()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_duplicate_record_skips_service_pipeline() -> None:
    """Duplicate detection should short-circuit before reaching service pipeline."""
    parsing_client = MagicMock()
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(return_value={
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "test.pdf",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "mimeType": "application/pdf",
        "connectorName": None,
        "origin": "UPLOAD",
        "md5Checksum": None,
        "sizeInBytes": None,
        "externalRecordId": "ext-rec-1",
        "connectorId": "connector-1",
        "createdAtTimestamp": 1000000,
        "updatedAtTimestamp": 1000000,
    })
    graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    graph_provider.batch_update_nodes = AsyncMock(return_value=True)
    graph_provider.find_duplicate_records = AsyncMock(return_value=[{
        "_key": "rec-2",
        "virtualRecordId": "vr-existing",
        "indexingStatus": "COMPLETED",
        "extractionStatus": "COMPLETED",
    }])
    graph_provider.copy_document_relationships = AsyncMock(return_value=True)
    graph_provider.get_departments = AsyncMock(return_value=[])

    ep = _make_event_processor(
        parsing_client=parsing_client,
        graph_provider=graph_provider,
    )

    events = []
    async for event in ep.on_event(_make_event_data()):
        events.append(event)

    # Duplicate found → parsing service should NOT be called
    parsing_client.parse.assert_not_awaited()
    # Events still yielded for status reporting
    assert len(events) >= 1


# ---------------------------------------------------------------------------
# Blob storage status update (post-enrichment)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_blob_storage_failure_does_not_block_indexing() -> None:
    """When blob_storage.apply raises, INDEXING_COMPLETE is still yielded and the
    error is logged rather than propagated."""
    parsing_client = MagicMock()
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()
    sink_orchestrator.blob_storage.apply = AsyncMock(side_effect=RuntimeError("blob write failed"))

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
    )

    events = []
    async for event in ep.on_event(_make_event_data()):
        events.append(event)

    # Blob storage failure must not crash the generator.
    sink_orchestrator.blob_storage.apply.assert_awaited_once()
    # Pipeline still completes and reports success despite the blob failure.
    event_types = [e.event for e in events]
    assert IndexingEvent.PARSING_COMPLETE in event_types
    assert IndexingEvent.INDEXING_COMPLETE in event_types
    # Document was still indexed and enriched before the blob failure.
    sink_orchestrator.index.assert_awaited_once()
    sink_orchestrator.enrich.assert_awaited_once()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_blob_storage_called_after_enrichment() -> None:
    """blob_storage.apply must run after the enrichment try/except block, so that
    a blob status update reflects the outcome of enrichment."""
    parsing_client = MagicMock()
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    call_order: list[str] = []

    async def _enrich_side_effect(_ctx: Any) -> None:
        call_order.append("enrich")

    async def _blob_apply_side_effect(_ctx: Any) -> None:
        call_order.append("blob_storage.apply")

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock(side_effect=_enrich_side_effect)
    sink_orchestrator.blob_storage.apply = AsyncMock(side_effect=_blob_apply_side_effect)

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
    )

    async for _ in ep.on_event(_make_event_data()):
        pass

    sink_orchestrator.enrich.assert_awaited_once()
    sink_orchestrator.blob_storage.apply.assert_awaited_once()
    assert call_order == ["enrich", "blob_storage.apply"]
