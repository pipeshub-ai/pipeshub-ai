"""Unit tests for all parsing provider implementations."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlocksContainer
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult, ParserProvider
from app.services.parsing.providers.docling_service_parser import DoclingServiceParser
from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
from app.services.parsing.providers.ocr_parser import OCRParser
from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser


def _make_block_container() -> BlocksContainer:
    """Create a minimal valid BlocksContainer for testing."""
    return BlocksContainer(blocks=[], block_groups=[])


class TestDoclingServiceParser:
    """Tests for DoclingServiceParser."""

    @pytest.mark.asyncio
    async def test_parse_success(self):
        mock_client = MagicMock()
        mock_client.parse_pdf = AsyncMock(return_value={"parsed": "data"})
        mock_client.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = DoclingServiceParser(mock_client)
        result = await parser.parse(b"pdf content", "test.pdf")

        assert isinstance(result, ParseResult)
        assert result.provider_used == ParserProvider.DOCLING
        assert result.metadata["record_name"] == "test.pdf"
        mock_client.parse_pdf.assert_called_once_with("test.pdf", b"pdf content")
        mock_client.create_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_adds_pdf_extension(self):
        mock_client = MagicMock()
        mock_client.parse_pdf = AsyncMock(return_value={"parsed": "data"})
        mock_client.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = DoclingServiceParser(mock_client)
        result = await parser.parse(b"content", "document")

        mock_client.parse_pdf.assert_called_once_with("document.pdf", b"content")
        assert result.metadata["record_name"] == "document"

    @pytest.mark.asyncio
    async def test_parse_fails_when_parse_pdf_returns_none(self):
        mock_client = MagicMock()
        mock_client.parse_pdf = AsyncMock(return_value=None)

        parser = DoclingServiceParser(mock_client)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "failed to parse" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_parse_fails_when_create_blocks_returns_none(self):
        mock_client = MagicMock()
        mock_client.parse_pdf = AsyncMock(return_value={"data": "value"})
        mock_client.create_blocks = AsyncMock(return_value=None)

        parser = DoclingServiceParser(mock_client)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "failed to create blocks" in exc_info.value.message.lower()

    def test_supported_formats(self):
        mock_client = MagicMock()
        parser = DoclingServiceParser(mock_client)
        assert parser.supported_formats() == ["pdf"]


class TestLocalDoclingParser:
    """Tests for LocalDoclingParser."""

    @pytest.mark.asyncio
    async def test_parse_success_pdf(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"conversion": "result"})
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = LocalDoclingParser(mock_processor)
        result = await parser.parse(b"pdf content", "document.pdf")

        assert isinstance(result, ParseResult)
        assert result.provider_used == ParserProvider.DOCLING
        assert result.metadata["record_name"] == "document.pdf"
        mock_processor.parse_document.assert_called_once_with("document.pdf", b"pdf content")

    @pytest.mark.asyncio
    async def test_parse_success_docx(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"conversion": "result"})
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = LocalDoclingParser(mock_processor)
        result = await parser.parse(b"docx content", "document.docx")

        mock_processor.parse_document.assert_called_once_with("document.docx", b"docx content")
        assert result.provider_used == ParserProvider.DOCLING

    @pytest.mark.asyncio
    async def test_parse_normalizes_doc_to_docx(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"conversion": "result"})
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = LocalDoclingParser(mock_processor)
        await parser.parse(b"doc content", "legacy.doc")

        mock_processor.parse_document.assert_called_once_with("legacy.docx", b"doc content")

    @pytest.mark.asyncio
    async def test_parse_normalizes_ppt_to_pptx(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"conversion": "result"})
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = LocalDoclingParser(mock_processor)
        await parser.parse(b"ppt content", "presentation.ppt")

        mock_processor.parse_document.assert_called_once_with("presentation.pptx", b"ppt content")

    @pytest.mark.asyncio
    async def test_parse_fails_when_create_blocks_returns_none(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"data": "value"})
        mock_processor.create_blocks = AsyncMock(return_value=None)

        parser = LocalDoclingParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "empty result" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_parse_fails_when_create_blocks_returns_false(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"data": "value"})
        mock_processor.create_blocks = AsyncMock(return_value=False)

        parser = LocalDoclingParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED

    def test_supported_formats(self):
        mock_processor = MagicMock()
        parser = LocalDoclingParser(mock_processor)
        formats = parser.supported_formats()
        assert "pdf" in formats
        assert "docx" in formats
        assert "doc" in formats
        assert "pptx" in formats
        assert "ppt" in formats
        assert "md" in formats
        assert "mdx" in formats


class TestOCRParser:
    """Tests for OCRParser."""

    @pytest.mark.asyncio
    async def test_parse_success_vlm_ocr(self):
        mock_handler = MagicMock()
        mock_handler.provider = "vlmOCR"
        mock_handler.process_document = AsyncMock(
            return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1\nContent here"},
                    {"page_number": 2, "markdown": "# Page 2\nMore content"},
                ]
            }
        )

        mock_md_parser = MagicMock()
        mock_md_parser.parse_to_blocks = AsyncMock(return_value=_make_block_container())

        parser = OCRParser(mock_handler, mock_md_parser)
        result = await parser.parse(b"pdf content", "scanned.pdf")

        assert isinstance(result, ParseResult)
        assert result.metadata["ocr_provider"] == "vlmOCR"
        assert result.metadata["record_name"] == "scanned.pdf"
        mock_handler.process_document.assert_called_once_with(b"pdf content")
        assert mock_md_parser.parse_to_blocks.call_count == 2

    @pytest.mark.asyncio
    async def test_parse_skips_empty_pages(self):
        mock_handler = MagicMock()
        mock_handler.provider = "vlmOCR"
        mock_handler.process_document = AsyncMock(
            return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1"},
                    {"page_number": 2, "markdown": "   "},
                    {"page_number": 3, "markdown": "# Page 3"},
                ]
            }
        )

        mock_md_parser = MagicMock()
        mock_md_parser.parse_to_blocks = AsyncMock(return_value=_make_block_container())

        parser = OCRParser(mock_handler, mock_md_parser)
        await parser.parse(b"content", "test.pdf")

        assert mock_md_parser.parse_to_blocks.call_count == 2

    @pytest.mark.asyncio
    async def test_parse_fails_with_unsupported_provider(self):
        mock_handler = MagicMock()
        mock_handler.provider = "unknown_provider"
        mock_handler.process_document = AsyncMock(return_value={})

        mock_md_parser = MagicMock()
        parser = OCRParser(mock_handler, mock_md_parser)

        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE
        assert "unsupported ocr provider" in exc_info.value.message.lower()

    def test_supported_formats(self):
        mock_handler = MagicMock()
        mock_md_parser = MagicMock()
        parser = OCRParser(mock_handler, mock_md_parser)
        assert parser.supported_formats() == ["pdf"]


class TestPdfPlumberParser:
    """Tests for PdfPlumberParser."""

    @pytest.mark.asyncio
    async def test_parse_success(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"parsed": "data"})
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = PdfPlumberParser(mock_processor)
        result = await parser.parse(b"pdf content", "test.pdf")

        assert isinstance(result, ParseResult)
        assert result.provider_used == ParserProvider.DEFAULT
        assert result.metadata["record_name"] == "test.pdf"
        mock_processor.parse_document.assert_called_once_with("test.pdf", b"pdf content")
        mock_processor.create_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_adds_pdf_extension(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"data": "value"})
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = PdfPlumberParser(mock_processor)
        await parser.parse(b"content", "document")

        mock_processor.parse_document.assert_called_once_with("document.pdf", b"content")

    @pytest.mark.asyncio
    async def test_parse_fails_when_create_blocks_returns_none(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value={"data": "value"})
        mock_processor.create_blocks = AsyncMock(return_value=None)

        parser = PdfPlumberParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "empty result" in exc_info.value.message.lower()

    def test_supported_formats(self):
        mock_processor = MagicMock()
        parser = PdfPlumberParser(mock_processor)
        assert parser.supported_formats() == ["pdf"]


class TestSmartPDFParser:
    """Tests for SmartPDFParser."""

    @pytest.mark.asyncio
    async def test_parse_uses_ocr_when_detected(self):
        mock_primary = MagicMock()
        mock_ocr = MagicMock()
        mock_ocr.parse = AsyncMock(
            return_value=ParseResult(
                block_container=_make_block_container(),
                metadata={"ocr_provider": "vlm_ocr"},
            )
        )

        parser = SmartPDFParser(mock_primary, mock_ocr)

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=True):
            result = await parser.parse(b"scanned content", "scanned.pdf")

        mock_ocr.parse.assert_called_once_with(b"scanned content", "scanned.pdf", None)
        mock_primary.parse.assert_not_called()
        assert result.metadata["ocr_provider"] == "vlm_ocr"

    @pytest.mark.asyncio
    async def test_parse_uses_primary_when_not_scanned(self):
        mock_primary = MagicMock()
        mock_primary.parse = AsyncMock(
            return_value=ParseResult(
                block_container=_make_block_container(),
                provider_used=ParserProvider.DOCLING,
                metadata={"record_name": "regular.pdf"},
            )
        )
        mock_ocr = MagicMock()

        parser = SmartPDFParser(mock_primary, mock_ocr)

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=False):
            result = await parser.parse(b"regular content", "regular.pdf")

        mock_primary.parse.assert_called_once_with(b"regular content", "regular.pdf", None)
        mock_ocr.parse.assert_not_called()
        assert result.provider_used == ParserProvider.DOCLING

    @pytest.mark.asyncio
    async def test_parse_falls_back_to_ocr_on_primary_failure(self):
        mock_primary = MagicMock()
        mock_primary.parse = AsyncMock(
            side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "Primary parser failed")
        )
        mock_ocr = MagicMock()
        mock_ocr.parse = AsyncMock(
            return_value=ParseResult(
                block_container=_make_block_container(),
                metadata={"ocr_provider": "vlm_ocr"},
            )
        )

        parser = SmartPDFParser(mock_primary, mock_ocr)

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=False):
            result = await parser.parse(b"content", "test.pdf")

        mock_primary.parse.assert_called_once()
        mock_ocr.parse.assert_called_once_with(b"content", "test.pdf", None)
        assert result.metadata["ocr_provider"] == "vlm_ocr"

    @pytest.mark.asyncio
    async def test_parse_does_not_fallback_on_unsupported_format_error(self):
        mock_primary = MagicMock()
        mock_primary.parse = AsyncMock(
            side_effect=ParseError(ParseErrorCode.UNSUPPORTED_FORMAT, "Format not supported")
        )
        mock_ocr = MagicMock()

        parser = SmartPDFParser(mock_primary, mock_ocr)

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=False):
            with pytest.raises(ParseError) as exc_info:
                await parser.parse(b"content", "test.xyz")

        assert exc_info.value.code == ParseErrorCode.UNSUPPORTED_FORMAT
        mock_ocr.parse.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_does_not_fallback_on_empty_content_error(self):
        mock_primary = MagicMock()
        mock_primary.parse = AsyncMock(
            side_effect=ParseError(ParseErrorCode.EMPTY_CONTENT, "Empty content")
        )
        mock_ocr = MagicMock()

        parser = SmartPDFParser(mock_primary, mock_ocr)

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=False):
            with pytest.raises(ParseError) as exc_info:
                await parser.parse(b"", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT
        mock_ocr.parse.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_does_not_fallback_on_invalid_input_error(self):
        mock_primary = MagicMock()
        mock_primary.parse = AsyncMock(
            side_effect=ParseError(ParseErrorCode.INVALID_INPUT, "Invalid input")
        )
        mock_ocr = MagicMock()

        parser = SmartPDFParser(mock_primary, mock_ocr)

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=False):
            with pytest.raises(ParseError) as exc_info:
                await parser.parse(b"bad data", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.INVALID_INPUT
        mock_ocr.parse.assert_not_called()

    def test_supported_formats(self):
        mock_primary = MagicMock()
        mock_ocr = MagicMock()
        parser = SmartPDFParser(mock_primary, mock_ocr)
        assert parser.supported_formats() == ["pdf"]
