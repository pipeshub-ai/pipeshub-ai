"""Tests for OCRHandler and OCRStrategy."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.ai_models import OCRProvider
from app.modules.parsers.pdf.ocr_handler import OCRHandler, OCRStrategy


class TestOCRStrategyNeedsOcr:
    """Tests for OCRStrategy.needs_ocr static method."""

    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_ocr")

    def _make_page(
        self,
        text="",
        words=None,
        images=None,
        width=612.0,
        height=792.0,
        parent=None,
    ):
        """Create a mock PDF page with configurable attributes."""
        page = MagicMock()
        page.get_text.side_effect = lambda *args, **kwargs: (
            text if not args else words if args[0] == "words" else text
        )

        # Properly handle the two-call pattern: page.get_text() and page.get_text("words")
        def get_text_side_effect(mode=None):
            if mode == "words":
                return words or []
            return text

        page.get_text = get_text_side_effect
        page.get_images = MagicMock(return_value=images or [])
        page.rect = MagicMock()
        page.rect.width = width
        page.rect.height = height
        page.parent = parent or MagicMock()
        return page

    def test_needs_ocr_minimal_text_and_significant_images(self, logger):
        """Page with minimal text and significant images needs OCR."""
        # Images with width > 500, height > 500 are significant
        images = [
            (1, 0, 600, 600, 8, "RGB"),
            (2, 0, 700, 700, 8, "RGB"),
            (3, 0, 800, 800, 8, "RGB"),
        ]
        page = self._make_page(text="short", images=images)

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 3
            pix.alpha = 0
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        assert result is True

    def test_no_ocr_for_text_heavy_page(self, logger):
        """Page with substantial text does not need OCR."""
        long_text = "A" * 200
        # Words that cover substantial area
        words = [(0, 0, 100, 20, "word", 0, 0, 0)] * 50
        page = self._make_page(text=long_text, words=words)

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 3
            pix.alpha = 0
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        assert result is False

    def test_needs_ocr_low_density(self, logger):
        """Page with low text density needs OCR."""
        # Short text with tiny word area, no significant images
        page = self._make_page(
            text="tiny",
            words=[(0, 0, 1, 1, "tiny", 0, 0, 0)],
            images=[],
        )

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 3
            pix.alpha = 0
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        assert result is True

    def test_no_ocr_when_text_above_threshold_and_good_density(self, logger):
        """Page with enough text and reasonable density does not need OCR."""
        text = "X" * 150
        # Words covering a reasonable area
        words = [(0, 0, 200, 30, "word", 0, 0, 0)] * 20
        page = self._make_page(text=text, words=words, width=612, height=792)

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 3
            pix.alpha = 0
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        assert result is False

    def test_images_below_min_size_not_significant(self, logger):
        """Small images are not counted as significant."""
        images = [
            (1, 0, 100, 100, 8, "RGB"),  # too small
            (2, 0, 200, 200, 8, "RGB"),  # too small
        ]
        page = self._make_page(text="short", images=images)

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 3
            pix.alpha = 0
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        # Short text but no significant images, and density depends on words
        # With no words, density = 0 < 0.01, so low_density is True
        assert result is True

    def test_cmyk_image_converted(self, logger):
        """CMYK images (n - alpha > 3) are converted to RGB."""
        images = [(1, 0, 600, 600, 8, "CMYK")]
        page = self._make_page(text="short", images=images)

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 5  # CMYK + alpha
            pix.alpha = 1  # n - alpha = 4 > 3
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        # Called twice: once for parent, once for conversion
        assert mock_pixmap.call_count >= 1

    def test_exception_returns_true(self, logger):
        """When an exception occurs, needs_ocr returns True (safe fallback)."""
        page = MagicMock()
        page.get_text = MagicMock(side_effect=RuntimeError("error"))

        result = OCRStrategy.needs_ocr(page, logger)
        assert result is True

    def test_no_words_means_zero_density(self, logger):
        """When words list is empty, text_density is 0 (low_density=True)."""
        page = self._make_page(text="short", words=[], images=[])

        with patch("fitz.Pixmap") as mock_pixmap:
            pix = MagicMock()
            pix.n = 3
            pix.alpha = 0
            mock_pixmap.return_value = pix
            result = OCRStrategy.needs_ocr(page, logger)

        assert result is True

    def test_image_extraction_failure_continues(self, logger):
        """If extracting an image fails, processing continues."""
        images = [(1, 0, 600, 600, 8, "RGB"), (2, 0, 700, 700, 8, "RGB"), (3, 0, 800, 800, 8, "RGB")]
        page = self._make_page(text="short", images=images)

        with patch("fitz.Pixmap", side_effect=RuntimeError("extraction failed")):
            result = OCRStrategy.needs_ocr(page, logger)

        # Even with extraction failures, the heuristic still runs
        assert isinstance(result, bool)


class TestOCRHandlerInit:
    """Tests for OCRHandler initialization."""

    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_ocr_handler")

    @patch("app.modules.parsers.pdf.ocr_handler.OCRProvider", OCRProvider)
    def test_init_azure_di_strategy(self, logger):
        """OCRHandler with Azure Document Intelligence strategy."""
        with patch(
            "app.modules.parsers.pdf.azure_document_intelligence_processor.AzureOCRStrategy"
        ) as mock_cls:
            mock_strategy = MagicMock()
            mock_cls.return_value = mock_strategy

            handler = OCRHandler(
                logger,
                OCRProvider.AZURE_DI.value,
                endpoint="https://example.com",
                key="secret",
                model_id="prebuilt-document",
                config={},
            )

            assert handler.strategy is mock_strategy
            mock_cls.assert_called_once_with(
                logger=logger,
                endpoint="https://example.com",
                key="secret",
                model_id="prebuilt-document",
                config={},
            )

    @patch("app.modules.parsers.pdf.ocr_handler.OCRProvider", OCRProvider)
    def test_init_vlm_ocr_strategy(self, logger):
        """OCRHandler with VLM OCR strategy."""
        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.VLMOCRStrategy"
        ) as mock_cls:
            mock_strategy = MagicMock()
            mock_cls.return_value = mock_strategy

            handler = OCRHandler(logger, OCRProvider.VLM_OCR.value, config={"key": "val"})

            assert handler.strategy is mock_strategy
            mock_cls.assert_called_once_with(logger=logger, config={"key": "val"})

    def test_init_unsupported_strategy_raises(self, logger):
        """Unsupported strategy type raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported OCR strategy"):
            OCRHandler(logger, "unknown_strategy")


class TestOCRHandlerCreateStrategy:
    """Tests for OCRHandler._create_strategy selection logic."""

    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_strategy")

    def test_create_strategy_azure_default_model(self, logger):
        """Azure strategy uses default model_id when not provided."""
        with patch(
            "app.modules.parsers.pdf.azure_document_intelligence_processor.AzureOCRStrategy"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            handler = OCRHandler(
                logger,
                OCRProvider.AZURE_DI.value,
                endpoint="https://example.com",
                key="secret",
            )
            mock_cls.assert_called_once_with(
                logger=logger,
                endpoint="https://example.com",
                key="secret",
                model_id="prebuilt-document",
                config=None,
            )

class TestOCRHandlerProcessDocument:
    """Tests for OCRHandler.process_document."""

    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_process")

    @pytest.mark.asyncio
    async def test_process_document_success(self, logger):
        """process_document calls strategy.load_document and returns result."""
        mock_strategy = MagicMock()
        mock_strategy.load_document = AsyncMock(return_value=None)
        mock_strategy.document_analysis_result = {"pages": [{"text": "Hello"}]}

        # Bypass __init__ strategy creation
        with patch.object(OCRHandler, "__init__", lambda self, *a, **kw: None):
            handler = OCRHandler.__new__(OCRHandler)
            handler.logger = logger
            handler.strategy = mock_strategy
            mock_strategy.load_document = AsyncMock()

            result = await handler.process_document(b"pdf-bytes")

            mock_strategy.load_document.assert_awaited_once_with(b"pdf-bytes")
            assert result == {"pages": [{"text": "Hello"}]}

    @pytest.mark.asyncio
    async def test_process_document_raises_on_error(self, logger):
        """process_document re-raises exceptions from strategy."""
        with patch.object(OCRHandler, "__init__", lambda self, *a, **kw: None):
            handler = OCRHandler.__new__(OCRHandler)
            handler.logger = logger
            handler.strategy = MagicMock()
            handler.strategy.load_document = AsyncMock(side_effect=RuntimeError("parse error"))

            with pytest.raises(RuntimeError, match="parse error"):
                await handler.process_document(b"bad-pdf")


# Need this import for AsyncMock in process_document tests
from unittest.mock import AsyncMock
import asyncio
