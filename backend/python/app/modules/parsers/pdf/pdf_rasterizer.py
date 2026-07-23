"""
Thread-safe PDF page rasterization and inspection via pdfium.

pypdfium2 is not thread-safe. All rendering (and the scanned-document probe
below, which also drives pdfium) runs in a dedicated process pool so
concurrent requests never share pdfium state in the same interpreter.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import math
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

_logger = logging.getLogger(__name__)
_pool_lock = threading.Lock()


def _get_pdf_raster_worker_count() -> int:
    raw_value = os.getenv("PDF_RASTER_WORKERS")
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            pass

    # Each worker imports the PDF/ML stack and can approach 1 GiB RSS while
    # rendering. Keep the safe default at one; operators running parsing in a
    # dedicated, memory-sized container can explicitly raise it.
    return 1


PDF_RASTER_WORKERS = _get_pdf_raster_worker_count()


@lru_cache(maxsize=1)
def _get_pdf_raster_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=PDF_RASTER_WORKERS,
        mp_context=multiprocessing.get_context("spawn"),
    )


def shutdown_pdf_raster_pool() -> bool:
    """Shut down the PDF rasterization process pool if it was initialised."""
    if _get_pdf_raster_pool.cache_info().currsize == 0:
        return False
    _get_pdf_raster_pool().shutdown(wait=False, cancel_futures=True)
    _get_pdf_raster_pool.cache_clear()
    return True


@atexit.register
def _shutdown_pdf_raster_pool_on_exit() -> None:
    shutdown_pdf_raster_pool()


# --------------------------------------------------------------------------- #
# Scanned-document detection
# --------------------------------------------------------------------------- #

# Pages sampled per document; evenly spread so front matter can't skew the
# verdict. Enough for a stable estimate of the 30% threshold below while
# keeping detection O(1) in document size.
SCANNED_PDF_SAMPLE_PAGES = max(
    4, int(os.getenv("SCANNED_PDF_SAMPLE_PAGES", "16"))
)
# A page with fewer extractable characters than this is treated as image-only.
# Matches MIN_TEXT_LENGTH from the previous pdfplumber heuristic.
SCANNED_PDF_MIN_CHARS_PER_PAGE = int(
    os.getenv("SCANNED_PDF_MIN_CHARS_PER_PAGE", "100")
)
# Fraction of sampled pages that must be image-only before the whole document
# is routed to the VLM. Matches the previous _OCR_PAGE_THRESHOLD.
SCANNED_PDF_PAGE_RATIO = float(os.getenv("SCANNED_PDF_PAGE_RATIO", "0.3"))


def _sample_page_indices(total_pages: int, sample_size: int) -> List[int]:
    if total_pages <= sample_size:
        return list(range(total_pages))
    step = total_pages / sample_size
    return sorted({int(i * step) for i in range(sample_size)})


def _worker_detect_scanned_pdf(
    pdf_bytes: bytes,
    sample_size: int,
    min_chars_per_page: int,
    scanned_page_ratio: float,
) -> bool:
    """Decide whether a PDF is a scan by counting extractable text characters
    on an evenly-spaced page sample.

    Runs on pdfium's native text index, so it never materialises page text,
    char dicts, or images — memory stays constant regardless of page count.
    """
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        indices = _sample_page_indices(len(pdf), sample_size)
        if not indices:
            return False

        needed = max(1, math.ceil(scanned_page_ratio * len(indices)))
        scanned = 0
        for pos, index in enumerate(indices):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    if textpage.count_chars() < min_chars_per_page:
                        scanned += 1
                finally:
                    textpage.close()
            finally:
                page.close()

            remaining = len(indices) - pos - 1
            if scanned >= needed:
                return True
            if scanned + remaining < needed:
                return False
        return False
    finally:
        pdf.close()


def detect_scanned_pdf_sync(pdf_bytes: bytes) -> bool:
    """Return True when the PDF looks scanned (image-only pages, no text layer).

    Raises on unreadable input (encrypted/corrupt PDFs) — callers decide the
    routing default in that case.
    """
    return _run_in_pool(
        _worker_detect_scanned_pdf,
        pdf_bytes,
        SCANNED_PDF_SAMPLE_PAGES,
        SCANNED_PDF_MIN_CHARS_PER_PAGE,
        SCANNED_PDF_PAGE_RATIO,
    )


async def detect_scanned_pdf(pdf_bytes: bytes) -> bool:
    """Async variant of :func:`detect_scanned_pdf_sync`; blocks a worker
    thread only on pool submission, never the event loop."""
    return await asyncio.to_thread(detect_scanned_pdf_sync, pdf_bytes)


def _page_to_rgb_array(page, resolution: float) -> Tuple[np.ndarray, float]:
    pil = page.to_image(resolution=resolution).original
    return np.array(pil), resolution / 72.0


def _render_all_pages_impl(
    pdf_bytes: Optional[bytes],
    pdf_path: Optional[str],
    resolution: float,
) -> Dict[int, Tuple[np.ndarray, float]]:
    if pdf_path is not None:
        ctx = pdfplumber.open(pdf_path)
    else:
        ctx = pdfplumber.open(BytesIO(pdf_bytes))

    result: Dict[int, Tuple[np.ndarray, float]] = {}
    with ctx as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            result[page_number] = _page_to_rgb_array(page, resolution)
    return result


def _render_batch_impl(
    pdf_bytes: Optional[bytes],
    pdf_path: Optional[str],
    page_numbers: List[int],
    resolution: float,
) -> Dict[int, Tuple[np.ndarray, float]]:
    """Render only the requested 1-based *page_numbers* from a PDF."""
    if pdf_path is not None:
        ctx = pdfplumber.open(pdf_path)
    else:
        ctx = pdfplumber.open(BytesIO(pdf_bytes))

    result: Dict[int, Tuple[np.ndarray, float]] = {}
    with ctx as pdf:
        for page_number in page_numbers:
            result[page_number] = _page_to_rgb_array(
                pdf.pages[page_number - 1], resolution
            )
    return result


def _worker_render_all_from_path(
    pdf_path: str,
    resolution: float,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return _render_all_pages_impl(None, pdf_path, resolution)


def _worker_render_all_from_bytes(
    pdf_bytes: bytes,
    resolution: float,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return _render_all_pages_impl(pdf_bytes, None, resolution)


def _worker_render_batch_from_path(
    pdf_path: str,
    page_numbers: List[int],
    resolution: float,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return _render_batch_impl(None, pdf_path, page_numbers, resolution)


def _worker_render_batch_from_bytes(
    pdf_bytes: bytes,
    page_numbers: List[int],
    resolution: float,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return _render_batch_impl(pdf_bytes, None, page_numbers, resolution)


def _worker_render_page_from_path(
    pdf_path: str,
    page_number: int,
    resolution: float,
) -> Tuple[np.ndarray, float]:
    with pdfplumber.open(pdf_path) as pdf:
        return _page_to_rgb_array(pdf.pages[page_number - 1], resolution)


def _worker_render_page_from_bytes(
    pdf_bytes: bytes,
    page_number: int,
    resolution: float,
) -> Tuple[np.ndarray, float]:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return _page_to_rgb_array(pdf.pages[page_number - 1], resolution)


def _run_in_pool(fn, *args):
    try:
        return _get_pdf_raster_pool().submit(fn, *args).result()
    except BrokenProcessPool:
        _logger.warning(
            "PDF rasterization process pool broke (worker likely OOM-killed); "
            "recreating pool"
        )
        with _pool_lock:
            _get_pdf_raster_pool.cache_clear()
        raise


def render_all_pages_from_path_sync(
    pdf_path: str,
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return _run_in_pool(_worker_render_all_from_path, pdf_path, resolution)


def render_all_pages_from_bytes_sync(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return _run_in_pool(_worker_render_all_from_bytes, pdf_bytes, resolution)


def render_page_from_path_sync(
    pdf_path: str,
    page_number: int,
    resolution: float = 72,
) -> Tuple[np.ndarray, float]:
    return _run_in_pool(
        _worker_render_page_from_path,
        pdf_path,
        page_number,
        resolution,
    )


def render_page_from_bytes_sync(
    pdf_bytes: bytes,
    page_number: int,
    resolution: float = 72,
) -> Tuple[np.ndarray, float]:
    return _run_in_pool(
        _worker_render_page_from_bytes,
        pdf_bytes,
        page_number,
        resolution,
    )


def render_batch_from_path_sync(
    pdf_path: str,
    page_numbers: List[int],
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    """Render a subset of pages (1-based) from a PDF on disk."""
    return _run_in_pool(
        _worker_render_batch_from_path, pdf_path, page_numbers, resolution
    )


def render_batch_from_bytes_sync(
    pdf_bytes: bytes,
    page_numbers: List[int],
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    """Render a subset of pages (1-based) from in-memory PDF bytes."""
    return _run_in_pool(
        _worker_render_batch_from_bytes, pdf_bytes, page_numbers, resolution
    )


def render_all_pages_as_pil_from_bytes_sync(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> List[Image.Image]:
    pages = render_all_pages_from_bytes_sync(pdf_bytes, resolution)
    return [Image.fromarray(pages[i][0]) for i in sorted(pages)]


async def render_all_pages_from_path(
    pdf_path: str,
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _get_pdf_raster_pool(),
        _worker_render_all_from_path,
        pdf_path,
        resolution,
    )


async def render_all_pages_from_bytes(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _get_pdf_raster_pool(),
        _worker_render_all_from_bytes,
        pdf_bytes,
        resolution,
    )


async def render_all_pages_as_pil_from_bytes(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> List[Image.Image]:
    pages = await render_all_pages_from_bytes(pdf_bytes, resolution)
    return [Image.fromarray(pages[i][0]) for i in sorted(pages)]
