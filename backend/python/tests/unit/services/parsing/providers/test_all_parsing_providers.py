"""Unit tests for all parsing provider implementations."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlocksContainer
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.providers.docling_service_parser import DoclingServiceParser
from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
from app.services.parsing.providers.ocr_parser import OCRParser
from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser
from app.services.parsing.providers.smart_pdf_parser import (
    SmartPDFParser,
    _detect_needs_ocr,
)


def _make_block_container() -> BlocksContainer:
    """Create a minimal valid BlocksContainer for testing."""
    return BlocksContainer(blocks=[], block_groups=[])


class TestDoclingServiceParser:
    """Tests for DoclingServiceParser."""

    @pytest.mark.asyncio
    async def test_parse_success(self):
        mock_client = MagicMock()
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = '{"doc": true}'
        mock_client.parse_pdf_batched = AsyncMock(return_value=mock_doc)

        parser = DoclingServiceParser(mock_client)
        result = await parser.parse(b"pdf content", "test.pdf")

        assert isinstance(result, ParseResult)
        assert result.block_container is None
        assert result.raw_document == '{"doc": true}'
        assert result.provider_used == ParserProvider.DOCLING
        assert result.metadata["record_name"] == "test.pdf"
        mock_client.parse_pdf_batched.assert_awaited_once_with("test.pdf", b"pdf content")

    @pytest.mark.asyncio
    async def test_parse_adds_pdf_extension(self):
        mock_client = MagicMock()
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = "{}"
        mock_client.parse_pdf_batched = AsyncMock(return_value=mock_doc)

        parser = DoclingServiceParser(mock_client)
        result = await parser.parse(b"content", "document")

        mock_client.parse_pdf_batched.assert_awaited_once_with("document.pdf", b"content")
        assert result.metadata["record_name"] == "document"

    @pytest.mark.asyncio
    async def test_parse_fails_when_process_returns_none(self):
        mock_client = MagicMock()
        mock_client.parse_pdf_batched = AsyncMock(return_value=None)

        parser = DoclingServiceParser(mock_client)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "failed to parse" in exc_info.value.message.lower()

    def test_supported_formats(self):
        mock_client = MagicMock()
        parser = DoclingServiceParser(mock_client)
        assert parser.supported_formats() == ["pdf"]


class TestLocalDoclingParser:
    """Tests for LocalDoclingParser."""

    @pytest.mark.asyncio
    async def test_parse_success_pdf(self):
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = '{"conversion": "result"}'
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_doc)

        parser = LocalDoclingParser(mock_processor)
        result = await parser.parse(b"pdf content", "document.pdf")

        assert isinstance(result, ParseResult)
        assert result.block_container is None
        assert result.raw_document == '{"conversion": "result"}'
        assert result.provider_used == ParserProvider.DOCLING
        assert result.metadata["record_name"] == "document.pdf"
        mock_processor.parse_document.assert_called_once_with("document.pdf", b"pdf content")

    @pytest.mark.asyncio
    async def test_parse_success_docx(self):
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = "{}"
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_doc)

        parser = LocalDoclingParser(mock_processor)
        result = await parser.parse(b"docx content", "document.docx")

        mock_processor.parse_document.assert_called_once_with("document.docx", b"docx content")
        assert result.provider_used == ParserProvider.DOCLING

    @pytest.mark.asyncio
    async def test_parse_normalizes_doc_to_docx(self):
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = "{}"
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_doc)

        parser = LocalDoclingParser(mock_processor)
        await parser.parse(b"doc content", "legacy.doc")

        mock_processor.parse_document.assert_called_once_with("legacy.docx", b"doc content")

    @pytest.mark.asyncio
    async def test_parse_normalizes_ppt_to_pptx(self):
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = "{}"
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_doc)

        parser = LocalDoclingParser(mock_processor)
        await parser.parse(b"ppt content", "presentation.ppt")

        mock_processor.parse_document.assert_called_once_with("presentation.pptx", b"ppt content")

    @pytest.mark.asyncio
    async def test_parse_fails_when_parse_document_returns_none(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=None)

        parser = LocalDoclingParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "empty result" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_parse_fails_when_parse_document_returns_false(self):
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=False)

        parser = LocalDoclingParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED

    @pytest.mark.asyncio
    async def test_parse_maps_conversion_error_to_unsupported_format(self):
        from docling.exceptions import ConversionError

        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(
            side_effect=ConversionError(
                "File format not allowed: ._free-software-support-agreement.docx"
            )
        )

        parser = LocalDoclingParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "._free-software-support-agreement.docx")

        assert exc_info.value.code == ParseErrorCode.UNSUPPORTED_FORMAT
        assert "File format not allowed" in exc_info.value.message
        assert exc_info.value.details["record_name"] == "._free-software-support-agreement.docx"

    @pytest.mark.asyncio
    async def test_parse_maps_generic_conversion_error_to_parse_failed(self):
        from docling.exceptions import ConversionError

        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(
            side_effect=ConversionError("Invalid input: corrupted document stream")
        )

        parser = LocalDoclingParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "corrupted.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "Invalid input" in exc_info.value.message
        assert exc_info.value.details["record_name"] == "corrupted.pdf"
        assert exc_info.value.__cause__ is not None

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

    @pytest.mark.asyncio
    async def test_processor_crash_becomes_parse_error_so_smart_parser_falls_back(self) -> None:
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(side_effect=ValueError("bad xref"))

        parser = PdfPlumberParser(mock_processor)
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"content", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
        assert "bad xref" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_skip_table_enrichment_skips_llm(self) -> None:
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=[])
        mock_processor.create_blocks = AsyncMock(return_value=_make_block_container())

        parser = PdfPlumberParser(mock_processor)
        await parser.parse(b"content", "test.pdf", {"skip_table_enrichment": True})

        mock_processor.create_blocks.assert_called_once_with([], skip_llm_enrichment=True)

    def test_supported_formats(self):
        mock_processor = MagicMock()
        parser = PdfPlumberParser(mock_processor)
        assert parser.supported_formats() == ["pdf"]


class TestSmartPDFParser:
    """Tests for SmartPDFParser."""

    def test_single_dominant_image_page_routes_document_to_ocr(self) -> None:
        """One dominant-image page must not be hidden by an aggregate ratio."""
        pages = [MagicMock() for _ in range(5)]
        pdf = MagicMock()
        pdf.pages = pages
        pdf_context = MagicMock()
        pdf_context.__enter__.return_value = pdf

        with (
            patch(
                "app.services.parsing.providers.smart_pdf_parser.pdfplumber.open",
                return_value=pdf_context,
            ),
            patch(
                "app.services.parsing.providers.smart_pdf_parser._sample_page_indices",
                return_value=[0, 1, 2, 3, 4],
            ),
            patch.object(
                OCRStrategy,
                "has_dominant_image_with_limited_text",
                side_effect=[False, False, True, False, False],
            ),
        ):
            assert _detect_needs_ocr(b"pdf content") == (True, 5)

    def test_single_general_ocr_page_keeps_existing_threshold(self) -> None:
        """A single non-dominant OCR signal does not route the whole document."""
        pages = [MagicMock() for _ in range(5)]
        pdf = MagicMock()
        pdf.pages = pages
        pdf_context = MagicMock()
        pdf_context.__enter__.return_value = pdf

        with (
            patch(
                "app.services.parsing.providers.smart_pdf_parser.pdfplumber.open",
                return_value=pdf_context,
            ),
            patch(
                "app.services.parsing.providers.smart_pdf_parser._sample_page_indices",
                return_value=[0, 1, 2, 3, 4],
            ),
            patch.object(
                OCRStrategy,
                "has_dominant_image_with_limited_text",
                return_value=False,
            ),
            patch.object(
                OCRStrategy,
                "needs_ocr",
                side_effect=[False, False, True, False, False],
            ),
        ):
            assert _detect_needs_ocr(b"pdf content") == (False, 5)

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

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(True, 3)):
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

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 3)):
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

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 3)):
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

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 3)):
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

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 3)):
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

        with patch("app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 3)):
            with pytest.raises(ParseError) as exc_info:
                await parser.parse(b"bad data", "test.pdf")

        assert exc_info.value.code == ParseErrorCode.INVALID_INPUT
        mock_ocr.parse.assert_not_called()

    def test_supported_formats(self):
        mock_primary = MagicMock()
        mock_ocr = MagicMock()
        parser = SmartPDFParser(mock_primary, mock_ocr)
        assert parser.supported_formats() == ["pdf"]


def _result(provider: ParserProvider) -> ParseResult:
    return ParseResult(block_container=_make_block_container(), provider_used=provider)


def _routed_parsers(
    docling_error: ParseError | None = None, layout_error: ParseError | None = None
) -> tuple[MagicMock, MagicMock, MagicMock]:
    docling, layout, ocr = MagicMock(), MagicMock(), MagicMock()
    docling.parse = AsyncMock(side_effect=docling_error, return_value=_result(ParserProvider.DOCLING))
    layout.parse = AsyncMock(side_effect=layout_error, return_value=_result(ParserProvider.DEFAULT))
    ocr.parse = AsyncMock(return_value=_result(ParserProvider.DEFAULT))
    return docling, layout, ocr


class TestSmartPDFParserPageRouting:
    """Docling takes text PDFs of up to PDF_DOCLING_MAX_PAGES (30) pages; PdfPlumber the rest."""

    @pytest.mark.parametrize(
        ("page_count", "env", "uses_docling"),
        [
            (1, {}, True),
            (30, {}, True),
            (31, {}, False),
            (300, {}, False),
            (None, {}, True),
            (11, {"PDF_DOCLING_MAX_PAGES": "10"}, False),
            (1, {"PDF_DOCLING_MAX_PAGES": "0"}, False),
            (30, {"PDF_DOCLING_MAX_PAGES": ""}, True),
            (31, {"PDF_DOCLING_MAX_PAGES": "abc"}, False),
            (30, {"PDF_DOCLING_MAX_PAGES": "-1"}, True),
        ],
        ids=["1", "30", "31", "300", "unknown", "11 of 10", "limit 0", "empty limit", "bad limit", "negative limit"],
    )
    @pytest.mark.asyncio
    async def test_routes_by_page_count(
        self, monkeypatch: pytest.MonkeyPatch, page_count: int | None, env: dict[str, str], uses_docling: bool
    ) -> None:
        monkeypatch.delenv("PDF_DOCLING_MAX_PAGES", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        docling, layout, ocr = _routed_parsers()
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr",
            return_value=(False, page_count),
        ):
            await parser.parse(b"%PDF", "a.pdf")

        assert docling.parse.await_count == (1 if uses_docling else 0)
        assert layout.parse.await_count == (0 if uses_docling else 1)
        ocr.parse.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_pdf_over_doclings_size_limit_goes_to_the_layout_parser(self) -> None:
        docling, layout, ocr = _routed_parsers()
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 5)
        ), patch("app.services.docling.client.MAX_PDF_BYTES", 3):
            await parser.parse(b"%PDF", "a.pdf")

        docling.parse.assert_not_called()
        layout.parse.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_long_scan_still_goes_to_ocr(self) -> None:
        docling, layout, ocr = _routed_parsers()
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(True, 100)
        ):
            await parser.parse(b"%PDF", "a.pdf")

        ocr.parse.assert_awaited_once()
        docling.parse.assert_not_called()
        layout.parse.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_docling_failure_goes_to_the_layout_parser_before_ocr(self) -> None:
        docling, layout, ocr = _routed_parsers(docling_error=ParseError(ParseErrorCode.PARSE_FAILED, "no"))
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 10)
        ):
            result = await parser.parse(b"%PDF", "a.pdf")

        layout.parse.assert_awaited_once()
        ocr.parse.assert_not_called()
        assert result.provider_used == ParserProvider.DEFAULT

    @pytest.mark.asyncio
    async def test_ocr_is_the_last_resort_after_both_parsers_fail(self) -> None:
        failed = ParseError(ParseErrorCode.PARSE_FAILED, "no")
        docling, layout, ocr = _routed_parsers(docling_error=failed, layout_error=failed)
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 10)
        ):
            await parser.parse(b"%PDF", "a.pdf")

        ocr.parse.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failure_about_the_file_is_not_retried_by_another_parser(self) -> None:
        docling, layout, ocr = _routed_parsers(docling_error=ParseError(ParseErrorCode.EMPTY_CONTENT, "empty"))
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 10)
        ), pytest.raises(ParseError) as raised:
            await parser.parse(b"%PDF", "a.pdf")

        assert raised.value.code == ParseErrorCode.EMPTY_CONTENT
        layout.parse.assert_not_called()
        ocr.parse.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unreachable_docling_hands_the_pdf_to_the_layout_parser(self) -> None:
        from app.services.docling.client import DoclingUnavailableError

        docling, layout, ocr = _routed_parsers(docling_error=DoclingUnavailableError("down"))
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 10)
        ):
            result = await parser.parse(b"%PDF", "a.pdf")

        layout.parse.assert_awaited_once()
        ocr.parse.assert_not_called()
        assert result.provider_used == ParserProvider.DEFAULT

    @pytest.mark.asyncio
    async def test_a_layout_failure_with_docling_down_is_not_sent_to_ocr(self) -> None:
        from app.services.docling.client import DoclingUnavailableError

        docling, layout, ocr = _routed_parsers(
            docling_error=DoclingUnavailableError("down"),
            layout_error=ParseError(ParseErrorCode.PARSE_FAILED, "no"),
        )
        parser = SmartPDFParser(docling, ocr, large_pdf_parser=layout)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 10)
        ), pytest.raises(ParseError):
            await parser.parse(b"%PDF", "a.pdf")

        ocr.parse.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_a_layout_parser_an_unreachable_docling_is_raised(self) -> None:
        """The explicit Docling provider has no other parser to hand the file to: it retries."""
        from app.services.docling.client import DoclingUnavailableError

        docling, _, ocr = _routed_parsers(docling_error=DoclingUnavailableError("down"))
        parser = SmartPDFParser(docling, ocr)

        with patch(
            "app.services.parsing.providers.smart_pdf_parser._detect_needs_ocr", return_value=(False, 10)
        ), pytest.raises(DoclingUnavailableError):
            await parser.parse(b"%PDF", "a.pdf")

        ocr.parse.assert_not_called()

    def test_a_file_pdfplumber_cannot_open_has_no_page_count(self) -> None:
        with patch(
            "app.services.parsing.providers.smart_pdf_parser.pdfplumber.open", side_effect=ValueError("bad")
        ):
            assert _detect_needs_ocr(b"%PDF") == (False, None)

    def test_the_page_count_comes_from_the_ocr_pass_without_another_open(self) -> None:
        pdf = MagicMock()
        pdf.pages = [MagicMock() for _ in range(42)]
        pdf_context = MagicMock()
        pdf_context.__enter__.return_value = pdf

        with patch(
            "app.services.parsing.providers.smart_pdf_parser.pdfplumber.open", return_value=pdf_context
        ) as opened, patch.object(
            OCRStrategy, "has_dominant_image_with_limited_text", return_value=False
        ), patch.object(OCRStrategy, "needs_ocr", return_value=False):
            assert _detect_needs_ocr(b"%PDF") == (False, 42)

        assert opened.call_count == 1
