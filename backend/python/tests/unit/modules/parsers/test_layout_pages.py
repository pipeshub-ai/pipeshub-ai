"""Page layout parsing runs in a parse worker, so a long PDF cannot hold the loop's GIL."""
from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.pdf.layout_pages import ParsedPageData, parse_pdf_layout
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    ParsedPageData as ProcessorParsedPageData,
)
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from app.services.messaging.config import messaging_env
from tests.support.pdf_samples import text_pdf

if TYPE_CHECKING:
    from collections.abc import Iterator


def _processor() -> PDFPlumberOpenCVProcessor:
    return PDFPlumberOpenCVProcessor(logger=logging.getLogger("test-layout-pages"), config=MagicMock())


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(parse_pool.WORKERS_ENV, "1")
    governor = MagicMock()
    governor.ceilings = SimpleNamespace(heavy=4)
    parse_pool.set_resource_governor(governor)
    try:
        yield
    finally:
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)


async def _spied_submit(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record what is submitted to the parse pool, and prove the pool's worker is another process
    (its GIL is not the loop's). Used instead of timing the loop: a stall bound also measures the host."""
    submitted: list[object] = []
    real_submit = parse_pool.submit

    async def spy(fn: object, *args: object, **kwargs: object) -> object:
        submitted.append(fn)
        return await real_submit(fn, *args, **kwargs)

    monkeypatch.setattr(parse_pool, "submit", spy)
    assert await real_submit(os.getpid) != os.getpid()
    return submitted


class TestParsePdfLayout:
    def test_every_page_is_read_with_its_size_and_text(self) -> None:
        pages = parse_pdf_layout(text_pdf(pages=2))

        assert [page.page_number for page in pages] == [1, 2]
        assert all(page.width > 0 and page.height > 0 for page in pages)
        assert any("quick brown fox" in region.text for region in pages[0].regions)

    def test_the_processor_keeps_exporting_the_page_type(self) -> None:
        assert ProcessorParsedPageData is ParsedPageData


class TestProcessorParse:
    async def test_the_pages_are_parsed_through_the_parse_pool(self) -> None:
        content = text_pdf()
        with patch.object(parse_pool, "submit", new=AsyncMock(return_value=[])) as submit:
            await _processor().parse_document("report.pdf", content)

        submit.assert_awaited_once_with(
            parse_pdf_layout,
            content,
            label="report.pdf",
            size=len(content),
            timeout=messaging_env.pdf_record_processing_timeout,
        )

    async def test_a_real_worker_parses_while_the_loop_keeps_serving_io(
        self, pool: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = text_pdf(pages=30)
        submitted = await _spied_submit(monkeypatch)

        pages = await _processor().parse_document("long.pdf", content)

        assert len(pages) == 30
        assert isinstance(pages[0], ParsedPageData)
        assert submitted == [parse_pdf_layout]  # parsed in the worker process, not on the loop


class TestParsePdfLayoutMemory:
    def test_each_page_is_closed_once_parsed_and_the_heap_is_trimmed(self) -> None:
        """pdf.pages keeps every Page and its parsed chars until the PDF closes."""
        import pdfplumber.page

        from app.modules.parsers.pdf import layout_pages

        with patch.object(pdfplumber.page.Page, "close", autospec=True) as close, \
                patch.object(layout_pages, "_release_freed_memory") as trim:
            pages = parse_pdf_layout(text_pdf(pages=3))

        assert len(pages) == 3
        assert close.call_count >= 3
        trim.assert_called_once()


class TestParsePdfLayoutModelRasters:
    def test_one_windowed_72_dpi_cache_serves_the_layout_model(self) -> None:
        from app.modules.parsers.pdf import layout_pages

        seen = []
        real = layout_pages.extract_layout_regions

        def spy(page: object, **kwargs: object) -> list[object]:
            seen.append(kwargs["model_raster_cache"])
            return real(page, **kwargs)

        with patch.object(layout_pages, "extract_layout_regions", side_effect=spy):
            parse_pdf_layout(text_pdf(pages=3))

        assert len(seen) == 3 and len({id(c) for c in seen}) == 1
        cache = seen[0]
        assert (cache._dpi, cache._num_pages, cache._window) == (
            layout_pages.MODEL_DPI, 3, layout_pages.MODEL_RASTER_WINDOW_PAGES,
        )


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, []), ({"PIPESHUB_LANE_THREADS": "2"}, [2]), ({"PIPESHUB_LANE_THREADS": "2", "PIPESHUB_LANE_CV2_THREADS": "1"}, [1]),
     ({"PIPESHUB_LANE_THREADS": "2", "PIPESHUB_LANE_CV2_THREADS": "0"}, []), ({"PIPESHUB_LANE_CV2_THREADS": "3"}, [3])],
)
def test_cv2_cap_applied_once(monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: list[int]) -> None:
    import cv2

    from app.modules.parsers.pdf import layout_pages

    for name in ("PIPESHUB_LANE_THREADS", "PIPESHUB_LANE_CV2_THREADS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    calls: list[int] = []
    monkeypatch.setattr(cv2, "setNumThreads", calls.append)
    monkeypatch.setitem(layout_pages._cv2_cap, "applied", False)
    layout_pages._cap_cv2_threads()
    layout_pages._cap_cv2_threads()
    assert calls == expected


def test_parse_pdf_layout_caps_before_it_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.pdf import layout_pages

    order: list[str] = []
    monkeypatch.setattr(layout_pages, "_cap_cv2_threads", lambda: order.append("cap"))
    monkeypatch.setattr(layout_pages.pdfplumber, "open", lambda *a, **k: order.append("open") or (_ for _ in ()).throw(RuntimeError))
    with pytest.raises(RuntimeError):
        parse_pdf_layout(text_pdf(1))
    assert order == ["cap", "open"]
