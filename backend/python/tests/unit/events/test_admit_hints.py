"""``Admit.hints`` reaches the PDF route and decides OCR and Docling there; with no hints the route is the one it always was."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.indexing.extension import (
    PdfParseHints,
)
from tests.unit.events.test_events import (
    _drain,
    _make_event_processor,
    _mock_processor_gen,
    _pdf_inspection,
)

pytestmark = pytest.mark.asyncio


def _ep_with_processors():  # noqa: ANN202
    ep, _, processor, _ = _make_event_processor()
    for name in ("process_pdf_with_docling", "process_pdf_document_with_ocr", "process_pdf_with_pdf_plumber"):
        setattr(processor, name, MagicMock(side_effect=_mock_processor_gen))
    return ep, processor


async def _route(hints: PdfParseHints | None, *, needs_ocr: bool) -> MagicMock:
    ep, processor = _ep_with_processors()
    with _pdf_inspection(needs_ocr=needs_ocr, page_count=3), patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
        await _drain(ep._dispatch_pdf_binary("a.pdf", "rec-1", 1, "c", "org-1", b"%PDF", "vr-1", hints=hints))
    return processor


async def test_without_hints_the_inspection_decides_as_always() -> None:
    assert (await _route(None, needs_ocr=False)).process_pdf_with_docling.called
    assert (await _route(None, needs_ocr=True)).process_pdf_document_with_ocr.called


async def test_hints_can_force_the_ocr_route_on_or_off() -> None:
    assert (await _route(PdfParseHints(ocr=True), needs_ocr=False)).process_pdf_document_with_ocr.called
    off = await _route(PdfParseHints(ocr=False), needs_ocr=True)
    assert not off.process_pdf_document_with_ocr.called and off.process_pdf_with_docling.called


async def test_hints_can_keep_a_pdf_off_docling() -> None:
    processor = await _route(PdfParseHints(allow_docling=False), needs_ocr=False)
    assert not processor.process_pdf_with_docling.called
    assert processor.process_pdf_with_pdf_plumber.called


async def test_hints_with_nothing_set_change_nothing() -> None:
    assert (await _route(PdfParseHints(), needs_ocr=False)).process_pdf_with_docling.called
