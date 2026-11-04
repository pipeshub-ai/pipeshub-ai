"""Smart PDF parser that auto-selects Docling or OCR based on page content.

Mirrors the existing OCR-detection logic from EventProcessor so the decision
stays encapsulated inside the registry rather than being spread across callers.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any

import pdfplumber

from app.exceptions.indexing_exceptions import MultimodalModelNotConfiguredError
from app.models.blocks import Block, BlockType
from app.modules.parsers.encryption import raise_if_pdf_needs_password
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.services.docling.client import (
    MAX_PAGES_ENV,
    DoclingUnavailableError,
    docling_max_pages,
    docling_takes,
)
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.providers.ocr_parser import OCRParser

logger = logging.getLogger(__name__)

# Fraction of sampled pages that must meet the general OCR heuristics.
_OCR_PAGE_THRESHOLD = 0.3
_OCR_SAMPLE_PAGES = 5
# Failures about the file itself, which no other parser would read better.
_NOT_RETRIED = (
    ParseErrorCode.UNSUPPORTED_FORMAT,
    ParseErrorCode.EMPTY_CONTENT,
    ParseErrorCode.INVALID_INPUT,
)


def _sample_page_indices(total: int, sample_size: int) -> list[int]:
    """Evenly spaced page indices, first and last included.

    Deterministic so a retry of the same file makes the same OCR decision.
    """
    if sample_size <= 1:
        return [0]
    step = (total - 1) / (sample_size - 1)
    return sorted({round(i * step) for i in range(sample_size)})


def _detect_needs_ocr(content: bytes) -> tuple[bool, int | None]:
    """Whether sampled pages indicate that OCR is needed, and the page count
    (None when pdfplumber cannot open the file).

    Raises ``PasswordProtectedError`` for a PDF that needs a password.
    """
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            total = len(pdf.pages)
            if total == 0:
                return False, 0
            sample_pages = [
                pdf.pages[i]
                for i in _sample_page_indices(total, min(_OCR_SAMPLE_PAGES, total))
            ]
            sample_size = len(sample_pages)
            if any(
                OCRStrategy.has_dominant_image_with_limited_text(page)
                for page in sample_pages
            ):
                return True, total
            ocr_pages = sum(
                1 for page in sample_pages if OCRStrategy.needs_ocr(page, logger)
            )
            return (ocr_pages / sample_size) >= _OCR_PAGE_THRESHOLD, total
    except Exception:  # noqa: BLE001
        # pdfplumber could not open it. A password is the one reason that
        # must not fall through to OCR, which would spend model calls on a
        # file no one can read.
        raise_if_pdf_needs_password(content)
        return False, None


def _has_text(result: ParseResult) -> bool:
    """Whether a parse found any text, in built blocks or in a Docling document.

    Table text counts: a PdfPlumber table row keeps it in ``row_natural_language_text``,
    and Docling keeps cell text under ``tables[].data.table_cells`` (``texts`` can then
    be empty). Image blocks never count: their data is the picture, not text.
    """
    if result.block_container is not None:
        return any(_block_has_text(block) for block in result.block_container.blocks)
    try:
        doc = json.loads(result.raw_document or "{}")
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    if any(_has_str(t, "text") for t in doc.get("texts") or []):
        return True
    for table in doc.get("tables") or []:
        data = table.get("data") if isinstance(table, dict) else None
        cells = data.get("table_cells") if isinstance(data, dict) else None
        if any(_has_str(cell, "text") for cell in cells or []):
            return True
    return False


def _block_has_text(block: Block) -> bool:
    if block.type == BlockType.IMAGE:
        return False
    if isinstance(block.data, str):
        return bool(block.data.strip())
    return _has_str(block.data, "row_natural_language_text")


def _has_str(value: object, key: str) -> bool:
    return isinstance(value, dict) and isinstance(value.get(key), str) and bool(value[key].strip())


class SmartPDFParser:
    """Delegates to OCR when the document appears to be scanned; otherwise uses
    the primary parser (typically DoclingServiceParser or LocalDoclingParser).

    With a *large_pdf_parser*, a text PDF that Docling does not take (over its
    size or page limit, see ``docling_takes``) goes to that parser instead, and
    so does one the primary parser fails on, before OCR is tried, or one it
    cannot reach (Docling down or shedding load).

    A PDF sent to OCR when no model that can read images is set up is read as
    text instead, if it has any; see ``_text_without_ocr``.
    """

    def __init__(
        self,
        primary_parser: IParser,
        ocr_parser: OCRParser,
        large_pdf_parser: IParser | None = None,
    ) -> None:
        self._primary = primary_parser
        self._ocr = ocr_parser
        self._large = large_pdf_parser

    def supported_formats(self) -> list[str]:
        return ["pdf"]

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        # Full-document pdfplumber scan is synchronous CPU work; keep it off
        # the event loop so one large PDF can't stall every other request.
        needs_ocr, page_count = await asyncio.to_thread(_detect_needs_ocr, content)
        if needs_ocr:
            logger.info(
                "SmartPDFParser: '%s' appears scanned, using OCR provider",
                record_name,
            )
            try:
                return await self._ocr.parse(content, record_name, config)
            except MultimodalModelNotConfiguredError as exc:
                return await self._text_without_ocr(exc, content, record_name, config, page_count)
        return await self._parse_text(content, record_name, config, page_count)

    async def _text_without_ocr(
        self,
        no_model: MultimodalModelNotConfiguredError,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None,
        page_count: int | None,
    ) -> ParseResult:
        """A PDF sent to OCR when no model that can read images is set up.

        One page that is mostly image is enough to send the whole PDF to OCR
        (an image-only cover, say), so failing here would lose the text of
        every other page. The text layer is read instead, without what is only
        in images. A PDF with no text layer, a real scan, still fails with the
        reason that says to add such a model.
        """
        logger.warning(
            "SmartPDFParser: '%s' needs OCR for some pages, but no model that can read "
            "images is set up. Reading its text layer instead; text only in images is left out.",
            record_name,
        )
        # A text parser that fails keeps its own error: a timeout or a crash is
        # not a missing model, and must stay retryable.
        result = await self._parse_text(content, record_name, config, page_count, ocr_fallback=False)
        if not _has_text(result):
            raise no_model
        result.metadata = {**(result.metadata or {}), "ocr_skipped": "no multimodal model"}
        return result

    async def _parse_text(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None,
        page_count: int | None,
        *,
        ocr_fallback: bool = True,
    ) -> ParseResult:
        primary = self._primary
        if self._large is not None and not docling_takes(len(content), page_count):
            logger.info(
                "SmartPDFParser: '%s' (%s pages, %d bytes) is over Docling's limits "
                "(%s=%d), using the PdfPlumber+OpenCV parser",
                record_name, page_count, len(content), MAX_PAGES_ENV, docling_max_pages(),
            )
            primary = self._large

        try:
            return await primary.parse(content, record_name, config)
        except DoclingUnavailableError as exc:
            if self._large is None or primary is self._large:
                raise
            logger.warning(
                "SmartPDFParser: Docling is unavailable for '%s' (%s). Using the PdfPlumber+OpenCV parser.",
                record_name,
                exc,
            )
            return await self._large.parse(content, record_name, config)
        except ParseError as exc:
            if self._large is None or primary is self._large or exc.code in _NOT_RETRIED:
                return await self._ocr_after(exc, content, record_name, config, ocr_fallback=ocr_fallback)
            # A text PDF is not sent to OCR (a model call per page) just because
            # Docling could not read it: the local layout parser reads it first.
            logger.warning(
                "SmartPDFParser: Docling failed for '%s' (%s). Using the PdfPlumber+OpenCV parser.",
                record_name,
                exc.message,
            )
            try:
                return await self._large.parse(content, record_name, config)
            except ParseError as large_exc:
                return await self._ocr_after(large_exc, content, record_name, config, ocr_fallback=ocr_fallback)

    async def _ocr_after(
        self,
        exc: ParseError,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None,
        *,
        ocr_fallback: bool = True,
    ) -> ParseResult:
        if exc.code in _NOT_RETRIED or not ocr_fallback:
            raise exc
        logger.warning(
            "SmartPDFParser: primary parser failed for '%s' (%s). Falling back to OCR.",
            record_name,
            exc.message,
        )
        return await self._ocr.parse(content, record_name, config)


assert isinstance(SmartPDFParser.__new__(SmartPDFParser), IParser)
