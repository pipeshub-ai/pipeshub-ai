"""Unit tests for app.modules.parsers.epub.epub_parser.EPUBParser.

Reading real books is covered in test_epub_reader.py; these pin the wiring.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlocksContainer
from app.modules.parsers.epub.epub_parser import EPUBParser
from app.modules.parsers.epub.epub_reader import EpubBook, EpubMetadata
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult


def _book() -> EpubBook:
    return EpubBook(
        metadata=EpubMetadata(title="T", authors=["A"], language="en"),
        chapter_bodies=["<p>one</p>", "<p>two</p>"],
        version="3.0",
    )


def _html_parser() -> MagicMock:
    parser = MagicMock()
    parser.parse = AsyncMock(
        return_value=ParseResult(
            block_container=BlocksContainer(blocks=[], block_groups=[]),
            metadata={"record_name": "book.epub"},
        )
    )
    return parser


class TestEPUBParser:
    def test_default_html_parser_is_none(self) -> None:
        assert EPUBParser().html_parser is None

    async def test_raises_when_no_html_parser_configured(self) -> None:
        with pytest.raises(ParseError) as exc_info:
            await EPUBParser().parse(b"data", "book.epub")
        assert exc_info.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE

    async def test_hands_the_book_to_the_html_parser_as_one_utf8_document(self) -> None:
        html_parser = _html_parser()
        with patch("app.modules.parsers.epub.epub_parser.read_epub", return_value=_book()) as read:
            result = await EPUBParser(html_parser).parse(b"epub bytes", "book.epub", {"key": "val"})

        read.assert_called_once_with(b"epub bytes")
        content, name, config = html_parser.parse.call_args.args
        assert content == _book().to_html().encode("utf-8")
        assert b'<meta charset="utf-8">' in content
        assert content.index(b"one") < content.index(b"two")
        assert (name, config) == ("book.epub", {"key": "val"})
        assert result.metadata == {
            "record_name": "book.epub",
            "title": "T",
            "authors": ["A"],
            "language": "en",
            "epub_version": "3.0",
            "chapter_count": 2,
        }

    async def test_a_book_that_cannot_be_read_never_reaches_the_html_parser(self) -> None:
        html_parser = _html_parser()
        with pytest.raises(ParseError):
            await EPUBParser(html_parser).parse(b"not a zip", "book.epub")
        html_parser.parse.assert_not_called()

    def test_does_not_depend_on_libreoffice_or_pdf_parsing(self) -> None:
        import app.modules.parsers.epub.epub_parser as epub_parser_module

        names = dir(epub_parser_module)
        assert "convert_with_libreoffice" not in names
        assert "fitz" not in names and "pymupdf" not in names
