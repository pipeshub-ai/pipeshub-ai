"""Runs a real file through either pipeline, or through the record handler, for the new file types.

Legacy: the real ``EventProcessor.on_event`` (gate-free dispatch, the prep step) and the real
``Processor`` methods, with only storage, the LLM and the sink stubbed; the blocks the sink would
have received are returned. Service: the parser ``parsing_main`` registers for the format, with
Docling's raw document turned into blocks the way the indexer does. Handler: the real
``RecordEventHandler.process_event`` from the gate to the stored status and reason.
"""
from __future__ import annotations

import contextlib
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import file_types
from app.events.events import DedupDecision, EventProcessor
from app.events.processor import Processor
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor

if TYPE_CHECKING:
    from app.models.blocks import BlocksContainer

LOGGER = logging.getLogger("new-type-real-file-tests")


class _Store:
    """The record as the graph holds it: status writes land here."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.provider = AsyncMock()
        self.provider.get_document = AsyncMock(side_effect=self._get)
        self.provider.update_node = AsyncMock(side_effect=self._update)
        self.provider.update_queued_duplicates_status = AsyncMock(return_value=0)

    async def _get(self, _doc_id: str, collection: str, **_k: object) -> dict[str, Any]:
        if collection == "apps":
            return {"_key": "conn-1", "name": "SMB", "isActive": True, "isAgentActive": False}
        return dict(self.record)

    async def _update(self, _doc_id: str, _collection: str, fields: dict[str, Any]) -> bool:
        self.record.update(fields)
        return True


def processor(store: _Store, captured: list[BlocksContainer], docling_inputs: list[bytes]) -> Processor:
    p = Processor.__new__(Processor)
    p.logger = LOGGER
    excel = ExcelParser(LOGGER, MagicMock())
    p.parsers = {
        "html": SelectolaxHtmlParser(), "png": ImageParser(LOGGER), "xlsx": excel,
        "md": MarkdownParser(logger=LOGGER, config_service=MagicMock()),
    }
    docling = DoclingProcessor(LOGGER, None)
    real_parse = docling.parse_document

    async def parse_document(name: str, content: object) -> object:
        data = content.getvalue() if hasattr(content, "getvalue") else content
        docling_inputs.append(bytes(data))  # type: ignore[arg-type]
        return await real_parse(name, content)

    async def create_blocks(conv_res: object, **_k: object) -> BlocksContainer:
        return await DoclingProcessor.create_blocks(docling, conv_res, skip_table_enrichment=True)

    docling.parse_document = parse_document  # type: ignore[method-assign]
    docling.create_blocks = create_blocks  # type: ignore[method-assign]
    p.docling_processor = docling
    p.graph_provider = store.provider
    p._get_llm_for_role = AsyncMock(return_value=(None, None))
    p._convert_record = lambda _record: SimpleNamespace()
    p._create_transform_context = lambda record, *_a: record
    sink = MagicMock()
    sink.apply = AsyncMock(side_effect=lambda ctx: captured.append(ctx.block_containers))
    p._pipeline = lambda: sink
    return p


def _lightweight_excel_blocks() -> object:
    async def create_blocks(self: ExcelParser, _llm: object) -> BlocksContainer:
        return await self.create_blocks_lightweight()

    return patch.object(ExcelParser, "create_blocks", create_blocks)


def _record(ext: str | None, mime: str | None, name: str, record_type: str = "FILE") -> dict[str, Any]:
    return {
        "_key": "r1", "id": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "recordType": record_type,
        "recordName": name, "connectorId": "conn-1", "indexingStatus": "QUEUED",
        "extension": ext, "mimeType": mime, "origin": "CONNECTOR", "connectorName": "SMB",
    }


async def legacy(ext: str | None, mime: str | None, content: bytes, name: str | None = None) -> tuple[
    list[BlocksContainer], list[bytes], dict[str, Any]
]:
    """The blocks the legacy pipeline hands the sink, what Docling was given, and the record."""
    name = name or f"Sample.{ext}"
    store = _Store(_record(ext, mime, name))
    captured: list[BlocksContainer] = []
    docling_inputs: list[bytes] = []
    ep = EventProcessor(LOGGER, processor(store, captured, docling_inputs), store.provider, MagicMock())
    data = {
        "eventType": "newRecord",
        "payload": {
            "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "version": 1,
            "connectorName": "SMB", "extension": ext, "mimeType": mime, "recordName": name,
            "buffer": content,
        },
    }
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         _lightweight_excel_blocks(), _parse_in_thread():
        async for _ in ep.on_event(data):
            pass
    return captured, docling_inputs, store.record


def _parse_in_thread() -> object:
    """The msg worker run in this process, so a test can patch what it reads."""
    return patch("app.modules.parsers.parse_pool.run_blocking", side_effect=lambda fn, *a, **_k: fn(*a))


async def service(ext: str, content: bytes, mime: str | None = None) -> BlocksContainer:
    """The blocks the parsing service's parser for this type returns, built as the indexer builds them."""
    from docling_core.types.doc.document import DoclingDocument

    from app.modules.parsers.email.msg_parser import MsgParser
    from app.modules.parsers.ooxml.ooxml_package_parser import OoxmlPackageParser
    from app.modules.parsers.text.plain_text_parser import PlainTextParser
    from app.services.parsing.providers.local_docling_parser import LocalDoclingParser

    fmt = file_types.parse_format(mime or "application/octet-stream", ext)
    docling = DoclingProcessor(LOGGER, None)
    local = LocalDoclingParser(docling)
    parsers = {
        "docm": OoxmlPackageParser(local, "word"),
        "dotx": OoxmlPackageParser(local, "word"),
        "potx": OoxmlPackageParser(local, "slides"),
        "msg": MsgParser(SelectolaxHtmlParser(allow_remote_fetch=False)),
        "log": PlainTextParser(),
        "cfg": PlainTextParser(),
    }
    name = f"Sample.{ext}"
    if fmt == "xlsx":
        return await ExcelParser(LOGGER, MagicMock()).parse_workbook(content)
    with _parse_in_thread():
        result = await parsers[fmt].parse(content, name)  # type: ignore[index]
    if result.block_container is not None:
        return result.block_container
    document = DoclingDocument.model_validate_json(result.raw_document)
    return await docling.create_blocks(document, skip_table_enrichment=True)


async def handler_run(
    ext: str | None, mime: str | None, content: bytes, *, name: str | None = None,
    size: int | None = None, attempt_final: bool = False, real_worker: bool = False,
) -> tuple[dict[str, Any], bool]:
    """The record after one delivery through the real record handler, and whether it was downloaded."""
    from tests.unit.services.messaging.test_record_handler import _make_handler

    name = name or f"Sample.{ext}"
    record = _record(ext, mime, name)
    if size is not None:
        record["sizeInBytes"] = size
    store = _Store(record)
    captured: list[BlocksContainer] = []
    ep = EventProcessor(LOGGER, processor(store, captured, []), store.provider, MagicMock())
    ep.sink_orchestrator = None
    ep.stage_ingress = None
    handler = _make_handler(logger=LOGGER, event_processor=ep)
    handler.event_processor.graph_provider = store.provider
    downloads: list[str] = []

    async def download(**_k: object) -> bytes:
        downloads.append(name)
        return content

    payload = {
        "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "connectorId": "conn-1",
        "recordName": name, "extension": ext, "mimeType": mime, "version": 1,
        "signedUrl": "https://example.invalid/f", "is_final_failure": attempt_final,
    }
    with patch.object(handler, "_download_from_signed_url", side_effect=download), \
         patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         _lightweight_excel_blocks(), (contextlib.nullcontext() if real_worker else _parse_in_thread()):
        try:
            async for _ in handler.process_event("newRecord", payload):
                pass
        except Exception:
            pass
    return store.record, bool(downloads)
