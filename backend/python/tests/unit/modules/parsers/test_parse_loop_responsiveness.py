"""Blocking parse work must not stall the event loop (heartbeat tests).

The indexing pipeline shares one loop between all in-flight records, so a
synchronous PDF open or a large table walk delays every other record.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pdfplumber
import pytest

from app.models.blocks import BlockType
from app.modules.parsers.pdf import pdfplumber_opencv_processor, vlm_ocr_strategy
from app.modules.parsers.pdf.layout_pages import ParsedPageData
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    LayoutRegion,
    LayoutRegionType,
)
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from app.utils.table_enrichment import TableEnrichmentResult
from tests.support.pdf_samples import blank_pdf

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

MAX_GAP_SECONDS = 0.2


async def _worst_loop_gap(coro_factory: Callable[[], Awaitable[object]]) -> tuple[float, object]:
    worst = 0.0
    stop = False

    async def heartbeat() -> None:
        nonlocal worst
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    task = asyncio.create_task(heartbeat())  # test-local task, awaited below
    await asyncio.sleep(0.05)
    try:
        result = await coro_factory()
    finally:
        stop = True
        await task
    return worst, result


def _strategy() -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("test-loop"), MagicMock())
    strategy._get_multimodal_llm = AsyncMock(return_value=MagicMock())
    strategy._preprocess_document = AsyncMock(return_value={})
    return strategy


@pytest.fixture
def slow_open(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = pdfplumber.open

    def slow(*args: object, **kwargs: object) -> object:
        time.sleep(0.5)  # a scanned or damaged file makes the open this slow
        return real_open(*args, **kwargs)

    monkeypatch.setattr(vlm_ocr_strategy.pdfplumber, "open", slow)


class TestVlmLoadDocument:
    async def test_opening_the_pdf_does_not_block_the_loop(self, slow_open: None) -> None:
        strategy = _strategy()
        worst, _ = await _worst_loop_gap(lambda: strategy.load_document(blank_pdf(pages=2)))
        assert worst < MAX_GAP_SECONDS, worst

    async def test_the_temp_file_is_removed_and_state_cleared(self) -> None:
        strategy = _strategy()
        seen: list[str] = []

        async def preprocess() -> dict:
            seen.append(strategy._pdf_path)
            assert os.path.exists(strategy._pdf_path)
            assert len(strategy.doc.pages) == 3
            return {"ok": True}

        strategy._preprocess_document = preprocess
        await strategy.load_document(blank_pdf(pages=3))
        assert strategy.document_analysis_result == {"ok": True}
        assert not os.path.exists(seen[0])
        assert strategy.doc is None and strategy._pdf_path is None

    async def test_the_temp_file_is_removed_when_preprocessing_fails(self) -> None:
        strategy = _strategy()
        seen: list[str] = []

        async def preprocess() -> dict:
            seen.append(strategy._pdf_path)
            raise RuntimeError("boom")

        strategy._preprocess_document = preprocess
        with pytest.raises(RuntimeError):
            await strategy.load_document(blank_pdf(pages=1))
        assert not os.path.exists(seen[0])

    async def test_the_temp_file_is_removed_when_the_pdf_cannot_be_opened(self, monkeypatch: pytest.MonkeyPatch) -> None:
        paths: list[str] = []
        def failing(path: str, *a: object, **k: object) -> object:
            paths.append(path)
            raise ValueError("damaged")

        monkeypatch.setattr(vlm_ocr_strategy.pdfplumber, "open", failing)
        with pytest.raises(ValueError):
            await _strategy().load_document(b"%PDF-broken")
        assert paths and not os.path.exists(paths[0])

    @staticmethod
    def _signalling_open_and_release(monkeypatch: pytest.MonkeyPatch) -> "tuple[threading.Event, threading.Event, list[str], list[int]]":
        """Make the open slow and signal when it starts; record which thread runs each release."""
        started, released = threading.Event(), threading.Event()
        paths: list[str] = []
        release_threads: list[int] = []
        real_open = pdfplumber.open
        real_release = VLMOCRStrategy._release_pdf_sync

        def slow(path: str, *args: object, **kwargs: object) -> object:
            paths.append(path)
            started.set()
            time.sleep(0.3)
            return real_open(path, *args, **kwargs)

        def recording_release(path: str | None, doc: object) -> None:
            release_threads.append(threading.get_ident())
            real_release(path, doc)
            released.set()

        monkeypatch.setattr(vlm_ocr_strategy.pdfplumber, "open", slow)
        monkeypatch.setattr(VLMOCRStrategy, "_release_pdf_sync", staticmethod(recording_release))
        return started, released, paths, release_threads

    async def test_cancellation_while_opening_releases_the_file_off_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started, released, paths, release_threads = self._signalling_open_and_release(monkeypatch)
        task = asyncio.create_task(_strategy().load_document(blank_pdf(pages=1)))  # test-local
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(released.wait, 5)
        assert paths and not os.path.exists(paths[0])
        assert threading.get_ident() not in release_threads

    async def test_a_file_handed_over_just_before_the_cancel_is_released(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started, released, paths, release_threads = self._signalling_open_and_release(monkeypatch)
        task = asyncio.create_task(_strategy().load_document(blank_pdf(pages=1)))  # test-local
        assert await asyncio.to_thread(started.wait, 5)
        time.sleep(1.0)  # block the loop: the thread finishes and hands the file over first
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(released.wait, 5)
        assert paths and not os.path.exists(paths[0])
        assert threading.get_ident() not in release_threads

    def test_the_file_is_released_when_the_loop_closes_during_the_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started, released, paths, _ = self._signalling_open_and_release(monkeypatch)
        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(_strategy().load_document(blank_pdf(pages=1)))  # test-local
            loop.run_until_complete(asyncio.wait_for(asyncio.to_thread(started.wait, 5), 6))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                loop.run_until_complete(task)
        finally:
            loop.close()  # a worker loop shutting down: pending callbacks never run
        assert released.wait(5)
        assert paths and not os.path.exists(paths[0])


def _table_region(rows: int, cols: int = 6) -> LayoutRegion:
    grid = [[f"h{c}" for c in range(cols)]] + [
        [f"cell {r}-{c} " + "x" * 20 for c in range(cols)] for r in range(rows)
    ]
    return LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 100, 100), table_grid=grid)


def _page(region: LayoutRegion) -> ParsedPageData:
    return ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[region])


def _processor() -> PDFPlumberOpenCVProcessor:
    return PDFPlumberOpenCVProcessor(logging.getLogger("test-loop"), MagicMock())


@pytest.fixture
def frozen_heap() -> Iterator[None]:
    """Keep a full GC pass over the suite's large heap out of the measurement.

    A generation-2 collection holds the GIL for as long as the heap is big, whichever
    thread triggers it; that is a property of the test process, not of the code under test.
    """
    gc.collect()
    gc.freeze()
    yield
    gc.unfreeze()


class TestTableRows:
    async def test_a_large_table_does_not_block_the_loop(self, frozen_heap: None) -> None:
        region = _table_region(rows=20_000)
        proc = _processor()
        worst, container = await _worst_loop_gap(
            lambda: proc.create_blocks([_page(region)], skip_llm_enrichment=True)
        )
        assert len(container.blocks) == 20_000
        assert worst < MAX_GAP_SECONDS, worst

    async def test_row_text_is_unchanged(self) -> None:
        region = LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=(0, 0, 100, 100),
            table_grid=[["A", "B"], ["1", "2"], ["3", None], ["4", "5", "6"]],
        )
        container = await _processor().create_blocks([_page(region)], skip_llm_enrichment=True)
        rows = [b for b in container.blocks if b.type == BlockType.TABLE_ROW]
        assert [b.data["row_natural_language_text"] for b in rows] == [
            "A: 1, B: 2",
            "A: 3, B: ",
            "A: 4, B: 5, Column_3: 6",
        ]
        assert [b.data["row_number"] for b in rows] == [1, 2, 3]

    async def test_an_offloaded_table_matches_an_inline_one(self) -> None:
        def build(rows: int) -> Awaitable[object]:
            grid = [["A", "B"]] + [[f"a{r}", f"b{r}"] for r in range(rows)]
            region = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 100, 100), table_grid=grid)
            return _processor().create_blocks([_page(region)], skip_llm_enrichment=True)

        small = await build(3)
        big = await build(2500)  # above the offload threshold
        for container, n in ((small, 3), (big, 2500)):
            assert [b.index for b in container.blocks] == list(range(n))
            assert [b.data["row_number"] for b in container.blocks] == list(range(1, n + 1))
            assert container.block_groups[0].children.block_ranges[0].start == 0
            assert container.block_groups[0].children.block_ranges[-1].end == n - 1
        assert big.blocks[2499].data["row_natural_language_text"] == "A: a2499, B: b2499"
        assert all(b.parent_index == 0 for b in big.blocks)

    @pytest.mark.parametrize("rows", [10, 2500])  # below and above the offload threshold
    async def test_rows_with_empty_enriched_text_are_skipped(
        self, rows: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        texts = ["" if i % 3 == 0 else f"t{i}" for i in range(rows)]

        async def enrich(llm: object, grids: list, logger: object = None) -> list[TableEnrichmentResult]:
            return [TableEnrichmentResult(summary="s", headers=["h"], descriptions=texts) for _ in grids]

        monkeypatch.setattr(
            pdfplumber_opencv_processor, "get_llm_for_role", AsyncMock(return_value=(MagicMock(), None))
        )
        monkeypatch.setattr(pdfplumber_opencv_processor, "enrich_tables", enrich)
        region = LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=(0, 0, 100, 100),
            table_grid=[["h"]] + [["x"] for _ in range(rows)],
        )
        container = await _processor().create_blocks([_page(region)])
        kept = [(i + 1, t) for i, t in enumerate(texts) if t]
        assert [b.index for b in container.blocks] == list(range(len(kept)))
        assert [b.data["row_number"] for b in container.blocks] == [n for n, _ in kept]
        assert [b.data["row_natural_language_text"] for b in container.blocks] == [t for _, t in kept]
