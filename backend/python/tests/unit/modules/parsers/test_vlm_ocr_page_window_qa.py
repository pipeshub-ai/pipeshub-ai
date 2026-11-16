"""QA additions for the VLM OCR page window: cancellation, edge sizes, window bound, no idle slots."""
from __future__ import annotations

import asyncio
import gc
import logging
import weakref
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, scope="module")
def _collect_only_new_objects() -> Iterator[None]:
    # A full collection walks every object of the imported backend (~0.9 s each);
    # the images under test are all created after the freeze.
    gc.freeze()
    yield
    gc.unfreeze()


class _Image(str):
    """A page image whose lifetime can be watched."""


def _strategy(
    pages: int, batch: int, concurrency: int = 3
) -> tuple[VLMOCRStrategy, weakref.WeakSet[_Image], list[tuple[str, int]], list[list[int]]]:
    strategy = VLMOCRStrategy(logging.getLogger("test-window-qa"), MagicMock())
    strategy.PAGE_RENDER_BATCH_SIZE = batch
    strategy.CONCURRENCY_LIMIT = concurrency
    strategy.doc = MagicMock(pages=[MagicMock(width=1, height=2) for _ in range(pages)])
    strategy._pdf_path = "/unused.pdf"
    live: weakref.WeakSet[_Image] = weakref.WeakSet()
    events: list[tuple[str, int]] = []
    render_calls: list[list[int]] = []

    def render(numbers: list[int]) -> dict[int, str]:
        render_calls.append(list(numbers))
        out = {}
        for n in numbers:
            events.append(("render", n))
            image = _Image(f"data:image/png;base64,p{n}" + "x" * 500)
            live.add(image)
            out[n] = image
        return out

    strategy._render_page_batch_to_base64 = render  # type: ignore[method-assign]
    return strategy, live, events, render_calls


class TestCancellationMidBatch:
    async def test_cancel_while_calls_are_in_flight_leaves_no_images_and_no_tasks(self) -> None:
        strategy, live, _events, _calls = _strategy(pages=12, batch=4)
        started = asyncio.Event()
        gate = asyncio.Event()  # never set: calls hang until cancelled

        async def call_llm(image_base64: str, page_number: int) -> str:
            started.set()
            await gate.wait()
            return "never"

        strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        job = asyncio.create_task(strategy._preprocess_document())
        await started.wait()
        await asyncio.sleep(0)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        await asyncio.sleep(0)

        assert strategy._page_images == {}
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert [t for t in others if not t.done()] == []
        gc.collect()
        assert len(live) == 0


class TestBatchShapes:
    @pytest.mark.parametrize(
        ("pages", "batch"),
        [(1, 20), (3, 20), (8, 4), (9, 4), (5, 1), (20, 20), (21, 20)],
    )
    async def test_every_page_rendered_once_in_order_and_never_more_than_a_batch(
        self, pages: int, batch: int
    ) -> None:
        strategy, live, _events, render_calls = _strategy(pages, batch)

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(0)
            assert f"p{page_number}x" in image_base64  # the page's own image
            return f"md{page_number}"

        strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        result = await strategy._preprocess_document()

        assert all(1 <= len(c) <= batch for c in render_calls)
        assert [n for c in render_calls for n in c] == list(range(1, pages + 1))
        assert [p["page_number"] for p in result["pages"]] == list(range(1, pages + 1))
        assert [p["markdown"] for p in result["pages"]] == [f"md{n}" for n in range(1, pages + 1)]
        assert result["total_pages"] == pages
        assert strategy._page_images == {}
        gc.collect()
        assert len(live) == 0

    async def test_images_alive_never_exceed_the_window_at_any_model_call(self) -> None:
        strategy, live, _events, _calls = _strategy(pages=60, batch=8, concurrency=5)
        render = strategy._render_page_batch_to_base64
        peak = 0
        filled = False

        def render_and_count(numbers: list[int]) -> dict[int, str]:
            nonlocal peak
            out = render(numbers)
            # Images only become alive in a render, so the count's highest value is read here, not at whatever
            # moments the model calls happen to sample it. WeakSet len: refcounting drops images at once.
            peak = max(peak, len(live))
            return out

        async def call_llm(image_base64: str, page_number: int) -> str:
            nonlocal filled
            # The first calls are held until the render thread has recorded a full window. Left to the wall clock,
            # the 1-3 ms calls outran a render thread on a busy runner and the window never filled (peak 4).
            # The recorded peak, not len(live): images are alive before the thread samples them, and calls
            # released in between could drop some first.
            async with asyncio.timeout(20):  # bounds a hang only: a window that never fills fails here
                while not filled:
                    filled = peak >= 8
                    await asyncio.sleep(0.001)
            await asyncio.sleep(0.001 * (page_number % 4))
            return "x"

        strategy._render_page_batch_to_base64 = render_and_count  # type: ignore[method-assign]

        strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        await strategy._preprocess_document()

        assert peak == 8  # the window is used to the full and never exceeded

    async def test_a_slow_page_does_not_hold_up_the_other_slots(self) -> None:
        """A 24-page document, window 8, 4 slots: page 1's call does not end until page 9's
        has started. A per-batch barrier would hold page 9 until page 1 is done."""
        strategy, _live, _events, _calls = _strategy(pages=24, batch=8, concurrency=4)
        ninth_started = asyncio.Event()
        overlapped = False

        async def call_llm(image_base64: str, page_number: int) -> str:
            nonlocal overlapped
            if page_number == 9:
                ninth_started.set()
            if page_number == 1:
                # Proved by an event, not timed (it was "elapsed < 0.6 s"). The wait bounds a hang only, and
                # does not raise: the page retry loop would run it three times.
                waiter = asyncio.ensure_future(ninth_started.wait())
                await asyncio.wait({waiter}, timeout=10)
                waiter.cancel()
                overlapped = ninth_started.is_set()
            await asyncio.sleep(0)
            return "x"

        strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        await strategy._preprocess_document()

        assert overlapped
