"""Unit tests for app.modules.parsers.pdf.docling.DoclingProcessor."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_mock_config():
    """Return a minimal config dict for DoclingProcessor."""
    return {}


def _make_mock_logger():
    """Return a mock logger."""
    import logging
    return MagicMock(spec=logging.Logger)


def _make_processor():
    """Create a DoclingProcessor with a mocked converter (bypass real docling init)."""
    with patch("app.modules.parsers.pdf.docling.DocumentConverter") as MockConverter, \
         patch("app.modules.parsers.pdf.docling.PdfFormatOption"), \
         patch("app.modules.parsers.pdf.docling.WordFormatOption"), \
         patch("app.modules.parsers.pdf.docling.MarkdownFormatOption"), \
         patch("app.modules.parsers.pdf.docling.PdfPipelineOptions"), \
         patch("app.modules.parsers.pdf.docling.PyPdfiumDocumentBackend"):
        from app.modules.parsers.pdf.docling import DoclingProcessor
        processor = DoclingProcessor(_make_mock_logger(), _make_mock_config())
    return processor


# ---------------------------------------------------------------------------
# DoclingProcessor.__init__
# ---------------------------------------------------------------------------
class TestDoclingProcessorInit:
    """Tests for DoclingProcessor.__init__() — config setup."""

    def test_init_sets_logger_and_config(self):
        """Constructor stores logger and config, and creates a converter."""
        processor = _make_processor()
        assert processor.logger is not None
        assert processor.config is not None
        assert processor.converter is not None

    def test_init_stores_config(self):
        """Config dict is stored as-is."""
        with patch("app.modules.parsers.pdf.docling.DocumentConverter"), \
             patch("app.modules.parsers.pdf.docling.PdfFormatOption"), \
             patch("app.modules.parsers.pdf.docling.WordFormatOption"), \
             patch("app.modules.parsers.pdf.docling.MarkdownFormatOption"), \
             patch("app.modules.parsers.pdf.docling.PdfPipelineOptions"), \
             patch("app.modules.parsers.pdf.docling.PyPdfiumDocumentBackend"):
            from app.modules.parsers.pdf.docling import DoclingProcessor
            config = {"ocr": True, "model": "fast"}
            processor = DoclingProcessor(_make_mock_logger(), config)
            assert processor.config is config


# ---------------------------------------------------------------------------
# DoclingProcessor.parse_document
# ---------------------------------------------------------------------------
class TestDoclingProcessorParseDocument:
    """Tests for DoclingProcessor.parse_document()."""

    @pytest.mark.asyncio
    async def test_parse_document_success_with_bytes(self):
        """parse_document succeeds with bytes input."""
        processor = _make_processor()

        mock_doc = MagicMock()
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document = mock_doc

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 1), \
             patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_conv_result):
            result = await processor.parse_document("test.pdf", b"fake pdf content")
            assert result is mock_doc

    @pytest.mark.asyncio
    async def test_parse_document_success_with_bytesio(self):
        """parse_document succeeds with BytesIO input."""
        processor = _make_processor()

        mock_doc = MagicMock()
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document = mock_doc

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 1), \
             patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_conv_result):
            content = BytesIO(b"fake pdf content")
            result = await processor.parse_document("test.pdf", content)
            assert result is mock_doc

    @pytest.mark.asyncio
    async def test_parse_document_failure_raises_valueerror(self):
        """parse_document raises ValueError on conversion failure."""
        processor = _make_processor()

        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "failure"

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 1), \
             patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_conv_result):
            with pytest.raises(ValueError, match="Failed to parse document"):
                await processor.parse_document("test.pdf", b"bad content")

    @pytest.mark.asyncio
    async def test_parse_document_creates_document_stream(self):
        """parse_document creates a DocumentStream with correct name."""
        processor = _make_processor()

        mock_doc = MagicMock()
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document = mock_doc

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 1), \
             patch("app.modules.parsers.pdf.docling.DocumentStream") as MockStream, \
             patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_conv_result):
            await processor.parse_document("my_report.pdf", b"content")
            MockStream.assert_called_once()
            call_kwargs = MockStream.call_args
            # Check name= kwarg
            assert call_kwargs.kwargs.get("name") == "my_report.pdf"

    @pytest.mark.asyncio
    async def test_parse_document_bytes_creates_bytesio_stream(self):
        """When bytes are passed, they are wrapped in BytesIO for DocumentStream."""
        processor = _make_processor()

        mock_doc = MagicMock()
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document = mock_doc

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 1), \
             patch("app.modules.parsers.pdf.docling.DocumentStream") as MockStream, \
             patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_conv_result):
            await processor.parse_document("test.pdf", b"raw bytes")
            call_kwargs = MockStream.call_args
            stream_arg = call_kwargs.kwargs.get("stream")
            # Should be a BytesIO instance
            assert isinstance(stream_arg, BytesIO)

    @pytest.mark.asyncio
    async def test_parse_document_bytesio_content_preserved(self):
        """When BytesIO is passed, its content is extracted and wrapped in a new BytesIO."""
        processor = _make_processor()

        mock_doc = MagicMock()
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document = mock_doc

        original_stream = BytesIO(b"pre-wrapped content")

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 1), \
             patch("app.modules.parsers.pdf.docling.DocumentStream") as MockStream, \
             patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_conv_result):
            await processor.parse_document("test.pdf", original_stream)
            call_kwargs = MockStream.call_args
            stream_arg = call_kwargs.kwargs.get("stream")
            assert isinstance(stream_arg, BytesIO)
            assert stream_arg.getvalue() == b"pre-wrapped content"


# ---------------------------------------------------------------------------
# DoclingProcessor.create_blocks
# ---------------------------------------------------------------------------
class TestDoclingProcessorCreateBlocks:
    """Tests for DoclingProcessor.create_blocks()."""

    @pytest.mark.asyncio
    async def test_create_blocks_success(self):
        """create_blocks delegates to DoclingDocToBlocksConverter."""
        processor = _make_processor()
        mock_blocks_container = MagicMock()

        with patch("app.modules.parsers.pdf.docling.DoclingDocToBlocksConverter") as MockBlockConverter:
            mock_converter_instance = MagicMock()
            mock_converter_instance.convert = AsyncMock(return_value=mock_blocks_container)
            MockBlockConverter.return_value = mock_converter_instance

            mock_doc = MagicMock()
            result = await processor.create_blocks(mock_doc)
            assert result is mock_blocks_container
            mock_converter_instance.convert.assert_awaited_once_with(mock_doc, page_number=None)

    @pytest.mark.asyncio
    async def test_create_blocks_with_page_number(self):
        """create_blocks passes page_number to converter."""
        processor = _make_processor()
        mock_blocks_container = MagicMock()

        with patch("app.modules.parsers.pdf.docling.DoclingDocToBlocksConverter") as MockBlockConverter:
            mock_converter_instance = MagicMock()
            mock_converter_instance.convert = AsyncMock(return_value=mock_blocks_container)
            MockBlockConverter.return_value = mock_converter_instance

            mock_doc = MagicMock()
            result = await processor.create_blocks(mock_doc, page_number=5)
            mock_converter_instance.convert.assert_awaited_once_with(mock_doc, page_number=5)

    @pytest.mark.asyncio
    async def test_create_blocks_passes_logger_and_config(self):
        """create_blocks creates DoclingDocToBlocksConverter with correct logger and config."""
        processor = _make_processor()

        with patch("app.modules.parsers.pdf.docling.DoclingDocToBlocksConverter") as MockBlockConverter:
            mock_converter_instance = MagicMock()
            mock_converter_instance.convert = AsyncMock(return_value=MagicMock())
            MockBlockConverter.return_value = mock_converter_instance

            await processor.create_blocks(MagicMock())
            MockBlockConverter.assert_called_once_with(
                logger=processor.logger, config=processor.config
            )

    @pytest.mark.asyncio
    async def test_create_blocks_default_page_number_none(self):
        """Default page_number is None."""
        processor = _make_processor()

        with patch("app.modules.parsers.pdf.docling.DoclingDocToBlocksConverter") as MockBlockConverter:
            mock_converter_instance = MagicMock()
            mock_converter_instance.convert = AsyncMock(return_value=MagicMock())
            MockBlockConverter.return_value = mock_converter_instance

            await processor.create_blocks(MagicMock())
            call_kwargs = mock_converter_instance.convert.call_args
            assert call_kwargs.kwargs.get("page_number") is None


# ---------------------------------------------------------------------------
# DoclingProcessor.load_document — Legacy combined method
# ---------------------------------------------------------------------------
class TestDoclingProcessorLoadDocument:
    """Tests for DoclingProcessor.load_document() — legacy combined method."""

    @pytest.mark.asyncio
    async def test_load_document_calls_parse_then_create(self):
        """load_document calls parse_document then create_blocks."""
        processor = _make_processor()
        mock_doc = MagicMock()
        mock_blocks = MagicMock()

        processor.parse_document = AsyncMock(return_value=mock_doc)
        processor.create_blocks = AsyncMock(return_value=mock_blocks)

        result = await processor.load_document("test.pdf", b"content")
        processor.parse_document.assert_awaited_once_with("test.pdf", b"content")
        processor.create_blocks.assert_awaited_once_with(mock_doc, page_number=None)
        assert result is mock_blocks

    @pytest.mark.asyncio
    async def test_load_document_with_page_number(self):
        """load_document passes page_number to create_blocks."""
        processor = _make_processor()
        mock_doc = MagicMock()
        mock_blocks = MagicMock()

        processor.parse_document = AsyncMock(return_value=mock_doc)
        processor.create_blocks = AsyncMock(return_value=mock_blocks)

        result = await processor.load_document("test.pdf", b"content", page_number=3)
        processor.create_blocks.assert_awaited_once_with(mock_doc, page_number=3)

    @pytest.mark.asyncio
    async def test_load_document_propagates_parse_error(self):
        """If parse_document fails, load_document propagates the error."""
        processor = _make_processor()
        processor.parse_document = AsyncMock(
            side_effect=ValueError("Failed to parse document: failure")
        )
        processor.create_blocks = AsyncMock()

        with pytest.raises(ValueError, match="Failed to parse"):
            await processor.load_document("bad.pdf", b"bad content")
        processor.create_blocks.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_load_document_propagates_create_blocks_error(self):
        """If create_blocks fails, load_document propagates the error."""
        processor = _make_processor()
        mock_doc = MagicMock()

        processor.parse_document = AsyncMock(return_value=mock_doc)
        processor.create_blocks = AsyncMock(
            side_effect=RuntimeError("Block creation failed")
        )

        with pytest.raises(RuntimeError, match="Block creation failed"):
            await processor.load_document("test.pdf", b"content")

    @pytest.mark.asyncio
    async def test_load_document_default_page_number_none(self):
        """Default page_number for load_document is None."""
        processor = _make_processor()
        mock_doc = MagicMock()

        processor.parse_document = AsyncMock(return_value=mock_doc)
        processor.create_blocks = AsyncMock(return_value=MagicMock())

        await processor.load_document("test.pdf", b"content")
        call_kwargs = processor.create_blocks.call_args
        assert call_kwargs.kwargs.get("page_number") is None
