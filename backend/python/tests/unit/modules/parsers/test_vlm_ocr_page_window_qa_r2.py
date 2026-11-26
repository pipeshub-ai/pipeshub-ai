"""QA round 2 for the rolling OCR page window: exact bound, deadlock shapes, producer failure, cancellation."""
from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import random
import threading
import time
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


class _Rig:
    def __init__(self, pages: int, window: int, concurrency: int) -> None:
        logger = logging.getLogger("test-window-qa-r2")
        logger.disabled = True  # pytest log capture would keep records, and their exception args, alive
        self.strategy = VLMOCRStrategy(logger, MagicMock())
        self.strategy.PAGE_RENDER_BATCH_SIZE = window
        self.strategy.CONCURRENCY_LIMIT = concurrency
        self.strategy.doc = MagicMock(pages=[MagicMock(width=1, height=2) for _ in range(pages)])
        self.strategy._pdf_path = "/unused.pdf"
        self.live: weakref.WeakSet[_Image] = weakref.WeakSet()
        self.render_calls: list[list[int]] = []
        self.peak_after_render = 0
        self.fail_render_at: int | None = None  # nth render call raises
        self.render_delay = 0.0
        self.renders_started = 0
        self._lock = threading.Lock()

        def render(numbers: list[int]) -> dict[int, str]:
            with self._lock:
                self.renders_started += 1
                nth = self.renders_started
                self.render_calls.append(list(numbers))
            if self.render_delay:
                time.sleep(self.render_delay)
            if self.fail_render_at == nth:
                raise RuntimeError("render failed")
            out = {}
            for n in numbers:
                image = _Image(f"data:image/png;base64,p{n}" + "x" * 200)
                self.live.add(image)
                out[n] = image
            with self._lock:
                self.peak_after_render = max(self.peak_after_render, len(self.live))
            return out

        self.strategy._render_page_batch_to_base64 = render  # type: ignore[method-assign]


def _no_pending_tasks() -> bool:
    return [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()] == []


class TestExactWindow:
    @pytest.mark.parametrize(("pages", "window", "concurrency"), [(30, 6, 10), (30, 6, 3), (40, 8, 8)])
    async def test_while_every_call_hangs_exactly_the_window_is_held_and_never_more(
        self, pages: int, window: int, concurrency: int
    ) -> None:
        rig = _Rig(pages, window, concurrency)
        gate = asyncio.Event()
        in_call = 0

        async def call_llm(image_base64: str, page_number: int) -> str:
            nonlocal in_call
            in_call += 1
            await gate.wait()
            return f"md{page_number}"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        job = asyncio.create_task(rig.strategy._preprocess_document())
        for _ in range(60):
            await asyncio.sleep(0.01)
        held = len(rig.live)
        assert held == window, "the window is the exact cap while calls hang"
        assert len(rig.strategy._page_images) == window
        gate.set()
        result = await asyncio.wait_for(job, 10)
        assert [p["markdown"] for p in result["pages"]] == [f"md{n}" for n in range(1, pages + 1)]
        assert rig.peak_after_render <= window
        assert rig.strategy._page_images == {}


class TestNoDeadlock:
    @pytest.mark.parametrize(
        ("pages", "window", "concurrency"),
        [
            (10, 1, 10),  # W = 1
            (10, 2, 5),  # W // 4 == 0 -> chunk of 1
            (10, 3, 10),  # W < concurrency
            (3, 20, 10),  # pages < W
            (1, 20, 10),
            (7, 4, 1),  # one worker
            (25, 5, 25),  # concurrency == pages
        ],
    )
    @pytest.mark.parametrize("mode", ["ok", "all_fail", "some_fail"])
    async def test_completes_in_order_and_releases_everything(
        self, pages: int, window: int, concurrency: int, mode: str
    ) -> None:
        rig = _Rig(pages, window, concurrency)

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(0)
            if mode == "all_fail" or (mode == "some_fail" and page_number % 3 == 0):
                raise ValueError(f"bad{page_number}")
            return f"md{page_number}"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        if mode == "all_fail":
            try:
                await asyncio.wait_for(rig.strategy._preprocess_document(), 15)
                raise AssertionError("every page failing must fail the record")
            except ValueError:
                pass  # no `as`: a held exception would keep its frames, and their images, alive
        else:
            result = await asyncio.wait_for(rig.strategy._preprocess_document(), 15)
            assert [p["page_number"] for p in result["pages"]] == list(range(1, pages + 1))
            del result
        await asyncio.sleep(0)
        assert rig.strategy._page_images == {}
        assert _no_pending_tasks()
        gc.collect()
        assert len(rig.live) == 0

    @pytest.mark.parametrize("fail_every", [2, 3])
    async def test_pages_that_fail_every_attempt_do_not_stretch_the_window(self, fail_every: int) -> None:
        """A failed page's exception (kept for the all-pages-failed check) must not keep its image."""
        rig = _Rig(pages=60, window=4, concurrency=4)

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(0)
            if page_number % fail_every == 0:
                raise ValueError(f"bad{page_number}")
            return "x"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        await asyncio.wait_for(rig.strategy._preprocess_document(), 30)
        assert rig.peak_after_render <= 4


    @pytest.mark.parametrize("fail_every", [1, 2, 3])
    async def test_a_chained_page_error_does_not_keep_its_image(self, fail_every: int) -> None:
        """A provider error raised while handling another keeps that one as __context__,
        whose traceback holds the frame, and so the image, of the failed call."""
        rig = _Rig(pages=60, window=4, concurrency=4)

        def send(image_base64: str) -> None:
            raise KeyError("upstream")

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(0)
            if page_number % fail_every == 0:
                try:
                    send(image_base64)
                except KeyError:
                    raise ValueError(f"bad{page_number}")  # noqa: B904 - the implicit chain is the point
            return "x"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        try:
            await asyncio.wait_for(rig.strategy._preprocess_document(), 30)
        except ValueError:
            assert fail_every == 1  # every page failing fails the document
        gc.collect()
        assert rig.peak_after_render <= 4
        assert len(rig.live) == 0


class TestProducerFailure:
    @pytest.mark.parametrize("fail_at", [1, 2, 5])
    async def test_a_failing_render_raises_and_leaves_no_hung_worker(self, fail_at: int) -> None:
        rig = _Rig(pages=30, window=8, concurrency=5)
        rig.fail_render_at = fail_at

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(0.005)
            return "x"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="render failed"):
            await asyncio.wait_for(rig.strategy._preprocess_document(), 10)
        await asyncio.sleep(0)
        assert _no_pending_tasks()
        assert rig.strategy._page_images == {}
        gc.collect()
        assert len(rig.live) == 0


class TestCancellation:
    @pytest.mark.parametrize("seed", range(25))
    async def test_cancel_at_a_random_point_leaves_nothing_running(self, seed: int) -> None:
        rnd = random.Random(seed)
        rig = _Rig(pages=40, window=rnd.choice([1, 2, 4, 8]), concurrency=rnd.choice([1, 3, 10]))
        rig.render_delay = 0.004

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(rnd.random() * 0.01)
            return "x"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        job = asyncio.create_task(rig.strategy._preprocess_document())
        await asyncio.sleep(rnd.random() * 0.08)
        job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(job, 10)
        assert _no_pending_tasks()
        assert rig.strategy._page_images == {}
        started = rig.renders_started
        await asyncio.sleep(0.1)  # a render thread cannot be interrupted; it must not feed anything back
        assert rig.renders_started == started, "no new render starts after cancellation"
        assert rig.strategy._page_images == {}
        gc.collect()
        assert len(rig.live) == 0


class TestLatePoolThread:
    """A pool thread keeps its job until it next runs; on a slow runner that is after
    the job has ended. Nothing it keeps may hold a page image."""

    @pytest.fixture(autouse=True)
    def _late_pool_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import concurrent.futures.thread as pool_thread

        run = pool_thread._WorkItem.run

        def run_then_linger(item: object) -> None:
            run(item)
            time.sleep(0.05)

        monkeypatch.setattr(pool_thread._WorkItem, "run", run_then_linger)

    @pytest.mark.parametrize("outcome", ["ok", "some_fail", "render_fails", "cancelled"])
    async def test_no_image_outlives_the_job(self, outcome: str) -> None:
        rig = _Rig(pages=10, window=2, concurrency=5)
        if outcome == "render_fails":
            rig.fail_render_at = 5

        async def call_llm(image_base64: str, page_number: int) -> str:
            await asyncio.sleep(0.01)
            if outcome == "some_fail" and page_number % 3 == 0:
                raise ValueError(f"bad{page_number}")
            return "x"

        rig.strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
        job = asyncio.ensure_future(rig.strategy._preprocess_document())
        if outcome == "cancelled":
            await asyncio.sleep(0.03)
            job.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await asyncio.wait_for(job, 10)
        del job
        await asyncio.sleep(0)
        assert rig.strategy._page_images == {}
        gc.collect()
        assert len(rig.live) == 0
