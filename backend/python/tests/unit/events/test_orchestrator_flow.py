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
        graph_provider.update_node = AsyncMock(return_value=True)
        graph_provider.find_duplicate_records = AsyncMock(return_value=[])

    processor = processor or MagicMock()
    # sync_vector_membership awaits the pipeline; a bare MagicMock raises
    # TypeError, which is now propagated rather than swallowed.
    if not isinstance(getattr(processor, "indexing_pipeline", None), AsyncMock):
        processor.indexing_pipeline = AsyncMock()

    return EventProcessor(
        logger=logging.getLogger("test"),
        processor=processor,
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
    # Awaited by the membership sync on the duplicate-attach path.
    mock_processor.indexing_pipeline = AsyncMock()
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
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)  # no metadata returned

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

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
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(side_effect=RuntimeError("LLM down"))

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

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
    updates = [
        call.args[2]
        for call in ep.graph_provider.update_node.await_args_list
    ]
    assert any(
        update.get("extractionStatus") == "FAILED"
        for update in updates
    )


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true", "DEFER_EXTRACTION": "true"})
async def test_deferred_extraction_skips_extraction_client() -> None:
    """When DEFER_EXTRACTION=true the extraction service is not called inline."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock()  # should NOT be called

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

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
    updates = [
        call.args[2]
        for call in ep.graph_provider.update_node.await_args_list
    ]
    assert any(
        update.get("extractionStatus") == "NOT_STARTED"
        for update in updates
    )


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_statuses_track_active_parse_and_index_phases() -> None:
    """The active pipeline is visible through indexingStatus while
    parsingStatus reflects whether parsing itself is still running."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

    graph_provider = MagicMock()
    persisted_record = {
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
    }
    graph_provider.get_document = AsyncMock(
        side_effect=lambda *_args: dict(persisted_record)
    )
    graph_provider.get_departments = AsyncMock(return_value=[])
    graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    graph_provider.batch_update_nodes = AsyncMock(return_value=True)
    graph_provider.find_duplicate_records = AsyncMock(return_value=[])

    status_writes: list[dict] = []

    async def _capture_update(_record_id, _collection, fields):
        persisted_record.update(fields)
        status_writes.append({
            k: persisted_record.get(k)
            for k in ("parsingStatus", "indexingStatus", "extractionStatus")
        })
        return True

    graph_provider.update_node = AsyncMock(side_effect=_capture_update)

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        graph_provider=graph_provider,
    )

    gen = ep.on_event(_make_event_data())

    first = await gen.__anext__()
    assert first.event == IndexingEvent.START_PARSING
    assert {
        "parsingStatus": "IN_PROGRESS",
        "indexingStatus": "IN_PROGRESS",
        "extractionStatus": None,
    } in status_writes

    second = await gen.__anext__()
    assert second.event == IndexingEvent.PARSING_COMPLETE
    assert status_writes[-1]["parsingStatus"] == "COMPLETED"
    assert status_writes[-1]["indexingStatus"] == "IN_PROGRESS"

    third = await gen.__anext__()
    assert third.event == IndexingEvent.INDEXING_COMPLETE
    assert {
        "parsingStatus": "COMPLETED",
        "indexingStatus": "IN_PROGRESS",
        "extractionStatus": "IN_PROGRESS",
    } in status_writes

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_start_parsing_size_bytes_uses_utf8_length_for_str_content() -> None:
    """size_bytes on START_PARSING must be the actual byte length used for
    parsing admission cost, not a character count. For str content with
    multi-byte UTF-8 characters those two counts diverge, and undercounting
    would underprice XL-heavy admission cost (tiers.py XL_HEAVY_BYTES)."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
    )

    non_ascii_text = "日本語テキスト " * 100  # each char is 3 bytes in UTF-8
    event_data = _make_event_data()
    event_data["payload"]["extension"] = "svg"
    event_data["payload"]["mimeType"] = "image/svg+xml"
    event_data["payload"]["buffer"] = non_ascii_text

    gen = ep.on_event(event_data)
    first = await gen.__anext__()

    assert first.event == IndexingEvent.START_PARSING
    # file_content is stripped before this point (matching what's actually
    # encoded downstream for parsing), so compare against the stripped text.
    stripped = non_ascii_text.strip()
    assert first.data.size_bytes == len(stripped.encode("utf-8"))
    assert first.data.size_bytes != len(stripped)


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_duplicate_record_skips_service_pipeline() -> None:
    """Duplicate detection should short-circuit before reaching service pipeline."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
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
    graph_provider.update_node = AsyncMock(return_value=True)
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
# AppleDouble / OS-metadata sidecar guard (content-based, not filename-based)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_apple_double_sidecar_is_skipped_before_parsing() -> None:
    """A macOS AppleDouble sidecar (detected by magic bytes, not filename)
    must be marked FILE_TYPE_NOT_SUPPORTED and never reach the parser."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

    ep = _make_event_processor(
        parsing_client=parsing_client,
        sink_orchestrator=sink_orchestrator,
    )

    event_data = _make_event_data()
    event_data["payload"]["recordName"] = "._Report.pdf"
    event_data["payload"]["buffer"] = b"\x00\x05\x16\x07" + b"\x00" * 32

    events = []
    async for event in ep.on_event(event_data):
        events.append(event)

    parsing_client.parse.assert_not_awaited()
    sink_orchestrator.index.assert_not_awaited()

    event_types = [e.event for e in events]
    assert event_types == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]

    updates = [call.args[2] for call in ep.graph_provider.update_node.await_args_list]
    assert any(update.get("indexingStatus") == "FILE_TYPE_NOT_SUPPORTED" for update in updates)


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_apple_double_sidecar_does_not_complete_if_status_write_fails() -> None:
    """If persisting FILE_TYPE_NOT_SUPPORTED fails (update_node returns
    False, not an exception), on_event must raise instead of yielding
    PARSING_COMPLETE/INDEXING_COMPLETE — otherwise the message would be
    treated as handled while the record is stuck at its prior status with
    nothing left to ever revisit it."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(return_value={
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "._Report.pdf",
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
    graph_provider.get_departments = AsyncMock(return_value=[])
    graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    graph_provider.batch_update_nodes = AsyncMock(return_value=True)
    graph_provider.find_duplicate_records = AsyncMock(return_value=[])
    graph_provider.update_node = AsyncMock(return_value=False)  # simulate write failure

    ep = _make_event_processor(
        parsing_client=parsing_client,
        graph_provider=graph_provider,
    )

    event_data = _make_event_data()
    event_data["payload"]["recordName"] = "._Report.pdf"
    event_data["payload"]["buffer"] = b"\x00\x05\x16\x07" + b"\x00" * 32

    from app.exceptions.indexing_exceptions import IndexingError  # noqa: PLC0415

    events = []
    with pytest.raises(IndexingError):
        async for event in ep.on_event(event_data):
            events.append(event)

    # No completion event was yielded before the failed write was surfaced.
    event_types = [e.event for e in events]
    assert IndexingEvent.INDEXING_COMPLETE not in event_types
    parsing_client.parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_apple_double_sidecar_is_skipped_in_legacy_pipeline() -> None:
    """Same guard applies when USE_PARSING_SERVICE is unset (legacy dispatch) —
    the check runs before the service/legacy branch, so neither PDF handler
    should ever be invoked."""
    mock_processor = MagicMock()
    mock_processor.indexing_pipeline = AsyncMock()
    mock_processor.process_pdf_with_docling = AsyncMock(return_value=_noop_gen())
    mock_processor.process_pdf_document_with_ocr = AsyncMock(return_value=_noop_gen())

    ep = _make_event_processor(processor=mock_processor)

    event_data = _make_event_data()
    event_data["payload"]["recordName"] = "._Report.pdf"
    event_data["payload"]["buffer"] = b"\x00\x05\x16\x07" + b"\x00" * 32

    events = []
    async for event in ep.on_event(event_data):
        events.append(event)

    mock_processor.process_pdf_with_docling.assert_not_called()
    mock_processor.process_pdf_document_with_ocr.assert_not_called()

    event_types = [e.event for e in events]
    assert event_types == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_real_pdf_named_with_leading_dot_is_not_skipped() -> None:
    """A real PDF whose *filename* happens to start with '._' (not an actual
    AppleDouble sidecar) must still be parsed normally — the guard keys off
    content magic bytes, never off the filename convention."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
    )

    event_data = _make_event_data()
    event_data["payload"]["recordName"] = "._John_Carter_Report.pdf"
    event_data["payload"]["buffer"] = b"%PDF-1.7\n" + b"rest of a real pdf"

    events = []
    async for event in ep.on_event(event_data):
        events.append(event)

    parsing_client.parse.assert_awaited_once()
    sink_orchestrator.index.assert_awaited_once()

    updates = [call.args[2] for call in ep.graph_provider.update_node.await_args_list]
    assert not any(
        update.get("indexingStatus") == "FILE_TYPE_NOT_SUPPORTED" for update in updates
    )
