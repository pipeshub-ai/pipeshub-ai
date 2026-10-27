import asyncio
from typing import Any

from app.modules.parsers.epub.epub_reader import read_epub
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
)


class EPUBParser:
    """Parser for EPUB e-books.

    Reads the book's chapters in reading order into one HTML document (see
    :mod:`app.modules.parsers.epub.epub_reader`) and hands it to the configured
    HTML parser, so an EPUB produces the same blocks as the equivalent HTML
    file. LibreOffice is not involved: it can write EPUB but cannot open it.
    """

    def __init__(self, html_parser: IParser | None = None) -> None:
        self.html_parser = html_parser

    async def parse(
        self, content: bytes, record_name: str, config: dict[str, Any] | None = None,
    ) -> ParseResult:
        if self.html_parser is None:
            raise ParseError(
                ParseErrorCode.PROVIDER_UNAVAILABLE,
                "EPUB parsing requires an html_parser; none was configured",
            )
        book = await asyncio.to_thread(read_epub, content)
        result = await self.html_parser.parse(book.to_html().encode("utf-8"), record_name, config)
        result.metadata.update({
            "title": book.metadata.title,
            "authors": book.metadata.authors,
            "language": book.metadata.language,
            "epub_version": book.version,
            "chapter_count": len(book.chapter_bodies),
        })
        return result
