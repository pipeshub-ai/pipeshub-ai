"""
Thread-safe PDF page rasterization via pdfplumber (pypdfium2 backend).

pypdfium2 is not thread-safe. All rendering runs in a dedicated process pool so
concurrent requests never share pdfium state in the same interpreter.
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np
import pdfplumber
from PIL import Image

from app.utils.recoverable_process_pool import RecoverableProcessPool


def _get_pdf_raster_worker_count() -> int:
    raw_value = os.getenv("PDF_RASTER_WORKERS")
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            pass

    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, 2))


PDF_RASTER_WORKERS = _get_pdf_raster_worker_count()


_pdf_raster_pool = RecoverableProcessPool(
    max_workers=PDF_RASTER_WORKERS, name="pdf-raster"
)


def shutdown_pdf_raster_pool() -> bool:
    """Shut down the PDF rasterization process pool if it was initialised."""
    return _pdf_raster_pool.shutdown()


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
    return _pdf_raster_pool.submit_and_wait(fn, *args)


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
    return await _pdf_raster_pool.run(
        _worker_render_all_from_path, pdf_path, resolution
    )


async def render_all_pages_from_bytes(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    return await _pdf_raster_pool.run(
        _worker_render_all_from_bytes, pdf_bytes, resolution
    )


async def render_all_pages_as_pil_from_bytes(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> List[Image.Image]:
    pages = await render_all_pages_from_bytes(pdf_bytes, resolution)
    return [Image.fromarray(pages[i][0]) for i in sorted(pages)]
