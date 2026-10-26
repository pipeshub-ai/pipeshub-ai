"""What indexing needs to know about a PDF before parsing it, read in a parse worker.

These functions run in a ``parse_pool`` worker process (see ``untrusted_pdf``),
which imports this module by name: keep its imports to the PDF libraries and
modules as light as them.
"""
from __future__ import annotations

import io
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pdfplumber
import pypdfium2

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.encryption import (
    PASSWORD_PROTECTED_MESSAGE,
    pdfium_password_error,
)
from app.modules.parsers.pdf.ocr_handler import OCRStrategy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pdfplumber.page import Page

_logger = logging.getLogger(__name__)

# A PDF needs OCR when at least this share of its (sampled) pages do.
OCR_PAGE_SHARE = 0.5


@dataclass(frozen=True)
class PdfStructure:
    page_count: int
    # Opening the file and listing its pages, which every later pdfplumber
    # read of these bytes pays again.
    open_seconds: float


def open_pdf(content: bytes) -> PdfStructure:
    """Open *content* with pdfplumber and list its pages."""
    started = time.monotonic()
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return PdfStructure(page_count=len(pdf.pages), open_seconds=time.monotonic() - started)


def detect_ocr(content: bytes, sample_pages: int) -> bool:
    """Whether at least half of a sample of *content*'s pages need OCR."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = pdf.pages
        return pages_need_ocr([pages[i] for i in sample_page_indices(len(pages), sample_pages)])


def sample_page_indices(page_count: int, limit: int) -> list[int]:
    """Every page up to *limit*; past it, *limit* pages evenly spread, first and last included.

    Deciding OCR from a sample keeps the cost of the decision flat: each page
    checked runs pdfminer's full layout analysis, and the decision can need
    half the pages of a long document.
    """
    limit = max(1, limit)
    if page_count <= limit:
        return list(range(page_count))
    if limit == 1:
        return [0]
    step = (page_count - 1) / (limit - 1)
    return [round(i * step) for i in range(limit)]


def pages_need_ocr(pages: Sequence[Page]) -> bool:
    """True when at least half of *pages* need OCR. Stops as soon as the answer is known."""
    if not pages:
        return False
    threshold = math.ceil(len(pages) * OCR_PAGE_SHARE)
    ocr_pages = 0
    for checked, page in enumerate(pages, start=1):
        if OCRStrategy.needs_ocr(page, _logger):
            ocr_pages += 1
            if ocr_pages >= threshold:
                return True
        if ocr_pages + (len(pages) - checked) < threshold:
            return False
    return ocr_pages >= threshold


def repair_pdf(content: bytes) -> bytes:
    """*content* rewritten by pdfium, with a cross-reference table that matches the file.

    pdfium reconstructs a damaged table from the objects themselves and drops
    trailing garbage, which pdfminer otherwise reads one byte at a time.
    Raises ``PasswordProtectedError`` when the file needs a password, and
    ``pypdfium2.PdfiumError`` when pdfium cannot open it for another reason.
    """
    try:
        document = pypdfium2.PdfDocument(content)
    except pypdfium2.PdfiumError as exc:
        if pdfium_password_error(exc):
            raise PasswordProtectedError(
                PASSWORD_PROTECTED_MESSAGE, details={"format": "pdf"}
            ) from exc
        raise
    try:
        rewritten = io.BytesIO()
        document.save(rewritten)
    finally:
        document.close()
    return rewritten.getvalue()
