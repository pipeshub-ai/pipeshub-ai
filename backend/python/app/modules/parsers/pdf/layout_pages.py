"""The layout regions of every page of a PDF, read in a parse worker.

pdfminer's layout analysis is pure Python and holds the GIL for every page, so
``PDFPlumberOpenCVProcessor`` runs this in a ``parse_pool`` worker process,
which imports this module by name: keep its imports to the PDF libraries and
the layout analyser.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import re
import tempfile
from dataclasses import dataclass

import pdfplumber

from app.modules.parsers.pdf.opencv_layout_analyzer import (
    MODEL_DPI,
    MODEL_RASTER_WINDOW_PAGES,
    DocumentRasterCache,
    LayoutRegion,
    LayoutRegionType,
    extract_layout_regions,
)


@dataclass
class ParsedPageData:
    page_number: int
    width: float
    height: float
    regions: list[LayoutRegion]


# pdfplumber's text for a glyph with no Unicode mapping: not text, so never indexed.
_UNMAPPED_GLYPH_RE = re.compile(r"\(cid:\d+\)")


def _release_freed_memory() -> None:
    """Hand memory freed by a parse back to the OS.

    glibc keeps freed heap for reuse, so a long-lived worker grew from ~1.2 GB to
    ~4 GB over a run of long PDFs although each one peaks under 2 GB on its own.
    """
    with contextlib.suppress(OSError, AttributeError):  # not glibc (macOS, musl)
        ctypes.CDLL("libc.so.6").malloc_trim(0)


def _drop_unmapped_glyphs(regions: list[LayoutRegion]) -> list[LayoutRegion]:
    """Remove "(cid:N)" glyph codes and the text regions left empty by it.

    Pages made mostly of them are routed to OCR (OCRStrategy.has_unreadable_text);
    this handles the rest, e.g. one unmapped font on an otherwise readable page.
    """
    kept = []
    for r in regions:
        r.text = _UNMAPPED_GLYPH_RE.sub("", r.text or "").strip()
        r.list_items = [t for t in (_UNMAPPED_GLYPH_RE.sub("", i).strip() for i in r.list_items) if t]
        if r.table_grid:
            r.table_grid = [
                [_UNMAPPED_GLYPH_RE.sub("", c) if isinstance(c, str) else c for c in row]
                for row in r.table_grid
            ]
        if r.type in (LayoutRegionType.TABLE, LayoutRegionType.IMAGE) or r.text or r.list_items:
            kept.append(r)
    return kept


def parse_pdf_layout(pdf_bytes: bytes) -> list[ParsedPageData]:
    # The rasteriser reads the file by path.
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix="pipeshub_pdf_") as tmp:
            tmp_path = tmp.name
            tmp.write(pdf_bytes)
            tmp.flush()
        pages: list[ParsedPageData] = []
        with pdfplumber.open(tmp_path) as pdf:
            raster_cache = DocumentRasterCache(tmp_path, num_pages=len(pdf.pages))
            model_rasters = DocumentRasterCache(
                tmp_path, dpi=MODEL_DPI, num_pages=len(pdf.pages), window=MODEL_RASTER_WINDOW_PAGES
            )
            for page_index, page in enumerate(pdf.pages):
                regions = _drop_unmapped_glyphs(
                    extract_layout_regions(
                        page, pdf_path=tmp_path, raster_cache=raster_cache, model_raster_cache=model_rasters
                    )
                )
                # pdf.pages keeps every Page, and each caches its parsed chars and
                # layout: ~5 MB a page until the PDF is closed.
                page.close()
                pages.append(
                    ParsedPageData(
                        page_number=page_index + 1,
                        width=float(page.width),
                        height=float(page.height),
                        regions=regions,
                    )
                )
        return pages
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        # The worker outlives the parse: hand its pages, chars and rasters back.
        _release_freed_memory()
