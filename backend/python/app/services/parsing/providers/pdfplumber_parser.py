"""Parser provider wrapping PDFPlumber + OpenCV layout analyser."""
from __future__ import annotations

from typing import Any

from app.exceptions.non_retryable import NonRetryableError
from app.modules.parsers.pdf.pdfplumber_opencv_processor import PDFPlumberOpenCVProcessor
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.paid_cache.orphan import RecordGone


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
        try:
            parsed_data = await self._processor.parse_document(
                record_name_pdf, content, table_model=True, record_id=(config or {}).get("record_id")
            )
            block_containers = await self._processor.create_blocks(
                parsed_data,
                skip_llm_enrichment=bool((config or {}).get("skip_table_enrichment")),
            )
        except (NonRetryableError, RecordGone):
            # A deliberate stop (a table model call refused for good, a record deleted mid-run) is not a file OCR could read better.
            raise
        except Exception as e:
            # A ParseError lets SmartPDFParser fall back to OCR, as the in-process path does.
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"PdfPlumber+OpenCV failed on '{record_name}': {e}",
            ) from e
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
