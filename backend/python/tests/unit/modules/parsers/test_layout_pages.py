"""Page layout parsing runs in a parse worker, so a long PDF cannot hold the loop's GIL."""
from __future__ import annotations

import asyncio
import logging
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
from tests.support.loop_probes import IoProbe
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

    async def test_a_real_worker_parses_while_the_loop_keeps_serving_io(self, pool: None) -> None:
        content = text_pdf(pages=30)
        await parse_pool.submit(len, b"warm up the worker")

        with IoProbe() as io_probe:
            await asyncio.sleep(0.1)
            pages = await _processor().parse_document("long.pdf", content)

        assert len(pages) == 30
        assert isinstance(pages[0], ParsedPageData)
        assert 0 < io_probe.worst < 0.25


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
