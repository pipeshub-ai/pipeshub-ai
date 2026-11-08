from typing import Any

from app.modules.parsers.excel.excel_parser import ExcelParser
from app.services.parsing.interface import ParseResult
from app.utils.libreoffice_convert import (
    convert_with_libreoffice,
    unreadable_file_as_parse_error,
)


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
        """Async .xls -> .xlsx conversion. See
        :func:`DocParser.convert_doc_to_docx_async` for rationale.
        """
        with unreadable_file_as_parse_error("xls"):
            return await convert_with_libreoffice(binary, "xls", "xlsx")
