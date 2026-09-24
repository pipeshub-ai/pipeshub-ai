"""Smart PDF parser that auto-selects Docling or OCR based on page content.

Mirrors the existing OCR-detection logic from EventProcessor so the decision
stays encapsulated inside the registry rather than being spread across callers.
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
from typing import Any

import pdfplumber

from app.modules.parsers.pdf.ocr_handler import OCRStrategy
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


def _detect_needs_ocr(content: bytes) -> bool:
    """Return True when sampled pages indicate that OCR is needed."""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            total = len(pdf.pages)
            if total == 0:
                return False
            sample_size = min(5, total)
            sample_pages = random.sample(pdf.pages, sample_size)
            if any(
                OCRStrategy.has_dominant_image_with_limited_text(page)
                for page in sample_pages
            ):
                return True
            ocr_pages = sum(
                1 for page in sample_pages if OCRStrategy.needs_ocr(page, logger)
            )
            return (ocr_pages / sample_size) >= _OCR_PAGE_THRESHOLD
    except Exception:  # noqa: BLE001
        return False


class SmartPDFParser:
    """Delegates to OCR when the document appears to be scanned; otherwise uses
    the primary parser (typically DoclingServiceParser or LocalDoclingParser).

    The *primary_parser* is tried first.  If it raises or returns an empty
    result **and** OCR is detected the *ocr_parser* is used as fallback.
    """

    def __init__(
        self,
        primary_parser: IParser,
        ocr_parser: OCRParser,
    ) -> None:
        self._primary = primary_parser
        self._ocr = ocr_parser

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
        needs_ocr = await asyncio.to_thread(_detect_needs_ocr, content)
        if needs_ocr:
            logger.info(
                "SmartPDFParser: '%s' appears scanned, using OCR provider",
                record_name,
            )
            return await self._ocr.parse(content, record_name, config)

        try:
            result = await self._primary.parse(content, record_name, config)
            return result
        except ParseError as exc:
            if exc.code in (
                ParseErrorCode.UNSUPPORTED_FORMAT,
                ParseErrorCode.EMPTY_CONTENT,
                ParseErrorCode.INVALID_INPUT,
            ):
                raise
            logger.warning(
                "SmartPDFParser: primary parser failed for '%s' (%s). Falling back to OCR.",
                record_name,
                exc.message,
            )
            return await self._ocr.parse(content, record_name, config)


assert isinstance(SmartPDFParser.__new__(SmartPDFParser), IParser)
