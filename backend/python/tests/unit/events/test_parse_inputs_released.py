"""A record's file and parse results are freed once parsing is done.

The parse permit goes back at PARSING_COMPLETE, so whatever the parse held
(the file's bytes, the Docling document, OCR pages) used to stay referenced
through embedding, the blob write and the upsert, outside anything the
governor counts. Each test checks, with weak references, what is still alive
when the indexing pipeline starts.
"""
from __future__ import annotations

import gc
import weakref
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ExtensionTypes, ProgressStatus
from app.events.events import DedupDecision, EventProcessor
from app.events.processor import Processor
from app.models.blocks import BlocksContainer
from app.modules.parsers.pdf.untrusted_pdf import InspectedPdf
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from app.services.messaging.kafka.handlers.record import RecordEventHandler


class Bytes(bytearray):
    """File content that can be weakly referenced (a bytes subclass cannot be)."""


class Parsed:
    """Stands in for a Docling document, an OCR result or a workbook."""


def _alive(refs: dict[str, weakref.ref]) -> list[str]:
    gc.collect()
    return sorted(name for name, ref in refs.items() if ref() is not None)


def _processor(refs: dict[str, weakref.ref], at_indexing: list[list[str]]) -> Processor:
    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        processor = Processor(
            logger=MagicMock(), config_service=MagicMock(), indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(), parsers={}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )
    processor.graph_provider.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

    async def parse(*_args: object, **_kwargs: object) -> Parsed:
        parsed = Parsed()
        refs["parsed"] = weakref.ref(parsed)
        return parsed

    async def create_blocks(*_args: object, **_kwargs: object) -> BlocksContainer:
        return BlocksContainer(blocks=[], block_groups=[])

    async def apply(_ctx: object) -> None:
        at_indexing.append(_alive(refs))

    processor.docling_client.parse_pdf_batched = parse
    processor.docling_processor.parse_document = parse
    processor.docling_processor.create_blocks = create_blocks
    processor._convert_record = lambda record: MagicMock()
    processor._create_transform_context = MagicMock()
    processor._pipeline = MagicMock(return_value=MagicMock(apply=apply))
    return processor


async def _index(extension: str, refs: dict[str, weakref.ref], processor: Processor) -> None:
    graph = AsyncMock()
    graph.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    graph.update_node = AsyncMock(return_value=True)
    events = EventProcessor(MagicMock(), processor, graph, MagicMock())
    content = Bytes(b"%PDF-1.7 a file")
    refs["file"] = weakref.ref(content)
    payload = {
        "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": None, "version": 1, "connectorName": "",
        "extension": extension, "mimeType": "unknown", "recordName": "a", "buffer": content,
    }
    data = {"payload": payload}
    del content

    # Plain functions: a mock keeps the arguments and return value of its calls.
    async def inspected(content: bytes, **_kw: object) -> InspectedPdf:
        return InspectedPdf(content, needs_ocr=False, repaired=False, page_count=1)

    async def not_a_duplicate(*_args: object, **_kwargs: object) -> DedupDecision:
        return DedupDecision(virtual_record_id=None, skip_indexing=False)

    with patch.object(events, "_check_duplicate_by_md5", new=not_a_duplicate), \
         patch("app.events.events.inspect_untrusted_pdf", new=inspected), \
         patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
        async for event in events.on_event(data):
            if event.event == IndexingEvent.PARSING_COMPLETE:
                # What the record handler does, and its own test below checks.
                payload.pop("buffer", None)


class TestWhatIsAliveWhenIndexingStarts:
    async def test_a_pdf_parsed_by_docling_leaves_neither_file_nor_document(self) -> None:
        refs: dict[str, weakref.ref] = {}
        alive: list[list[str]] = []

        await _index(ExtensionTypes.PDF.value, refs, _processor(refs, alive))

        assert alive == [[]]

    async def test_a_docx_leaves_neither_file_nor_parsed_document(self) -> None:
        refs: dict[str, weakref.ref] = {}
        alive: list[list[str]] = []

        await _index(ExtensionTypes.DOCX.value, refs, _processor(refs, alive))

        assert alive == [[]]

    async def test_a_pptx_leaves_neither_file_nor_parsed_document(self) -> None:
        refs: dict[str, weakref.ref] = {}
        alive: list[list[str]] = []

        await _index(ExtensionTypes.PPTX.value, refs, _processor(refs, alive))

        assert alive == [[]]


def _handler() -> RecordEventHandler:
    from tests.unit.services.messaging.test_record_handler import _make_handler

    return _make_handler()


class TestTheRecordHandlerDropsTheDownload:
    @pytest.mark.parametrize("path", ["signed_url", "connector_stream"])
    async def test_the_downloaded_bytes_are_gone_when_parsing_is_complete(self, path: str) -> None:
        handler = _handler()
        graph = handler.event_processor.graph_provider
        record = {
            "_key": "r1", "virtualRecordId": "vr1", "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        graph.get_document = AsyncMock(side_effect=[record, record])
        graph.update_queued_duplicates_status = AsyncMock(return_value=0)
        content = Bytes(b"a file")
        ref = weakref.ref(content)
        alive_after_parsing: list[bool] = []

        async def on_event(_data: dict) -> AsyncGenerator[PipelineEvent, None]:
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            gc.collect()
            alive_after_parsing.append(ref() is not None)
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        handler.event_processor.on_event = on_event
        payload = {
            "recordId": "r1", "virtualRecordId": "vr1", "orgId": "org-1",
            "mimeType": "application/pdf", "extension": "pdf",
        }
        # Plain functions again: a mock keeps what it returned.
        downloads = [content]

        async def download(**_kwargs: object) -> Bytes:
            return downloads.pop()

        async def stream(**_kwargs: object) -> dict:
            return {"data": downloads.pop()}

        async def token(*_args: object) -> str:
            return "t"

        patches = []
        if path == "signed_url":
            payload["signedUrl"] = "https://example.com/file.pdf"
            patches.append(patch.object(handler, "_download_from_signed_url", new=download))
        else:
            handler.config_service.get_config = AsyncMock(return_value={"connectors": {"endpoint": "http://c"}})
            patches.append(patch("app.services.messaging.kafka.handlers.record.generate_jwt", new=token))
            patches.append(patch("app.services.messaging.kafka.handlers.record.make_api_call", new=stream))
        del content

        for p in patches:
            p.start()
        try:
            events = [event async for event in handler.process_event("newRecord", payload)]
        finally:
            for p in patches:
                p.stop()

        assert IndexingEvent.INDEXING_COMPLETE in [e.event for e in events]
        assert alive_after_parsing == [False]
