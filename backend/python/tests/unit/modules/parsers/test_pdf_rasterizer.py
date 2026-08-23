"""Tests for thread-safe PDF rasterization helpers."""

from concurrent.futures.process import BrokenProcessPool
from io import BytesIO
from types import SimpleNamespace
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


def test_effective_resolution_leaves_standard_pages_untouched():
    for width, height in ((595.0, 842.0), (612.0, 792.0), (1191.0, 1684.0)):
        page = SimpleNamespace(width=width, height=height)
        assert rasterizer._effective_resolution(page, 150) == 150


def test_effective_resolution_clamps_large_format_sheet():
    page = SimpleNamespace(width=3543.31, height=2551.18)

    effective = rasterizer._effective_resolution(page, 150)

    assert effective < 150
    pixels = (page.width * effective / 72.0) * (page.height * effective / 72.0)
    assert pixels <= rasterizer.MAX_RASTER_PIXELS


def test_effective_resolution_tolerates_unusable_page_dimensions():
    assert rasterizer._effective_resolution(SimpleNamespace(), 150) == 150
    assert rasterizer._effective_resolution(
        SimpleNamespace(width=0, height=800), 150
    ) == 150


def test_clamped_render_returns_scale_matching_the_raster():
    """A clamped page must report the scale of the raster it actually produced;
    returning the requested resolution would misplace every downstream crop."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A1
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A1)
    c.drawString(72, 700, "large format")
    c.showPage()
    c.save()

    arr, scale = rasterizer._worker_render_page_from_bytes(buf.getvalue(), 1, 150)

    height_px, width_px = arr.shape[:2]
    # Each dimension is rounded up by the renderer, so allow a rounding margin.
    assert width_px * height_px <= rasterizer.MAX_RASTER_PIXELS * 1.01
    assert scale < 150 / 72.0
    assert width_px == pytest.approx(A1[0] * scale, abs=2)
    assert height_px == pytest.approx(A1[1] * scale, abs=2)


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


def test_broken_process_pool_reports_memory_incident_when_governor_wired():
    """A worker OOM-kill must feed the governor's fast incident path rather
    than only being logged — see resource_governor.controller.
    ResourceGovernor.report_memory_incident."""
    mock_pool = MagicMock()
    mock_future = MagicMock()
    mock_future.result.side_effect = BrokenProcessPool("worker killed")
    mock_pool.submit.return_value = mock_future
    mock_governor = MagicMock()

    rasterizer.set_resource_governor(mock_governor)
    try:
        with patch.object(rasterizer, "_get_pdf_raster_pool", return_value=mock_pool):
            with pytest.raises(BrokenProcessPool):
                rasterizer._run_in_pool(lambda: None)

        mock_governor.report_memory_incident.assert_called_once()
    finally:
        rasterizer.set_resource_governor(None)


def test_broken_process_pool_without_governor_does_not_raise():
    """No governor wired (e.g. standalone/test runs) must stay a no-op, not
    an AttributeError."""
    mock_pool = MagicMock()
    mock_future = MagicMock()
    mock_future.result.side_effect = BrokenProcessPool("worker killed")
    mock_pool.submit.return_value = mock_future

    assert rasterizer._resource_governor is None
    with patch.object(rasterizer, "_get_pdf_raster_pool", return_value=mock_pool):
        with pytest.raises(BrokenProcessPool):
            rasterizer._run_in_pool(lambda: None)
