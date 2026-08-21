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
            
            image_area_ratio = 0.0
            if images and page_area > 0:
                grid_size = 50
                grid = [[False] * grid_size for _ in range(grid_size)]
                for img in images:
                    x0 = max(0, img.get("x0", 0) or 0)
                    y0 = max(0, img.get("top", 0) or 0)
                    x1 = min(page.width, img.get("x1", x0 + (img.get("width", 0) or 0)) or page.width)
                    y1 = min(page.height, img.get("bottom", y0 + (img.get("height", 0) or 0)) or page.height)
                    
                    if x1 > x0 and y1 > y0:
                        gx0 = min(grid_size - 1, max(0, int((x0 / page.width) * grid_size)))
                        gy0 = min(grid_size - 1, max(0, int((y0 / page.height) * grid_size)))
                        gx1 = min(grid_size - 1, max(0, int((x1 / page.width) * grid_size)))
                        gy1 = min(grid_size - 1, max(0, int((y1 / page.height) * grid_size)))
                        
                        for i in range(gx0, gx1 + 1):
                            for j in range(gy0, gy1 + 1):
                                grid[i][j] = True
                
                covered_cells = sum(sum(row) for row in grid)
                image_area_ratio = covered_cells / (grid_size * grid_size)
            is_image_heavy = image_area_ratio > 0.5

            return (has_minimal_text and has_significant_images) or low_density or is_image_heavy

        except Exception as e:
            logger.warning(f"❌ Error in needs_ocr function: {str(e)}")
            return True


class OCRHandler:
    """Factory and facade for OCR processing.

    Strategies hold mutable per-document state (page images, temp PDF path),
    so each ``process_document`` call creates a fresh strategy instance.
    The handler itself is safe to share across concurrent requests.
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
        Process document using a fresh OCR strategy instance.

        Args:
            content: PDF document content as bytes

        Returns:
            Dict containing extracted text and layout information
        """
        self.logger.info("🚀 Starting document processing")
        strategy = self._create_strategy(self.provider, **self._strategy_kwargs)
        try:
            self.logger.debug("📥 Loading document")
            await strategy.load_document(content)
            return strategy.document_analysis_result
        except Exception as e:
            self.logger.error(f"❌ Error processing document: {str(e)}")
            raise

