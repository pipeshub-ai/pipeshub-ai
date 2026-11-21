"""OCR for the pages of a text-layer PDF whose content has no text layer.

The whole-document OCR check answers for a document, so a page whose text is drawn as outlines (a
schedule, a chart with its labels) inside a document that is otherwise text is never read: it has
about as many characters as a page number and thousands of curves. ``ocr_textless_pages`` finds those
pages (``pdf_inspection.textless_pages``), reads only them with the configured VLM OCR and replaces
each page's regions with the OCR text, so page order, page numbers and every other page stay as they were.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

from app.config.constants.ai_models import OCRProvider
from app.config.constants.service import config_node_constants
from app.modules.parsers import parse_pool
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    LayoutRegion,
    LayoutRegionType,
)
from app.modules.parsers.pdf.pdf_inspection import extract_pages, textless_pages
from app.services.resource_governor.kinds import ParseKind
from app.utils.aimodels import is_multimodal_llm
from app.utils.env_config import env_int

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.modules.parsers.pdf.layout_pages import ParsedPageData

# At most this many pages of one document are read by OCR; 0 turns the step off.
PAGE_OCR_MAX_PAGES_ENV = "PDF_PAGE_OCR_MAX_PAGES"
DEFAULT_PAGE_OCR_MAX_PAGES = 25

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def page_ocr_max_pages() -> int:
    return max(0, env_int(PAGE_OCR_MAX_PAGES_ENV, DEFAULT_PAGE_OCR_MAX_PAGES))


def regions_from_markdown(markdown: str, page: ParsedPageData) -> list[LayoutRegion]:
    """One text region per paragraph of OCR *markdown*, each spanning *page* (OCR gives no boxes)."""
    bbox = (0.0, 0.0, float(page.width), float(page.height))
    return [
        LayoutRegion(type=LayoutRegionType.TEXT, bbox=bbox, text=paragraph.strip())
        for paragraph in _PARAGRAPH_SPLIT_RE.split(markdown or "")
        if paragraph.strip()
    ]


async def _vlm_ocr_handler(config: ConfigurationService, logger: logging.Logger) -> Optional[OCRHandler]:
    """The VLM OCR handler the OCR route would use, or None when no OCR provider or multimodal model is set."""
    ai_models: Any = await config.get_config(config_node_constants.AI_MODELS.value, raise_on_error=True)
    if not isinstance(ai_models, dict):
        return None
    configured = any(
        isinstance(c, dict) and c.get("provider") == OCRProvider.VLM_OCR.value for c in ai_models.get("ocr") or []
    )
    if not configured:
        configured = any(is_multimodal_llm(c) for c in ai_models.get("llm") or [])
    if not configured:
        return None
    return OCRHandler(logger, OCRProvider.VLM_OCR.value, config=config)


async def ocr_textless_pages(
    pdf_bytes: bytes,
    parsed_data: list[ParsedPageData],
    config: ConfigurationService,
    logger: logging.Logger,
) -> list[int]:
    """Replace the regions of the text-less pages of *parsed_data* with their OCR text; the pages changed.

    Never raises: the pages stay as parsed when nothing is configured or the OCR fails.
    """
    cap = page_ocr_max_pages()
    if cap <= 0 or not pdf_bytes:
        return []
    try:
        numbers, found = await parse_pool.submit(
            textless_pages, pdf_bytes, cap, label="page-ocr-detect", size=len(pdf_bytes), kind=ParseKind.INSPECT
        )
    except Exception as e:
        logger.warning("Could not look for pages without a text layer: %s", type(e).__name__)
        return []
    if not numbers:
        return []
    by_number = {p.page_number: p for p in parsed_data}
    numbers = [n for n in numbers if n in by_number]
    if not numbers:
        return []
    if found > cap:
        logger.info(
            "Page OCR: %d pages without a text layer, reading the %d with the most drawn content (cap %s=%d)",
            found, len(numbers), PAGE_OCR_MAX_PAGES_ENV, cap,
        )
    try:
        handler = await _vlm_ocr_handler(config, logger)
        if handler is None:
            logger.info(
                "Page OCR: %d pages without a text layer left as parsed, no OCR provider or multimodal model is configured",
                len(numbers),
            )
            return []
        subset = await parse_pool.submit(
            extract_pages, pdf_bytes, numbers, label="page-ocr-extract", size=len(pdf_bytes), kind=ParseKind.INSPECT
        )
        result = await handler.process_document(subset)
    except Exception as e:
        logger.warning("Page OCR failed, the pages stay as parsed: %s", type(e).__name__)
        return []
    replaced: list[int] = []
    for ocr_page in (result or {}).get("pages", []):
        index = ocr_page.get("page_number")
        if not isinstance(index, int) or not 1 <= index <= len(numbers):
            continue
        page = by_number[numbers[index - 1]]
        regions = regions_from_markdown(ocr_page.get("markdown", ""), page)
        if regions:
            page.regions = regions
            replaced.append(page.page_number)
    logger.info("Page OCR: read %d of %d pages without a text layer", len(replaced), found)
    return sorted(replaced)
