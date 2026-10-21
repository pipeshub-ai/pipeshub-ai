"""The layout regions of every page of a PDF, read in a parse worker.

pdfminer's layout analysis is pure Python and holds the GIL for every page, so
``PDFPlumberOpenCVProcessor`` runs this in a ``parse_pool`` worker process,
which imports this module by name: keep its imports to the PDF libraries and
the layout analyser.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass

import pdfplumber

from app.modules.parsers.pdf.opencv_layout_analyzer import (
    DocumentRasterCache,
    LayoutRegion,
    extract_layout_regions,
)


@dataclass
class ParsedPageData:
    page_number: int
    width: float
    height: float
    regions: list[LayoutRegion]


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
            raster_cache = DocumentRasterCache(tmp_path)
            for page_index, page in enumerate(pdf.pages):
                pages.append(
                    ParsedPageData(
                        page_number=page_index + 1,
                        width=float(page.width),
                        height=float(page.height),
                        regions=extract_layout_regions(page, pdf_path=tmp_path, raster_cache=raster_cache),
                    )
                )
        return pages
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
