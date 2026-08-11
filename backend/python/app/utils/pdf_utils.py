"""Lightweight PDF helpers shared by the Docling client and the local processor.

Kept free of heavy Docling/converter dependencies so importers that only need
page-count/batching info (e.g. the external Docling HTTP client) don't pull in
the full conversion stack.
"""
import os

import pypdfium2 as pdfium

DEFAULT_PAGE_BATCH_SIZE = 10


def _get_page_batch_size() -> int:
    raw = os.getenv("DOCLING_PAGE_BATCH_SIZE")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_PAGE_BATCH_SIZE


PAGE_BATCH_SIZE = _get_page_batch_size()


def get_pdf_page_count(content: bytes) -> int:
    """Return the number of pages in a PDF binary using pypdfium2."""
    pdf = pdfium.PdfDocument(content)
    try:
        return len(pdf)
    finally:
        pdf.close()
