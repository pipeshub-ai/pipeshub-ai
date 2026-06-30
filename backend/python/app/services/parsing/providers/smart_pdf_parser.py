"""Smart PDF parser that auto-selects Docling or OCR based on page content.

Mirrors the existing OCR-detection logic from EventProcessor so the decision
stays encapsulated inside the registry rather than being spread across callers.
"""
from __future__ import annotations

import io
import logging
from typing import Any

import pdfplumber

from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.providers.ocr_parser import OCRParser

logger = logging.getLogger(__name__)

# Thresholds matching OCRStrategy.needs_ocr()
_MIN_TEXT_LENGTH = 100
_MIN_SIGNIFICANT_IMAGES = 2
_MIN_IMAGE_WIDTH = 100
_MIN_IMAGE_HEIGHT = 100
_LOW_DENSITY_THRESHOLD = 0.01

# Fraction of pages that must need OCR before we switch the whole document
_OCR_PAGE_THRESHOLD = 0.3


def _page_needs_ocr(page: Any) -> bool:
    """Replicate OCRStrategy.needs_ocr() without importing pdfplumber globally."""
    try:
        text = (page.extract_text() or "").strip()
        words = page.extract_words()
        images = page.images
        page_area = page.width * page.height

        significant_images = sum(
            1
            for img in images
            if (img.get("width") or 0) > _MIN_IMAGE_WIDTH
            and (img.get("height") or 0) > _MIN_IMAGE_HEIGHT
        )
        has_minimal_text = len(text) < _MIN_TEXT_LENGTH
        has_significant_images = significant_images > _MIN_SIGNIFICANT_IMAGES
        text_density = (
            sum((w["x1"] - w["x0"]) * (w["bottom"] - w["top"]) for w in words) / page_area
            if words and page_area > 0
            else 0.0
        )
        low_density = text_density < _LOW_DENSITY_THRESHOLD
        return (has_minimal_text and has_significant_images) or low_density
    except Exception:  # noqa: BLE001
        return True


def _detect_needs_ocr(content: bytes) -> bool:
    """Return True when enough pages appear scanned/image-heavy."""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            total = len(pdf.pages)
            if total == 0:
                return False
            ocr_pages = sum(1 for p in pdf.pages if _page_needs_ocr(p))
            return (ocr_pages / total) >= _OCR_PAGE_THRESHOLD
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
        needs_ocr = _detect_needs_ocr(content)
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
