import os
import subprocess
import tempfile
from pathlib import Path

from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.utils.libreoffice_convert import convert_with_libreoffice


class EPUBParser:
    """Parser for EPUB e-books.

    Converts EPUB to PDF via LibreOffice, then delegates all block extraction
    to the existing PDF parser (typically a ``SmartPDFParser``, which itself
    chooses Docling or pdfplumber/OCR). This class never parses PDF content
    directly and must not depend on PyMuPDF/fitz.
    """

    def __init__(self, pdf_parser=None) -> None:
        self.pdf_parser = pdf_parser

    async def parse(self, content: bytes, record_name: str, config: dict[str, any] | None = None) -> ParseResult:
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

    def convert_epub_to_pdf(self, binary: bytes) -> bytes:
        """Convert an .epub file to .pdf using LibreOffice

        Args:
            binary (bytes): The binary content of the .epub file

        Returns:
            bytes: The converted PDF file content as bytes

        Raises:
            subprocess.CalledProcessError: If LibreOffice is not installed or conversion fails
            FileNotFoundError: If the converted file is not found
            Exception: For other conversion errors
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Check if LibreOffice is installed
                subprocess.run(
                    ["which", "libreoffice"], check=True, capture_output=True
                )

                # Create input file path
                temp_epub = os.path.join(temp_dir, "input.epub")

                # Write binary content to temporary file
                with open(temp_epub, "wb") as f:
                    f.write(binary)

                # Convert .epub to .pdf using LibreOffice
                subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        temp_dir,
                        temp_epub,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )

                # Get the pdf file path
                pdf_file = os.path.join(temp_dir, "input.pdf")

                if not os.path.exists(pdf_file):
                    raise FileNotFoundError(
                        "PDF conversion failed - output file not found"
                    )

                # Read the converted file into bytes
                with open(pdf_file, "rb") as f:
                    pdf_content = f.read()

                return pdf_content

            except subprocess.CalledProcessError as e:
                error_msg = "LibreOffice is not installed. Please install it using: sudo apt-get install libreoffice"
                if e.stderr:
                    error_msg += (
                        f"\nError details: {e.stderr.decode('utf-8', errors='replace')}"
                    )
                raise subprocess.CalledProcessError(
                    e.returncode, e.cmd, output=e.output, stderr=error_msg.encode()
                )
            except subprocess.TimeoutExpired as e:
                raise DocumentProcessingError(
                    "LibreOffice conversion timed out after 60 seconds",
                    details={"timeout": "60s"},
                ) from e
            except Exception as e:
                raise DocumentProcessingError(
                    f"Error converting .epub to .pdf: {str(e)}",
                    details={"error": str(e)},
                ) from e
