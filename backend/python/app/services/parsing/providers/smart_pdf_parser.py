"""Smart PDF parser that auto-selects Docling or VLM based on page content.

Scanned-document detection runs on pdfium's native text index over a small
evenly-spaced page sample (see ``pdf_rasterizer.detect_scanned_pdf``), so the
routing decision costs milliseconds and constant memory regardless of page
count — unlike the previous pdfplumber pass, which extracted text and word
boxes for every page of every PDF.
"""
from __future__ import annotations

import logging
from typing import Any

from app.modules.parsers.pdf.pdf_rasterizer import detect_scanned_pdf
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
)
from app.services.parsing.providers.ocr_parser import OCRParser

logger = logging.getLogger(__name__)


class SmartPDFParser:
    """Delegates to the VLM when the document appears to be scanned; otherwise
    uses the primary parser (typically DoclingServiceParser or
    LocalDoclingParser).

    The *primary_parser* is tried first.  If it raises **and** the failure is
    not a client error the *ocr_parser* (VLM) is used as fallback.
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

    async def _detect_needs_ocr(self, content: bytes, record_name: str) -> bool:
        try:
            return await detect_scanned_pdf(content)
        except Exception as exc:  # noqa: BLE001 — encrypted/corrupt PDFs
            logger.warning(
                "SmartPDFParser: scanned-document detection failed for '%s' (%s); "
                "assuming digital PDF",
                record_name,
                exc,
            )
            return False

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        if await self._detect_needs_ocr(content, record_name):
            logger.info(
                "SmartPDFParser: '%s' appears scanned, using VLM provider",
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
                "SmartPDFParser: primary parser failed for '%s' (%s). Falling back to VLM.",
                record_name,
                exc.message,
            )
            return await self._ocr.parse(content, record_name, config)


assert isinstance(SmartPDFParser.__new__(SmartPDFParser), IParser)
