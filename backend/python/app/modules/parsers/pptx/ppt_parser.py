from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.utils.libreoffice_convert import (
    convert_with_libreoffice,
    unreadable_file_as_parse_error,
)


class PPTParser:
    """Parser for Microsoft PowerPoint .ppt files"""

    def __init__(self, pptx_parser=None) -> None:
        self.pptx_parser = pptx_parser

    async def parse(self, content: bytes, record_name: str, config: dict[str, any] | None = None) -> ParseResult:
        if self.pptx_parser is None:
            raise ParseError(
                ParseErrorCode.PROVIDER_UNAVAILABLE,
                "PPT parsing requires a pptx_parser; none was configured",
            )
        pptx_result = await self.convert_ppt_to_pptx_async(content)
        return await self.pptx_parser.parse(pptx_result, record_name, config)

    async def convert_ppt_to_pptx_async(self, binary: bytes) -> bytes:
        """Async .ppt -> .pptx conversion. See
        :func:`DocParser.convert_doc_to_docx_async` for rationale.
        """
        with unreadable_file_as_parse_error("ppt"):
            return await convert_with_libreoffice(binary, "ppt", "pptx")
