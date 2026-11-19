"""The page cap on OCR, kept apart from the OCR strategy so light modules can read it.

``vlm_ocr_strategy`` imports the LLM stack; anything that only needs the cap
(for example code that runs in a parse worker) imports this instead.
"""
from __future__ import annotations

from app.utils.env_config import env_int

# Every page is one model call, so a longer scan is refused rather than read at any cost.
MAX_PAGES_ENV = "VLM_OCR_MAX_PAGES"
DEFAULT_MAX_PAGES = 500


def vlm_ocr_max_pages() -> int:
    return max(1, env_int(MAX_PAGES_ENV, DEFAULT_MAX_PAGES))
