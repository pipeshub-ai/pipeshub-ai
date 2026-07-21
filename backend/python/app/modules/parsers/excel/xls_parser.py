import os
import subprocess
import tempfile
from typing import Any

from app.modules.parsers.excel.excel_parser import ExcelParser
from app.services.parsing.interface import ParseResult

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.utils.libreoffice_convert import convert_with_libreoffice


class XLSParser:
    """Parser for Microsoft Excel .xls files"""

    def __init__(self, excel_parser: ExcelParser) -> None:
        self._excel_parser = excel_parser

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        xlsx_bytes = await self.convert_xls_to_xlsx_async(content)
        return await self._excel_parser.parse(xlsx_bytes, record_name)

    async def convert_xls_to_xlsx_async(self, binary: bytes) -> bytes:
        """Async .xls -> .xlsx conversion for use on an event loop (e.g. the
        parsing service). See :func:`DocParser.convert_doc_to_docx_async` for
        rationale.
        """
        return await convert_with_libreoffice(binary, "xls", "xlsx")

    def convert_xls_to_xlsx(self, binary: bytes) -> bytes:
        """
        Convert .xls file to .xlsx using LibreOffice and return the xlsx binary content

        Args:
            binary (bytes): The binary content of the XLS file

        Returns:
            bytes: The binary content of the converted XLSX file

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
                temp_input = os.path.join(temp_dir, "input.xls")

                # Write binary data to temporary file
                with open(temp_input, "wb") as f:
                    f.write(binary)

                # Convert .xls to .xlsx using LibreOffice
                subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "xlsx",
                        "--outdir",
                        temp_dir,
                        temp_input,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )

                # Get the xlsx file path
                xlsx_file = os.path.join(temp_dir, "input.xlsx")

                if not os.path.exists(xlsx_file):
                    raise FileNotFoundError(
                        "XLSX conversion failed - output file not found"
                    )

                # Read the converted file as binary
                with open(xlsx_file, "rb") as f:
                    xlsx_binary = f.read()

                return xlsx_binary

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
                    f"Error converting .xls to .xlsx: {str(e)}",
                    details={"error": str(e)},
                ) from e
