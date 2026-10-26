"""Smart PDF parser that auto-selects Docling or OCR based on page content.

Mirrors the existing OCR-detection logic from EventProcessor so the decision
stays encapsulated inside the registry rather than being spread across callers.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import pdfplumber

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


class SmartPDFParser:
    """Delegates to OCR when the document appears to be scanned; otherwise uses
    the primary parser (typically DoclingServiceParser or LocalDoclingParser).

    With a *large_pdf_parser*, a text PDF that Docling does not take (over its
    size or page limit, see ``docling_takes``) goes to that parser instead, and
    so does one the primary parser fails on, before OCR is tried, or one it
    cannot reach (Docling down or shedding load).
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
            return await self._ocr.parse(content, record_name, config)

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
                return await self._ocr_after(exc, content, record_name, config)
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
                return await self._ocr_after(large_exc, content, record_name, config)

    async def _ocr_after(
        self,
        exc: ParseError,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None,
    ) -> ParseResult:
        if exc.code in _NOT_RETRIED:
            raise exc
        logger.warning(
            "SmartPDFParser: primary parser failed for '%s' (%s). Falling back to OCR.",
            record_name,
            exc.message,
        )
        return await self._ocr.parse(content, record_name, config)


assert isinstance(SmartPDFParser.__new__(SmartPDFParser), IParser)
