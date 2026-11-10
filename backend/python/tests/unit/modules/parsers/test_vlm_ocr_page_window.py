"""VLM OCR keeps one batch of page images alive at a time and nothing after the job."""
from __future__ import annotations

import asyncio
import gc
import hashlib
import logging
import weakref
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.pdf import vlm_ocr_strategy
from app.modules.parsers.pdf.pdf_rasterizer import _worker_render_batch_from_path
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from tests.support.pdf_samples import text_pdf

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, scope="module")
def _collect_only_new_objects() -> Iterator[None]:
    # A full collection walks every object of the imported backend (~0.9 s each);
    # the images under test are all created after the freeze.
    gc.freeze()
    yield
    gc.unfreeze()


def _numbered_pdf(pages: int) -> bytes:
    """Pages that differ from each other, so a swapped page is visible."""
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for n in range(1, pages + 1):
        pdf.setFont("Helvetica", 40 + n * 4)
        pdf.drawString(40, 400 + n * 20, f"Page {n}")
        pdf.showPage()
    pdf.save()
    return buf.getvalue()

BATCH = 4
PAGES = 23


class _Image(str):
    """A page image whose lifetime can be watched (a live counter, no gc needed)."""

    probe: _Probe

    def __del__(self) -> None:
        self.probe.alive -= 1


class _Probe:
    def __init__(self) -> None:
        self.live: weakref.WeakSet[_Image] = weakref.WeakSet()
        self.alive = 0
        self.peak_seen_by_llm = 0

    def sample(self) -> int:
        gc.collect()
        return len(self.live)


def _strategy(probe: _Probe, *, fail_on: int | None = None) -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("test-window"), MagicMock())
    strategy.PAGE_RENDER_BATCH_SIZE = BATCH
    strategy.CONCURRENCY_LIMIT = 3
    pages = [MagicMock(width=10, height=20) for _ in range(PAGES)]
    strategy.doc = MagicMock(pages=pages)
    strategy._pdf_path = "/unused.pdf"

    def render(page_numbers: list[int]) -> dict[int, str]:
        out = {}
        for n in page_numbers:
            image = _Image(f"data:image/png;base64,page{n}" + "x" * 1000)
            image.probe = probe
            probe.alive += 1
            probe.live.add(image)
            out[n] = image
        return out

    async def call_llm(image_base64: str, page_number: int) -> str:
        probe.peak_seen_by_llm = max(probe.peak_seen_by_llm, probe.alive)
        await asyncio.sleep(0)
        if page_number == fail_on:
            raise vlm_ocr_strategy.LLMUnavailableError("down")
        return f"md{page_number}:{image_base64[-3:]}"

    strategy._render_page_batch_to_base64 = render  # type: ignore[method-assign]
    strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]
    return strategy


class TestPageWindow:
    async def test_page_images_alive_at_once_are_bounded_by_the_batch(self) -> None:
        probe = _Probe()
        strategy = _strategy(probe)

        result = await strategy._preprocess_document()

        assert result["total_pages"] == PAGES
        assert 0 < probe.peak_seen_by_llm <= BATCH
        # Before the change every page was alive during the LLM calls (PAGES).

    async def test_nothing_is_held_after_the_job(self) -> None:
        probe = _Probe()
        strategy = _strategy(probe)

        result = await strategy._preprocess_document()

        assert strategy._page_images == {}
        assert probe.sample() == 0
        # Results carry the markdown only, never the image.
        assert all("data:image" not in str(p) for p in result["pages"])

    async def test_an_error_mid_document_releases_the_images(self) -> None:
        probe = _Probe()
        strategy = _strategy(probe, fail_on=10)

        try:
            await strategy._preprocess_document()
        except vlm_ocr_strategy.LLMUnavailableError:
            pass  # a handled error keeps no traceback, so nothing of it holds a frame
        else:
            pytest.fail("expected LLMUnavailableError")

        assert strategy._page_images == {}
        await asyncio.sleep(0)  # let the loop drop its finished-task callbacks
        assert probe.sample() == 0

    async def test_load_document_releases_images_when_the_model_fails(self) -> None:
        probe = _Probe()
        strategy = _strategy(probe, fail_on=2)
        strategy._pdf_path = None

        async def get_llm() -> MagicMock:
            return MagicMock()

        strategy._get_multimodal_llm = get_llm  # type: ignore[method-assign]
        try:
            await strategy.load_document(text_pdf(pages=PAGES))
        except vlm_ocr_strategy.LLMUnavailableError:
            pass
        else:
            pytest.fail("expected LLMUnavailableError")
        assert strategy._page_images == {}
        await asyncio.sleep(0)
        assert probe.sample() == 0

    async def test_output_matches_a_real_render_page_for_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Real pdfium render, stub model: same pages, same order, same per-page content."""
        monkeypatch.setattr(
            vlm_ocr_strategy,
            "render_batch_from_path_sync",
            lambda path, numbers, dpi: _worker_render_batch_from_path(path, numbers, dpi),
        )
        monkeypatch.setenv("VLM_OCR_MAX_PAGES", "50")
        strategy = VLMOCRStrategy(logging.getLogger("test-window"), MagicMock())
        strategy.PAGE_RENDER_BATCH_SIZE = 3
        strategy.RENDER_DPI = 30
        seen: list[tuple[int, str]] = []

        async def call_llm(image_base64: str, page_number: int) -> str:
            seen.append((page_number, hashlib.sha256(image_base64.encode()).hexdigest()[:12]))
            return f"p{page_number}"

        strategy._call_llm_for_markdown = call_llm  # type: ignore[method-assign]

        async def get_llm() -> MagicMock:
            return MagicMock()

        strategy._get_multimodal_llm = get_llm  # type: ignore[method-assign]
        await strategy.load_document(_numbered_pdf(8))

        out = strategy.document_analysis_result
        assert [p["page_number"] for p in out["pages"]] == list(range(1, 9))
        assert [p["markdown"] for p in out["pages"]] == [f"p{n}" for n in range(1, 9)]
        assert out["markdown"] == "\n\n---\n\n".join(f"p{n}" for n in range(1, 9))
        assert sorted(n for n, _ in seen) == list(range(1, 9))
        assert len({h for _, h in seen}) == 8  # every page got its own image
