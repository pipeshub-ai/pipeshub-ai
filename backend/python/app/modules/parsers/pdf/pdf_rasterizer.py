"""
Thread-safe PDF page rasterization via pdfplumber (pypdfium2 backend).

pypdfium2 is not thread-safe. All rendering runs in a dedicated process pool so
concurrent requests never share pdfium state in the same interpreter.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import ctypes
import logging
import math
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, TypeVar

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from app.services.resource_governor.probe import effective_cpu_count
from app.utils.worker_scaling import scaled

if TYPE_CHECKING:
    from collections.abc import Callable

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

    return max(1, min(effective_cpu_count(), 2))


PDF_RASTER_WORKERS = _get_pdf_raster_worker_count()


def pdf_raster_pool_size() -> int:
    """Processes the rasterizer pool uses in this service process (for /health)."""
    return scaled(PDF_RASTER_WORKERS)


_T = TypeVar("_T")
_HOUSEKEEPING_SECONDS = 2.0
# Set in a pool worker when a task ends; its housekeeping thread then trims.
_task_ended = threading.Event()


def _release_freed_memory() -> None:
    with contextlib.suppress(OSError, AttributeError):  # not glibc (macOS, musl)
        ctypes.CDLL("libc.so.6").malloc_trim(0)


def _start_worker_housekeeping(parent_pid: int) -> None:
    """Pool initializer: a thread that keeps an idle worker from holding memory.

    glibc keeps freed heap for reuse, so a worker held ~0.6 GB more than it
    needed after rendering 32 pages of a 300 MB PDF; it trims after each task.
    A SIGKILLed parent (OOM kill, an idle parse worker being stopped) never
    shuts the pool down, and its workers would block on their call queue
    forever, so a worker exits once it is reparented.
    """

    def housekeep() -> None:
        trims_left = 0
        while os.getppid() == parent_pid:
            time.sleep(_HOUSEKEEPING_SECONDS)
            if _task_ended.is_set():
                _task_ended.clear()
                # The pool sends the result after the task returns, so the
                # first trim can run while the worker still holds it.
                trims_left = 2
            if trims_left:
                trims_left -= 1
                _release_freed_memory()
        os._exit(0)

    threading.Thread(target=housekeep, name="raster-housekeeping", daemon=True).start()


def _run_and_release(fn: Callable[..., _T], *args: object) -> _T:
    try:
        return fn(*args)
    finally:
        _task_ended.set()


@lru_cache(maxsize=1)
def _get_pdf_raster_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=pdf_raster_pool_size(),
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_start_worker_housekeeping,
        initargs=(os.getpid(),),
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


# Target ceiling on rendered pixels for one page. Large-format sheets (a 49x35in
# CAD drawing renders to 39 MPix at 150 DPI) cost ~120 MB for the RGB array and
# peak near 1 GB in the worker, which OOM-kills the pool. Every ISO size up to
# A2 stays under this bound at 150 DPI, so only oversized sheets are stepped
# down. The renderer rounds each pixel dimension up, so an individual page can
# land a fraction of a percent over.
MAX_RASTER_PIXELS = 10_000_000


def _effective_resolution(page, resolution: float) -> float:
    """Reduce *resolution* until the rendered page fits in MAX_RASTER_PIXELS."""
    try:
        width_pt = float(page.width)
        height_pt = float(page.height)
    except (AttributeError, TypeError, ValueError):
        return resolution
    if width_pt <= 0 or height_pt <= 0 or resolution <= 0:
        return resolution
    pixels = width_pt * height_pt * (resolution / 72.0) ** 2
    if pixels <= MAX_RASTER_PIXELS:
        return resolution
    return resolution * math.sqrt(MAX_RASTER_PIXELS / pixels)


def _page_to_rgb_array(page, resolution: float) -> Tuple[np.ndarray, float]:
    effective = _effective_resolution(page, resolution)
    if effective < resolution:
        _logger.info(
            "Page is %.0fx%.0f pt; rendering at %.1f DPI instead of %.1f to stay "
            "within %d pixels",
            float(page.width), float(page.height), effective, resolution,
            MAX_RASTER_PIXELS,
        )
    pil = page.to_image(resolution=effective).original
    # The scale must describe the raster actually produced -- every consumer
    # maps PDF points to pixels by multiplying with it, so returning the
    # requested resolution here would misplace every crop.
    return np.array(pil), effective / 72.0


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


def _worker_count_chars_from_bytes(pdf_bytes: bytes) -> list[int]:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        counts: list[int] = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                counts.append(textpage.count_chars())
            finally:
                textpage.close()
                page.close()
        return counts
    finally:
        pdf.close()


def _run_in_pool(fn, *args):
    try:
        return _get_pdf_raster_pool().submit(_run_and_release, fn, *args).result()
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


def count_chars_per_page_from_bytes_sync(pdf_bytes: bytes) -> list[int]:
    """Characters in each page's text layer, read by pdfium in the raster pool.

    pdfplumber extracts text with pure-Python pdfminer, which can take minutes
    on a page whose glyphs are drawn as vector paths; pdfium answers in
    milliseconds, and it runs here because it is not thread-safe.
    """
    return _run_in_pool(_worker_count_chars_from_bytes, pdf_bytes)


def render_all_pages_as_pil_from_bytes_sync(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> List[Image.Image]:
    pages = render_all_pages_from_bytes_sync(pdf_bytes, resolution)
    return [Image.fromarray(pages[i][0]) for i in sorted(pages)]


async def render_all_pages_from_bytes(
    pdf_bytes: bytes,
    resolution: float = 72,
) -> Dict[int, Tuple[np.ndarray, float]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _get_pdf_raster_pool(),
        _run_and_release,
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
