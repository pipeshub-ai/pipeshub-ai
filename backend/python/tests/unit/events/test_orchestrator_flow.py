"""Orchestrator integration tests — full flow with mocked services.

Tests cover:
- Service pipeline enabled / disabled via env var
- Happy path (parse → index → hand-off to the pipeline stages)
- Content revision and forced reindex scoping
"""
from __future__ import annotations

import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from app.exceptions.indexing_exceptions import RecordDeletedError
from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockType,
    DataFormat,
)
from app.modules.pipeline.fingerprint import content_revision
from app.services.messaging.config import (
    IndexingEvent,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    is_expected_outcome,
)
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


def _project_code_blocks_mock() -> AsyncMock:
    """Autospec'd: a caller passing a kwarg the real processor does not accept
    fails here instead of at runtime."""
    from app.events.processor import Processor

    return create_autospec(Processor, instance=True).project_code_blocks_to_graph


def _make_event_processor(
    parsing_client=None,
    extraction_client=None,
    sink_orchestrator=None,
    transform_pipeline=None,
    graph_provider=None,
    processor=None,
    stage_ingress=None,
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

    if stage_ingress is None:
        stage_ingress = MagicMock()
        stage_ingress.on_indexed = AsyncMock(return_value=[])

    processor = processor or MagicMock()
    # sync_vector_membership awaits the pipeline; a bare MagicMock raises
    # TypeError, which is now propagated rather than swallowed.
    if not isinstance(getattr(processor, "indexing_pipeline", None), AsyncMock):
        processor.indexing_pipeline = AsyncMock()
    # Dedup checks the twin's stored content before reusing it; default to present.
    blob_storage = processor.sink_orchestrator.blob_storage
    if not isinstance(getattr(blob_storage, "get_actual_content_path", None), AsyncMock):
        blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")

    return EventProcessor(
        logger=logging.getLogger("test"),
        processor=processor,
        graph_provider=graph_provider,
        config_service=MagicMock(),
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        stage_ingress=stage_ingress,
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


def _service_parsing_client() -> MagicMock:
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())
    return parsing_client


def _service_clients() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Parsing client, extraction client (never called inline now) and indexing sink."""
    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)
    return _service_parsing_client(), extraction_client, _indexing_sink()


def _indexing_sink() -> MagicMock:
    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    return sink_orchestrator


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_full_pipeline_happy_path() -> None:
    """parse → index → the searchable record is handed to the pipeline stages."""
    parsing_client, sink_orchestrator = _service_parsing_client(), _indexing_sink()
    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock()
    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=extraction_client, sink_orchestrator=sink_orchestrator
    )

    events = [event async for event in ep.on_event(_make_event_data())]

    event_types = [e.event for e in events]
    assert IndexingEvent.PARSING_COMPLETE in event_types
    assert IndexingEvent.INDEXING_COMPLETE in event_types
    parsing_client.parse.assert_awaited_once()
    sink_orchestrator.index.assert_awaited_once()
    # Classification is a pipeline stage: never inline, dispatched once indexing is done.
    extraction_client.classify.assert_not_awaited()
    ep.stage_ingress.on_indexed.assert_awaited_once()
    assert ep.stage_ingress.on_indexed.await_args.args[0].id == "rec-1"
    assert ep.stage_ingress.on_indexed.await_args.kwargs == {"trigger": "NEW_RECORD", "defer_classify": False}


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_failed_hand_off_fails_the_attempt() -> None:
    """A record that could not be handed to the stages is retried, never reported complete."""
    stage_ingress = MagicMock()
    stage_ingress.on_indexed = AsyncMock(side_effect=ConnectionError("graph unavailable"))
    ep = _make_event_processor(
        parsing_client=_service_parsing_client(),
        extraction_client=MagicMock(),
        sink_orchestrator=_indexing_sink(),
        stage_ingress=stage_ingress,
    )

    events = []
    with pytest.raises(ConnectionError):
        async for event in ep.on_event(_make_event_data()):
            events.append(event)  # noqa: PERF401 - keeps the events yielded before the raise

    assert IndexingEvent.INDEXING_COMPLETE not in [e.event for e in events]


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_the_content_revision_is_written_with_in_progress() -> None:
    ep = _make_event_processor(
        parsing_client=_service_parsing_client(), extraction_client=MagicMock(), sink_orchestrator=_indexing_sink()
    )

    async for _ in ep.on_event(_make_event_data()):
        pass

    updates = [call.args[2] for call in ep.graph_provider.update_node.await_args_list]
    assert any(
        update.get("indexingStatus") == "IN_PROGRESS" and update.get("contentRev") == content_revision(b"%PDF-1.4")
        for update in updates
    )


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_forced_reindex_is_scoped_to_its_event() -> None:
    stage_ingress = MagicMock()
    stage_ingress.on_indexed = AsyncMock(return_value=[])
    ep = _make_event_processor(
        parsing_client=_service_parsing_client(),
        extraction_client=MagicMock(),
        sink_orchestrator=_indexing_sink(),
        stage_ingress=stage_ingress,
    )
    event_data = _make_event_data()
    event_data["payload"]["forceReindex"] = True

    async for _ in ep.on_event(event_data):
        pass

    stage_ingress.mark_forced.assert_called_once_with("rec-1")
    stage_ingress.clear_forced.assert_called_once_with("rec-1")


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
        side_effect=lambda *_args, **_kwargs: dict(persisted_record)
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
    # The classify stage owns extractionStatus; indexing writes it only as QUEUED, in the
    # sink's indexed write (mocked here), so no reader sees it indexed with a final-looking one.
    assert all(write["extractionStatus"] is None for write in status_writes)
    (index_ctx,), _ = sink_orchestrator.index.await_args
    assert index_ctx.settings.get("enrichment_follows") is True

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


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_event_without_org_sends_the_stored_records_org() -> None:
    parsing_client = _service_parsing_client()
    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=_indexing_sink()
    )
    event_data = _make_event_data()
    del event_data["payload"]["orgId"]

    async for _ in ep.on_event(event_data):
        pass

    assert parsing_client.parse.await_args.kwargs["org_id"] == "org-1"
    # The classify stage reads the org from the record it is handed.
    assert ep.stage_ingress.on_indexed.await_args.args[0].org_id == "org-1"


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_record_with_no_org_anywhere_fails_for_good_without_calling_the_services() -> None:
    from app.exceptions.indexing_exceptions import ProcessingError  # noqa: PLC0415
    from app.services.messaging.error_classifier import (  # noqa: PLC0415
        MessageErrorClassifier,
        MessageErrorType,
    )

    parsing_client = _service_parsing_client()
    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=_indexing_sink()
    )
    del ep.graph_provider.get_document.return_value["orgId"]
    event_data = _make_event_data()
    del event_data["payload"]["orgId"]

    with pytest.raises(ProcessingError, match="has no organisation") as exc_info:
        async for _ in ep.on_event(event_data):
            pass

    assert MessageErrorClassifier.classify_by_exception(exc_info.value) == MessageErrorType.TERMINAL
    parsing_client.parse.assert_not_awaited()
    ep.stage_ingress.on_indexed.assert_not_awaited()
    updates = [call.args[2] for call in ep.graph_provider.update_node.await_args_list]
    assert not any("indexingStatus" in update for update in updates)


# ---------------------------------------------------------------------------
# Code-graph projection on the service pipeline
#
# The service pipeline is what deployments run (every compose file defaults
# USE_PARSING_SERVICE to true), so a code file that is not projected here is
# absent from the code graph entirely while still being searchable.
# ---------------------------------------------------------------------------


def _make_code_event() -> dict[str, Any]:
    event = _make_event_data()
    event["payload"].update({
        "extension": "py",
        "mimeType": "text/plain",
        "recordName": "client.py",
        "filePath": "src/client.py",
    })
    return event


def _service_pipeline_processor() -> tuple:
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()

    processor = MagicMock()
    processor.project_code_blocks_to_graph = _project_code_blocks_mock()

    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        processor=processor,
    )
    return ep, processor, parsing_client, sink_orchestrator


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_service_pipeline_projects_code_blocks() -> None:
    """A code file's blocks reach the graph under its own record id."""
    ep, processor, parsing_client, _ = _service_pipeline_processor()

    async for _ in ep.on_event(_make_code_event()):
        pass

    processor.project_code_blocks_to_graph.assert_awaited_once()
    kwargs = processor.project_code_blocks_to_graph.await_args.kwargs
    assert kwargs["record_id"] == "rec-1"
    assert kwargs["org_id"] == "org-1"
    assert kwargs["file_path"] == "src/client.py"
    assert kwargs["connector_id"] == "connector-1"
    # Reuses what the parsing service already returned rather than re-parsing.
    assert kwargs["block_containers"] is parsing_client.parse.return_value.block_container


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_service_pipeline_skips_projection_for_non_code() -> None:
    """A PDF has no symbols; nothing should reach the code graph."""
    ep, processor, _, _ = _service_pipeline_processor()

    async for _ in ep.on_event(_make_event_data()):
        pass

    processor.project_code_blocks_to_graph.assert_not_awaited()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_code_projection_runs_before_enrichment() -> None:
    """Enrichment is allowed to fail; the code graph must still be populated."""
    ep, processor, _, sink_orchestrator = _service_pipeline_processor()
    sink_orchestrator.enrich = AsyncMock(side_effect=RuntimeError("extraction down"))

    async for _ in ep.on_event(_make_code_event()):
        pass

    processor.project_code_blocks_to_graph.assert_awaited_once()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_failed_code_projection_stops_the_record() -> None:
    """A record marked COMPLETED with no blocks makes the edge builder resolve
    the repo against a symbol table it is missing from, so a projection failure
    has to abort before indexing rather than be swallowed."""
    ep, processor, _, sink_orchestrator = _service_pipeline_processor()
    processor.project_code_blocks_to_graph.side_effect = RuntimeError("graph down")

    with pytest.raises(RuntimeError, match="graph down"):
        async for _ in ep.on_event(_make_code_event()):
            pass

    kwargs = processor.project_code_blocks_to_graph.await_args.kwargs
    assert kwargs["propagate_failure"] is True
    sink_orchestrator.index.assert_not_awaited()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_service_pipeline_forwards_the_record_file_path() -> None:
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())
    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)
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
    event_data = _make_event_data()
    event_data["payload"]["filePath"] = "docs/reports/test.pdf"

    async for _ in ep.on_event(event_data):
        pass

    assert parsing_client.parse.await_args.kwargs["file_path"] == "docs/reports/test.pdf"


@pytest.mark.asyncio
async def test_an_emptied_code_file_loses_its_old_blocks() -> None:
    """B3: the service path marked an emptied code file EMPTY but kept its last
    version's blocks, so their calls and imports stayed in the code graph."""
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result(empty=True))
    processor = MagicMock()
    processor._drop_code_blocks = AsyncMock()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=MagicMock(),
                               sink_orchestrator=MagicMock(), processor=processor)
    # Another record holds the content, so the emptied record's vectors are not dropped here.
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-2"])

    events = [e async for e in ep._orchestrate_via_services(
        record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="m.py",
        mime_type="text/x-python", extension="py", event_type="updateRecord",
        prev_virtual_record_id=None, file_content=b"", is_code=True, file_path="a/m.py",
    )]

    processor._drop_code_blocks.assert_awaited_once_with("rec-1")
    assert IndexingEvent.INDEXING_COMPLETE in [e.event for e in events]


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_record_deleted_during_parsing_ends_as_an_expected_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The record is gone when the service path re-reads it after parsing: that is
    a deletion (terminal, logged below error), not a RuntimeError that is retried."""
    parsing_client, sink_orchestrator = _service_parsing_client(), _indexing_sink()
    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=sink_orchestrator
    )
    record = ep.graph_provider.get_document.return_value

    async def deleted_once_parsed(*_args: object, raise_on_error: bool = False, **_kwargs: object) -> dict | None:
        if not parsing_client.parse.await_count:
            return record
        assert raise_on_error, "None is a deletion only from a read that propagates graph errors"
        return None

    ep.graph_provider.get_document.side_effect = deleted_once_parsed

    with caplog.at_level(logging.INFO), pytest.raises(RecordDeletedError) as raised:
        _ = [event async for event in ep.on_event(_make_event_data())]

    assert raised.value.record_id == "rec-1"
    assert is_expected_outcome(raised.value)
    assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL
    sink_orchestrator.index.assert_not_awaited()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
