from io import BytesIO

from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.utils.libreoffice_convert import (
    convert_with_libreoffice,
    unreadable_file_as_parse_error,
)


class DocParser:
    """Parser for Microsoft Word .doc and .docx files"""

    def __init__(self, docx_parser=None) -> None:
        self.docx_parser = docx_parser

    async def parse(self, content: bytes, record_name: str, config: dict[str, any] | None = None) -> ParseResult:
        if self.docx_parser is None:
            raise ParseError(
                ParseErrorCode.PROVIDER_UNAVAILABLE,
                "DOC parsing requires a docx_parser; none was configured",
            )
        doc_result = await self.convert_doc_to_docx_async(content)
        return await self.docx_parser.parse(doc_result, record_name, config)

    async def convert_doc_to_docx_async(self, binary: bytes) -> BytesIO:
        """Async .doc -> .docx conversion: runs LibreOffice via
        ``asyncio.create_subprocess_exec`` instead of blocking the event loop
        for the whole conversion.
        """
        with unreadable_file_as_parse_error("doc"):
            docx_bytes = await convert_with_libreoffice(binary, "doc", "docx")
        return BytesIO(docx_bytes)
