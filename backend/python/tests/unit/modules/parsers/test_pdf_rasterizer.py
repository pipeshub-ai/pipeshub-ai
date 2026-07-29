"""Tests for thread-safe PDF rasterization helpers."""

from concurrent.futures.process import BrokenProcessPool
from io import BytesIO
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.modules.parsers.pdf import pdf_rasterizer as rasterizer


@pytest.fixture(autouse=True)
def _reset_pool_cache():
    rasterizer.shutdown_pdf_raster_pool()
    yield
    rasterizer.shutdown_pdf_raster_pool()


def test_render_all_pages_from_bytes_sync_uses_process_pool():
    fake_pages = {1: (np.zeros((4, 4, 3), dtype=np.uint8), 2.0)}

    with patch.object(
        rasterizer,
        "_run_in_pool",
        return_value=fake_pages,
    ) as mock_run:
        result = rasterizer.render_all_pages_from_bytes_sync(b"%PDF", resolution=144)

    mock_run.assert_called_once_with(
        rasterizer._worker_render_all_from_bytes,
        b"%PDF",
        144,
    )
    assert result == fake_pages


def test_render_page_from_path_sync_uses_process_pool():
    fake_page = (np.ones((2, 2, 3), dtype=np.uint8), 1.0)

    with patch.object(rasterizer, "_run_in_pool", return_value=fake_page) as mock_run:
        result = rasterizer.render_page_from_path_sync("/tmp/test.pdf", 2, resolution=72)

    mock_run.assert_called_once_with(
        rasterizer._worker_render_page_from_path,
        "/tmp/test.pdf",
        2,
        72,
    )
    assert result == fake_page


@pytest.mark.asyncio
async def test_render_all_pages_as_pil_from_bytes():
    arr = np.zeros((3, 3, 3), dtype=np.uint8)
    fake_pil = MagicMock()

    with patch.object(
        rasterizer,
        "render_all_pages_from_bytes",
        return_value={1: (arr, 1.0)},
    ), patch.object(rasterizer.Image, "fromarray", return_value=fake_pil):
        images = await rasterizer.render_all_pages_as_pil_from_bytes(b"%PDF", resolution=72)

    assert images == [fake_pil]


def test_shutdown_pdf_raster_pool_noop_when_uninitialized():
    assert rasterizer.shutdown_pdf_raster_pool() is False


def test_worker_render_page_from_bytes_with_reportlab_pdf():
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 700, "hello")
    c.showPage()
    c.save()

    arr, scale = rasterizer._worker_render_page_from_bytes(buf.getvalue(), 1, 72)
    assert arr.shape[2] == 3
    assert scale == 1.0


def test_render_batch_from_path_sync_uses_process_pool():
    fake_pages = {2: (np.zeros((4, 4, 3), dtype=np.uint8), 2.0)}

    with patch.object(
        rasterizer,
        "_run_in_pool",
        return_value=fake_pages,
    ) as mock_run:
        result = rasterizer.render_batch_from_path_sync(
            "/tmp/test.pdf", [2], resolution=144
        )

    mock_run.assert_called_once_with(
        rasterizer._worker_render_batch_from_path,
        "/tmp/test.pdf",
        [2],
        144,
    )
    assert result == fake_pages


def test_broken_process_pool_clears_cache_and_reraises():
    """BrokenProcessPool should clear the cached pool and re-raise."""
    mock_pool = MagicMock()
    mock_future = MagicMock()
    mock_future.result.side_effect = BrokenProcessPool("worker killed")
    mock_pool.submit.return_value = mock_future

    with patch.object(
        rasterizer, "_get_pdf_raster_pool", return_value=mock_pool
    ) as mock_get_pool:
        with pytest.raises(BrokenProcessPool):
            rasterizer._run_in_pool(lambda: None)

        mock_get_pool.cache_clear.assert_called_once()


class TestIterPagesAsPilFromBytes:
    def test_yields_one_image_per_page_in_order(self):
        arrays = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(3)]
        calls: list[int] = []

        def _fake_render(pdf_bytes, page_number, resolution):
            calls.append(page_number)
            return arrays[page_number - 1], 1.0

        with patch.object(rasterizer, "render_page_from_bytes_sync", side_effect=_fake_render):
            images = list(rasterizer.iter_pages_as_pil_from_bytes(b"%PDF", 3, resolution=144))

        assert calls == [1, 2, 3]
        assert len(images) == 3
        for idx, image in enumerate(images):
            assert np.array_equal(np.array(image), arrays[idx])

    def test_is_a_true_generator_not_a_list_builder(self):
        """The whole point of this helper over
        `render_all_pages_as_pil_from_bytes_sync` is that a caller can
        consume-and-discard one page at a time without ever holding more
        than one rasterized page in memory — verify pages are rendered
        lazily, one per `next()`, not all up front."""
        render_calls: list[int] = []

        def _fake_render(pdf_bytes, page_number, resolution):
            render_calls.append(page_number)
            return np.zeros((2, 2, 3), dtype=np.uint8), 1.0

        with patch.object(rasterizer, "render_page_from_bytes_sync", side_effect=_fake_render):
            gen = rasterizer.iter_pages_as_pil_from_bytes(b"%PDF", 3, resolution=144)
            assert render_calls == []  # nothing rendered before first next()

            next(gen)
            assert render_calls == [1]

            next(gen)
            assert render_calls == [1, 2]

    def test_zero_page_count_yields_nothing(self):
        with patch.object(rasterizer, "render_page_from_bytes_sync") as mock_render:
            images = list(rasterizer.iter_pages_as_pil_from_bytes(b"%PDF", 0))
        assert images == []
        mock_render.assert_not_called()


class TestPdfRasterWorkerCount:
    def test_defaults_to_min_cpu_count_and_two(self, monkeypatch):
        monkeypatch.delenv("PDF_RASTER_WORKERS", raising=False)
        monkeypatch.setattr(rasterizer.os, "cpu_count", lambda: 8)
        assert rasterizer._get_pdf_raster_worker_count() == 2

    def test_defaults_to_one_on_a_single_cpu_host(self, monkeypatch):
        monkeypatch.delenv("PDF_RASTER_WORKERS", raising=False)
        monkeypatch.setattr(rasterizer.os, "cpu_count", lambda: 1)
        assert rasterizer._get_pdf_raster_worker_count() == 1

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("PDF_RASTER_WORKERS", "1")
        assert rasterizer._get_pdf_raster_worker_count() == 1

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PDF_RASTER_WORKERS", "not-a-number")
        monkeypatch.setattr(rasterizer.os, "cpu_count", lambda: 4)
        assert rasterizer._get_pdf_raster_worker_count() == 2

    def test_env_var_is_floored_at_one(self, monkeypatch):
        monkeypatch.setenv("PDF_RASTER_WORKERS", "0")
        assert rasterizer._get_pdf_raster_worker_count() == 1


class TestPdfRasterPoolRecycling:
    def test_pool_is_constructed_with_max_tasks_per_child(self):
        """Worker recycling (`max_tasks_per_child`) bounds how long a
        worker that rasterized a huge/scanned PDF can hold its peak
        pdfium/PIL heap before being replaced — verify the pool is actually
        constructed with this cap wired through."""
        with patch.object(rasterizer, "ProcessPoolExecutor") as mock_executor_cls:
            rasterizer._get_pdf_raster_pool()

        _, kwargs = mock_executor_cls.call_args
        assert kwargs["max_tasks_per_child"] == rasterizer.PDF_RASTER_MAX_TASKS_PER_CHILD
        assert rasterizer.PDF_RASTER_MAX_TASKS_PER_CHILD == 50
