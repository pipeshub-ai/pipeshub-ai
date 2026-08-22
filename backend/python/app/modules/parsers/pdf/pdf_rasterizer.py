"""
Thread-safe PDF page rasterization via pdfplumber (pypdfium2 backend).

pypdfium2 is not thread-safe. All rendering runs in a dedicated process pool so
concurrent requests never share pdfium state in the same interpreter.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np
import pdfplumber
from PIL import Image

if TYPE_CHECKING:
    from app.services.resource_governor import ResourceGovernor

_logger = logging.getLogger(__name__)
_pool_lock = threading.Lock()

# Wired by each service's lifespan (see parsing_main.py/indexing_main.py/
# docling_main.py) once a ResourceGovernor is constructed for that process.
# Mirrors the module-level singleton in services/docling/docling_service.py —
# this module has no DI path back to whichever governor its caller's process
# owns, since it's a leaf shared by the Parsing, Indexing and Docling
# services alike.
_resource_governor: "ResourceGovernor | None" = None


def set_resource_governor(governor: "ResourceGovernor | None") -> None:
    """Wire an initialized ResourceGovernor so a worker OOM-kill can trigger
    its fast incident path instead of waiting for the next periodic sample."""
    globals()["_resource_governor"] = governor


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
        if _resource_governor is not None:
            # A worker OOM-kill is a hard proof of memory exhaustion the
            # periodic sampler may not see for several seconds — react now
            # rather than let admission keep granting new heavy-parse slots
            # at the limit that just caused this kill.
            _resource_governor.report_memory_incident(
                "pdf_rasterizer worker OOM-killed (BrokenProcessPool)"
            )
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
