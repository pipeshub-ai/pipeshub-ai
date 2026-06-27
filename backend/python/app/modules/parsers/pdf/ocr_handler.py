from abc import ABC, abstractmethod
from typing import Any, Dict

from app.config.constants.ai_models import OCRProvider
from app.exceptions.indexing_exceptions import DocumentProcessingError


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
    """Factory and facade for OCR processing"""

    def __init__(self, logger, strategy_type: str, **kwargs) -> None:
        """
        Initialize OCR handler with specified strategy

        Args:
            strategy_type: Type of OCR strategy ("vlm_ocr")
            **kwargs: Strategy-specific configuration parameters
        """
        self.logger = logger
        self.provider = strategy_type
        self.logger.info("🛠️ Initializing OCR handler with strategy: %s", strategy_type)
        self.strategy = self._create_strategy(strategy_type, **kwargs)

    def _create_strategy(self, strategy_type: str, **kwargs) -> OCRStrategy:
        """Factory method to create appropriate OCR strategy"""
        self.logger.debug(f"🏭 Creating OCR strategy: {strategy_type}")

        if strategy_type == OCRProvider.VLM_OCR.value:
            self.logger.debug("🤖 Creating VLM OCR strategy")
            from app.modules.parsers.pdf.vlm_ocr_strategy import (
                VLMOCRStrategy,
            )

            return VLMOCRStrategy(
                logger=self.logger,
                config=kwargs.get("config"),
            )
        else:
            self.logger.error(f"❌ Unsupported OCR strategy: {strategy_type}")
            raise DocumentProcessingError(
                f"Unsupported OCR strategy: {strategy_type}",
                details={"strategy": strategy_type},
            )

    async def process_document(self, content: bytes) -> Dict[str, Any]:
        """
        Process document using the configured OCR strategy

        Args:
            content: PDF document content as bytes

        Returns:
            Dict containing extracted text and layout information
        """
        self.logger.info("🚀 Starting document processing")
        try:
            self.logger.debug("📥 Loading document")
            await self.strategy.load_document(content)
            return self.strategy.document_analysis_result
        except Exception as e:
            self.logger.error(f"❌ Error processing document: {str(e)}")
            raise

