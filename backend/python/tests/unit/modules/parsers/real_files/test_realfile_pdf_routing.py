"""Real PDFs reach the parser their page count calls for, on both indexing paths.

The page count comes from the real inspection (in-process indexing) and the real
OCR pass (parsing service); only the parsers themselves are stand-ins, so these
check that the count each path reads off a real file drives the route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events.events import EventProcessor
from app.models.blocks import BlocksContainer
from app.modules.parsers.pdf.untrusted_pdf import inspect_untrusted_pdf
from app.services.parsing.interface import ParseResult, ParserProvider
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
from tests.support.pdf_samples import blank_pdf, broken_xref_pdf, text_pdf

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# (pages, parser) at the default limit of 30 pages.
ROUTES = [(1, "docling"), (30, "docling"), (31, "layout"), (120, "layout")]


@pytest.fixture(autouse=True)
def _default_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDF_DOCLING_MAX_PAGES", raising=False)
    monkeypatch.setenv("ENABLE_PDFPLUMBER_PROCESSOR", "false")


def _event_processor() -> tuple[EventProcessor, MagicMock]:
    async def parsed(*_args: object, **_kwargs: object) -> AsyncIterator[object]:
        yield MagicMock()

    processor = MagicMock()
    for name in ("process_pdf_with_docling", "process_pdf_with_pdf_plumber", "process_pdf_document_with_ocr"):
        setattr(processor, name, MagicMock(side_effect=parsed))
    return EventProcessor(MagicMock(), processor, AsyncMock(), MagicMock()), processor


async def _dispatch(content: bytes) -> MagicMock:
    ep, processor = _event_processor()
    async for _ in ep._dispatch_pdf_binary(
        record_name="a.pdf", record_id="rec-1", record_version=1, connector="KB",
        org_id="org-1", pdf_binary=content, virtual_record_id="vr-1",
    ):
        pass
    return processor


def _smart_parser() -> tuple[SmartPDFParser, MagicMock, MagicMock, MagicMock]:
    def result(provider: ParserProvider) -> ParseResult:
        return ParseResult(block_container=BlocksContainer(blocks=[], block_groups=[]), provider_used=provider)

    docling, layout, ocr = MagicMock(), MagicMock(), MagicMock()
    docling.parse = AsyncMock(return_value=result(ParserProvider.DOCLING))
    layout.parse = AsyncMock(return_value=result(ParserProvider.DEFAULT))
    ocr.parse = AsyncMock(return_value=result(ParserProvider.DEFAULT))
    return SmartPDFParser(docling, ocr, large_pdf_parser=layout), docling, layout, ocr


class TestInProcessIndexing:
    @pytest.mark.parametrize(("pages", "parser"), ROUTES, ids=[str(p) for p, _ in ROUTES])
    async def test_a_text_pdf_goes_where_its_page_count_says(self, pages: int, parser: str) -> None:
        processor = await _dispatch(text_pdf(pages=pages))

        assert processor.process_pdf_with_docling.call_count == (parser == "docling")
        assert processor.process_pdf_with_pdf_plumber.call_count == (parser == "layout")
        processor.process_pdf_document_with_ocr.assert_not_called()

    async def test_a_long_scan_goes_to_ocr(self) -> None:
        processor = await _dispatch(blank_pdf(pages=40))

        processor.process_pdf_document_with_ocr.assert_called_once()
        processor.process_pdf_with_docling.assert_not_called()
        processor.process_pdf_with_pdf_plumber.assert_not_called()

    async def test_the_limit_follows_the_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PDF_DOCLING_MAX_PAGES", "5")

        processor = await _dispatch(text_pdf(pages=6))

        processor.process_pdf_with_pdf_plumber.assert_called_once()
        processor.process_pdf_with_docling.assert_not_called()

    async def test_a_damaged_file_is_counted_after_its_repair(self) -> None:
        """pdfminer sees no pages in it; the count is the rewritten copy's."""
        inspected = await inspect_untrusted_pdf(broken_xref_pdf(16 * 1024))

        assert inspected.repaired is True
        assert inspected.page_count == 1


class TestParsingService:
    @pytest.mark.parametrize(("pages", "parser"), ROUTES, ids=[str(p) for p, _ in ROUTES])
    async def test_a_text_pdf_goes_where_its_page_count_says(self, pages: int, parser: str) -> None:
        smart, docling, layout, ocr = _smart_parser()

        await smart.parse(text_pdf(pages=pages), "a.pdf")

        assert docling.parse.await_count == (parser == "docling")
        assert layout.parse.await_count == (parser == "layout")
        ocr.parse.assert_not_called()

    async def test_a_long_scan_goes_to_ocr(self) -> None:
        smart, docling, layout, ocr = _smart_parser()

        await smart.parse(blank_pdf(pages=40), "a.pdf")

        ocr.parse.assert_awaited_once()
        docling.parse.assert_not_called()
        layout.parse.assert_not_called()

    async def test_a_file_pdfplumber_cannot_open_goes_to_docling(self) -> None:
        smart, docling, layout, _ = _smart_parser()

        await smart.parse(b"%PDF-1.7 not really a pdf", "a.pdf")

        docling.parse.assert_awaited_once()
        layout.parse.assert_not_called()
