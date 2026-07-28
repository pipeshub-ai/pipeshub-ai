"""Tests for OCRHandler and OCRStrategy."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError

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
        """Create a pdfplumber-like mock page for OCRStrategy.needs_ocr."""
        page = MagicMock()
        page.width = width
        page.height = height
        page.parent = parent or MagicMock()
        page.extract_text = MagicMock(return_value=text)

        word_dicts = []
        for w in words or []:
            if isinstance(w, dict):
                word_dicts.append(w)
            else:
                x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
                word_dicts.append({"x0": float(x0), "top": float(y0), "x1": float(x1), "bottom": float(y1)})
        page.extract_words = MagicMock(return_value=word_dicts)

        img_list = []
        for im in images or []:
            if isinstance(im, dict):
                img_list.append(im)
            else:
                # Legacy tuple shape (xref, xref, width, height, ...)
                img_list.append({"width": float(im[2]), "height": float(im[3])})
        page.images = img_list
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

        assert OCRStrategy.needs_ocr(page, logger) is True

    def test_no_ocr_for_text_heavy_page(self, logger):
        """Page with substantial text does not need OCR."""
        long_text = "A" * 200
        # Words that cover substantial area
        words = [(0, 0, 100, 20, "word", 0, 0, 0)] * 50
        page = self._make_page(text=long_text, words=words)

        assert OCRStrategy.needs_ocr(page, logger) is False

    def test_needs_ocr_low_density(self, logger):
        """Page with low text density needs OCR."""
        # Short text with tiny word area, no significant images
        page = self._make_page(
            text="tiny",
            words=[(0, 0, 1, 1, "tiny", 0, 0, 0)],
            images=[],
        )

        assert OCRStrategy.needs_ocr(page, logger) is True

    def test_no_ocr_when_text_above_threshold_and_good_density(self, logger):
        """Page with enough text and reasonable density does not need OCR."""
        text = "X" * 150
        # Words covering a reasonable area
        words = [(0, 0, 200, 30, "word", 0, 0, 0)] * 20
        page = self._make_page(text=text, words=words, width=612, height=792)

        assert OCRStrategy.needs_ocr(page, logger) is False

    def test_images_below_min_size_not_significant(self, logger):
        """Small images are not counted as significant."""
        images = [
            (1, 0, 100, 100, 8, "RGB"),  # too small
            (2, 0, 200, 200, 8, "RGB"),  # too small
        ]
        page = self._make_page(text="short", images=images)

        # Short text but no significant images, and density depends on words
        # With no words, density = 0 < 0.01, so low_density is True
        assert OCRStrategy.needs_ocr(page, logger) is True

    def test_cmyk_image_converted(self, logger):
        """Raster images larger than thresholds count as significant."""
        images = [(1, 0, 600, 600, 8, "CMYK")]
        page = self._make_page(text="short", images=images)

        assert OCRStrategy.needs_ocr(page, logger) is True

    def test_exception_returns_true(self, logger):
        """When an exception occurs, needs_ocr returns True (safe fallback)."""
        page = MagicMock()
        page.extract_text.side_effect = RuntimeError("error")

        result = OCRStrategy.needs_ocr(page, logger)
        assert result is True

    def test_no_words_means_zero_density(self, logger):
        """When words list is empty, text_density is 0 (low_density=True)."""
        page = self._make_page(text="short", words=[], images=[])

        assert OCRStrategy.needs_ocr(page, logger) is True

    def test_zero_page_area_avoids_division_error(self, logger):
        """Zero page area must not raise; density stays 0."""
        page = self._make_page(text="short", words=[], images=[], width=0.0, height=0.0)

        assert OCRStrategy.needs_ocr(page, logger) is True

    def test_image_extraction_failure_continues(self, logger):
        """Needs-OCR heuristic tolerates malformed image metadata gracefully."""
        page = MagicMock()
        page.extract_text.return_value = "short"
        page.extract_words.return_value = []
        page.images = [{"width": float("nan"), "height": 600}]
        page.width = 612.0
        page.height = 792.0

        result = OCRStrategy.needs_ocr(page, logger)
        assert isinstance(result, bool)


class TestOCRHandlerInit:
    """Tests for OCRHandler initialization."""

    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_ocr_handler")

    def test_init_vlm_ocr_strategy(self, logger):
        """OCRHandler with VLM OCR strategy validates and stores config."""
        handler = OCRHandler(logger, OCRProvider.VLM_OCR.value, config={"key": "val"})

        assert handler.provider == OCRProvider.VLM_OCR.value
        assert handler._strategy_kwargs == {"config": {"key": "val"}}

    def test_init_unsupported_strategy_raises(self, logger):
        """Unsupported strategy type raises ValueError."""
        with pytest.raises(DocumentProcessingError, match="Unsupported OCR strategy"):
            OCRHandler(logger, "unknown_strategy")


class TestOCRHandlerProcessDocument:
    """Tests for OCRHandler.process_document."""

    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_process")

    @pytest.mark.asyncio
    async def test_process_document_success(self, logger):
        """process_document creates a strategy, loads the document, returns result."""
        mock_strategy = MagicMock()
        mock_strategy.load_document = AsyncMock(return_value=None)
        mock_strategy.document_analysis_result = {"pages": [{"text": "Hello"}]}

        handler = OCRHandler.__new__(OCRHandler)
        handler.logger = logger
        handler.provider = OCRProvider.VLM_OCR.value
        handler._strategy_kwargs = {"config": {"key": "val"}}

        with patch.object(handler, "_create_strategy", return_value=mock_strategy) as create:
            result = await handler.process_document(b"pdf-bytes")

        create.assert_called_once_with(OCRProvider.VLM_OCR.value, config={"key": "val"})
        mock_strategy.load_document.assert_awaited_once_with(b"pdf-bytes")
        assert result == {"pages": [{"text": "Hello"}]}

    @pytest.mark.asyncio
    async def test_process_document_raises_on_error(self, logger):
        """process_document re-raises exceptions from strategy."""
        mock_strategy = MagicMock()
        mock_strategy.load_document = AsyncMock(side_effect=RuntimeError("parse error"))

        handler = OCRHandler.__new__(OCRHandler)
        handler.logger = logger
        handler.provider = OCRProvider.VLM_OCR.value
        handler._strategy_kwargs = {}

        with patch.object(handler, "_create_strategy", return_value=mock_strategy):
            with pytest.raises(RuntimeError, match="parse error"):
                await handler.process_document(b"bad-pdf")

    @pytest.mark.asyncio
    async def test_process_document_uses_fresh_strategy_per_call(self, logger):
        """Concurrent-safe: each process_document call gets its own strategy."""
        strategies = []

        def make_strategy(*_args, **_kwargs):
            strategy = MagicMock()
            strategy.load_document = AsyncMock()
            strategy.document_analysis_result = {"id": len(strategies)}
            strategies.append(strategy)
            return strategy

        handler = OCRHandler(logger, OCRProvider.VLM_OCR.value, config={})
        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.VLMOCRStrategy",
            side_effect=make_strategy,
        ):
            result_a = await handler.process_document(b"doc-a")
            result_b = await handler.process_document(b"doc-b")

        assert result_a == {"id": 0}
        assert result_b == {"id": 1}
        assert strategies[0] is not strategies[1]
        strategies[0].load_document.assert_awaited_once_with(b"doc-a")
        strategies[1].load_document.assert_awaited_once_with(b"doc-b")
