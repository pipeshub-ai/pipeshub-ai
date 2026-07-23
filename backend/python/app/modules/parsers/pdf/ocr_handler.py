import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any, Dict

from app.config.constants.ai_models import OCRProvider
from app.exceptions.indexing_exceptions import DocumentProcessingError


VLM_OCR_MAX_CONCURRENT = max(
    1, int(os.getenv("VLM_OCR_MAX_CONCURRENT", "1"))
)
_vlm_ocr_semaphore: asyncio.Semaphore | None = None
_vlm_ocr_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_vlm_ocr_semaphore() -> asyncio.Semaphore:
    global _vlm_ocr_semaphore, _vlm_ocr_semaphore_loop
    loop = asyncio.get_running_loop()
    if _vlm_ocr_semaphore is None or _vlm_ocr_semaphore_loop is not loop:
        _vlm_ocr_semaphore = asyncio.Semaphore(VLM_OCR_MAX_CONCURRENT)
        _vlm_ocr_semaphore_loop = loop
    return _vlm_ocr_semaphore


class OCRStrategy(ABC):
    """Abstract base class for OCR strategies"""

    def __init__(self, logger) -> None:
        self.logger = logger

    @abstractmethod
    async def process_page(self, page) -> Dict[str, Any]:
        """Process a single page with OCR"""
        pass

    @abstractmethod
    async def load_document(self, content: bytes) -> None:
        """Load document content"""
        pass


    @staticmethod
    def needs_ocr(page, logger) -> bool:
        """Determine if a page needs OCR processing"""
        try:
            text = (page.extract_text() or "").strip()
            words = page.extract_words()
            images = page.images
            page_area = page.width * page.height

            MIN_IMAGE_WIDTH = 100
            MIN_IMAGE_HEIGHT = 100
            LOW_DENSITY_THRESHOLD = 0.01
            MIN_TEXT_LENGTH = 100
            MIN_SIGNIFICANT_IMAGES = 2

            significant_images = sum(
                1 for img in images
                if (img.get("width") or 0) > MIN_IMAGE_WIDTH and (img.get("height") or 0) > MIN_IMAGE_HEIGHT
            )

            has_minimal_text = len(text) < MIN_TEXT_LENGTH
            has_significant_images = significant_images > MIN_SIGNIFICANT_IMAGES
            text_density = (
                sum((w["x1"] - w["x0"]) * (w["bottom"] - w["top"]) for w in words) / page_area
                if words and page_area > 0
                else 0
            )
            low_density = text_density < LOW_DENSITY_THRESHOLD

            return (has_minimal_text and has_significant_images) or low_density

        except Exception as e:
            logger.warning(f"❌ Error in needs_ocr function: {str(e)}")
            return True


class OCRHandler:
    """Factory and facade for OCR processing.

    A single ``OCRHandler`` may be shared across concurrent requests (it is
    registered once in the :class:`ParserRegistry`).  ``process_document``
    creates a **fresh** strategy instance per call so that per-request state
    (temp files, page images, pdfplumber handles) is never shared between
    concurrent invocations. VLM OCR calls are additionally serialized through
    a semaphore (``VLM_OCR_MAX_CONCURRENT``, default 1) so at most that many
    documents are OCR'd at once.
    """

    def __init__(self, logger, strategy_type: str, **kwargs) -> None:
        """
        Initialize OCR handler with specified strategy

        Args:
            strategy_type: Type of OCR strategy ("vlm_ocr")
            **kwargs: Strategy-specific configuration parameters
        """
        self.logger = logger
        self.provider = strategy_type
        self._strategy_kwargs = kwargs
        self.logger.info("🛠️ Initializing OCR handler with strategy: %s", strategy_type)
        self._ensure_supported(strategy_type)

    def _ensure_supported(self, strategy_type: str) -> None:
        if strategy_type == OCRProvider.VLM_OCR.value:
            return
        self.logger.error(f"❌ Unsupported OCR strategy: {strategy_type}")
        raise DocumentProcessingError(
            f"Unsupported OCR strategy: {strategy_type}",
            details={"strategy": strategy_type},
        )

    def _create_strategy(self, strategy_type: str, **kwargs) -> OCRStrategy:
        """Factory method to create appropriate OCR strategy"""
        self.logger.debug(f"🏭 Creating OCR strategy: {strategy_type}")
        self._ensure_supported(strategy_type)

        self.logger.debug("🤖 Creating VLM OCR strategy")
        from app.modules.parsers.pdf.vlm_ocr_strategy import (
            VLMOCRStrategy,
        )

        return VLMOCRStrategy(
            logger=self.logger,
            config=kwargs.get("config"),
        )

    async def process_document(self, content: bytes) -> Dict[str, Any]:
        """
        Process a document using a fresh, request-scoped OCR strategy.

        A new strategy is created per call so that mutable per-request state
        (temp files, pre-rendered page images, pdfplumber document handles) is
        never shared between concurrent invocations — the previous approach of
        reusing a single strategy caused ``FileNotFoundError`` races when
        multiple PDFs were parsed in parallel. The actual OCR work is
        serialized via a semaphore so only one VLM OCR call runs at a time
        (bounded by ``VLM_OCR_MAX_CONCURRENT``).

        Args:
            content: PDF document content as bytes

        Returns:
            Dict containing extracted text and layout information
        """
        strategy = self._create_strategy(self.provider, **self._strategy_kwargs)
        self.logger.info("🚀 Starting document processing")
        try:
            async with _get_vlm_ocr_semaphore():
                self.logger.debug("📥 Loading document")
                await strategy.load_document(content)
                return strategy.document_analysis_result
        except Exception as e:
            self.logger.error(f"❌ Error processing document: {str(e)}")
            raise
