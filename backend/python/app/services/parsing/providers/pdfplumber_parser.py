"""Parser provider wrapping PDFPlumber + OpenCV layout analyser."""
from __future__ import annotations

from typing import Any

from app.modules.parsers.pdf.pdfplumber_opencv_processor import PDFPlumberOpenCVProcessor
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)


class PdfPlumberParser:
    """Wraps :class:`PDFPlumberOpenCVProcessor`."""

    def __init__(self, processor: PDFPlumberOpenCVProcessor) -> None:
        self._processor = processor

    def supported_formats(self) -> list[str]:
        return ["pdf"]

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        record_name_pdf = record_name if record_name.lower().endswith(".pdf") else f"{record_name}.pdf"
        parsed_data = await self._processor.parse_document(record_name_pdf, content)
        block_containers = await self._processor.create_blocks(parsed_data)
        if block_containers is None:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"PdfPlumber+OpenCV returned empty result for '{record_name}'",
            )
        return ParseResult(
            block_container=block_containers,
            provider_used=ParserProvider.DEFAULT,
            metadata={"record_name": record_name},
        )

assert isinstance(PdfPlumberParser.__new__(PdfPlumberParser), IParser)
