"""Unit tests for app.modules.parsers.epub.epub_parser.EPUBParser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.epub.epub_parser import EPUBParser
from app.services.parsing.interface import ParseError, ParseErrorCode


class TestEPUBParserInit:
    def test_default_pdf_parser_is_none(self):
        parser = EPUBParser()
        assert parser.pdf_parser is None

    def test_stores_provided_pdf_parser(self):
        mock_inner = MagicMock()
        parser = EPUBParser(pdf_parser=mock_inner)
        assert parser.pdf_parser is mock_inner


class TestParse:
    @pytest.mark.asyncio
    async def test_raises_when_no_pdf_parser_configured(self):
        parser = EPUBParser()
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"data", "book.epub")
        assert exc_info.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_delegates_to_pdf_parser_with_pdf_named_record(self):
        mock_pdf_parser = AsyncMock()
        mock_result = MagicMock()
        mock_pdf_parser.parse.return_value = mock_result

        parser = EPUBParser(pdf_parser=mock_pdf_parser)

        with patch.object(
            parser, "convert_epub_to_pdf_async", AsyncMock(return_value=b"pdf bytes")
        ) as mock_convert:
            result = await parser.parse(b"epub bytes", "book.epub", {"key": "val"})

        mock_convert.assert_called_once_with(b"epub bytes")
        mock_pdf_parser.parse.assert_called_once_with(b"pdf bytes", "book.pdf", {"key": "val"})
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_delegates_without_config(self):
        mock_pdf_parser = AsyncMock()
        mock_pdf_parser.parse.return_value = MagicMock()
        parser = EPUBParser(pdf_parser=mock_pdf_parser)

        with patch.object(
            parser, "convert_epub_to_pdf_async", AsyncMock(return_value=b"pdf bytes")
        ):
            await parser.parse(b"data", "name.epub")

        args, kwargs = mock_pdf_parser.parse.call_args
        assert args[0] == b"pdf bytes"
        assert args[1] == "name.pdf"
        assert args[2] is None

    @pytest.mark.asyncio
    async def test_falls_back_to_converted_pdf_when_record_name_empty(self):
        mock_pdf_parser = AsyncMock()
        mock_pdf_parser.parse.return_value = MagicMock()
        parser = EPUBParser(pdf_parser=mock_pdf_parser)

        with patch.object(
            parser, "convert_epub_to_pdf_async", AsyncMock(return_value=b"pdf bytes")
        ):
            await parser.parse(b"data", "")

        args, _kwargs = mock_pdf_parser.parse.call_args
        assert args[1] == "converted.pdf"

    @pytest.mark.asyncio
    async def test_never_imports_pymupdf(self):
        """EPUBParser must delegate PDF parsing entirely; it must not import
        fitz/PyMuPDF nor call Docling/pdfplumber directly."""
        import app.modules.parsers.epub.epub_parser as epub_parser_module

        assert "fitz" not in dir(epub_parser_module)
        assert "pymupdf" not in dir(epub_parser_module)


class TestConvertEpubToPdfAsync:
    @pytest.mark.asyncio
    async def test_calls_convert_with_libreoffice(self):
        parser = EPUBParser()
        with patch(
            "app.modules.parsers.epub.epub_parser.convert_with_libreoffice",
            AsyncMock(return_value=b"pdf output"),
        ) as mock_convert:
            result = await parser.convert_epub_to_pdf_async(b"epub input")

        mock_convert.assert_called_once_with(b"epub input", "epub", "pdf")
        assert result == b"pdf output"
