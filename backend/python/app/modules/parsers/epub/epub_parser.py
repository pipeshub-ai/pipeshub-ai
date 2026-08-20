from pathlib import Path
from typing import Any

from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
)
from app.utils.libreoffice_convert import convert_with_libreoffice


class EPUBParser:
    """Parser for EPUB e-books.

    Converts EPUB to PDF via LibreOffice, then delegates all block extraction
    to the existing PDF parser (typically a ``SmartPDFParser``, which itself
    chooses Docling or pdfplumber/OCR). This class never parses PDF content
    directly and must not depend on PyMuPDF/fitz.
    """

    def __init__(self, pdf_parser: IParser | None = None) -> None:
        self.pdf_parser = pdf_parser

    async def parse(
        self, content: bytes, record_name: str, config: dict[str, Any] | None = None,
    ) -> ParseResult:
        if self.pdf_parser is None:
            raise ParseError(
                ParseErrorCode.PROVIDER_UNAVAILABLE,
                "EPUB parsing requires a pdf_parser; none was configured",
            )
        pdf_bytes = await self.convert_epub_to_pdf_async(content)
        pdf_record_name = f"{Path(record_name).stem}.pdf" if record_name else "converted.pdf"
        return await self.pdf_parser.parse(pdf_bytes, pdf_record_name, config)

    async def convert_epub_to_pdf_async(self, binary: bytes) -> bytes:
        """Async EPUB -> PDF conversion for use on an event loop (e.g. the
        parsing service). See :func:`DocParser.convert_doc_to_docx_async` for
        rationale.
        """
        return await convert_with_libreoffice(binary, "epub", "pdf")
