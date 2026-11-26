import contextlib
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParseResult,
    UnreadableFileError,
    damaged_file_reason,
)
from app.utils.libreoffice_convert import (
    LibreOfficeCouldNotReadFileError,
    convert_with_libreoffice,
    unreadable_file_as_parse_error,
)


@contextlib.contextmanager
def _damaged_file_reported(input_ext: str) -> Iterator[None]:
    try:
        yield
    except LibreOfficeCouldNotReadFileError as exc:
        raise UnreadableFileError(input_ext, damaged_file_reason(input_ext)) from exc


class DocParser:
    """Parser for word-processing files LibreOffice converts to .docx: .doc, and .odt and .rtf."""

    def __init__(self, docx_parser=None, input_ext: str = "doc") -> None:
        self.docx_parser = docx_parser
        self.input_ext = input_ext

    async def parse(self, content: bytes, record_name: str, config: dict[str, any] | None = None) -> ParseResult:
        if self.docx_parser is None:
            raise ParseError(
                ParseErrorCode.PROVIDER_UNAVAILABLE,
                f"{self.input_ext.upper()} parsing requires a docx_parser; none was configured",
            )
        if self.input_ext == "doc":
            doc_result = await self.convert_doc_to_docx_async(content)
            return await self.docx_parser.parse(doc_result, record_name, config)
        if self.input_ext == "odt":
            from app.modules.parsers.encryption import raise_if_odf_encrypted

            raise_if_odf_encrypted(content)
        with _damaged_file_reported(self.input_ext):
            docx = await convert_with_libreoffice(content, self.input_ext, "docx")
        # The name says DOCX: Docling falls back to it when it cannot tell the format from the bytes.
        return await self.docx_parser.parse(BytesIO(docx), f"{Path(record_name).stem}.docx", config)

    async def convert_doc_to_docx_async(self, binary: bytes) -> BytesIO:
        """Async .doc -> .docx conversion: runs LibreOffice via
        ``asyncio.create_subprocess_exec`` instead of blocking the event loop
        for the whole conversion.
        """
        with unreadable_file_as_parse_error("doc"):
            docx_bytes = await convert_with_libreoffice(binary, "doc", "docx")
        return BytesIO(docx_bytes)
