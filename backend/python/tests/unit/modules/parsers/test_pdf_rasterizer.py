"""Tests for thread-safe PDF rasterization helpers."""

import select
import subprocess
import sys
import time
from concurrent.futures.process import BrokenProcessPool
from io import BytesIO
from pathlib import Path
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


def test_count_chars_per_page_from_bytes_sync_uses_process_pool() -> None:
    with patch.object(rasterizer, "_run_in_pool", return_value=[5, 0]) as mock_run:
        result = rasterizer.count_chars_per_page_from_bytes_sync(b"%PDF")

    mock_run.assert_called_once_with(rasterizer._worker_count_chars_from_bytes, b"%PDF")
    assert result == [5, 0]


def test_worker_count_chars_from_bytes_with_reportlab_pdf() -> None:
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 700, "hello")
    c.showPage()
    c.rect(72, 600, 200, 100, fill=1)
    c.showPage()
    c.save()

    text_page, shapes_page = rasterizer._worker_count_chars_from_bytes(buf.getvalue())
    assert text_page >= len("hello")
    assert shapes_page == 0


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


class TestRasterWorkerSizing:
    """The default pool size follows the container-aware CPU count, not the host's."""

    def test_follows_effective_cpu_count_not_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PDF_RASTER_WORKERS", raising=False)
        monkeypatch.setattr(rasterizer.os, "cpu_count", lambda: 64)
        monkeypatch.setattr(rasterizer, "effective_cpu_count", lambda: 1)
        assert rasterizer._get_pdf_raster_worker_count() == 1

    def test_still_capped_at_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PDF_RASTER_WORKERS", raising=False)
        monkeypatch.setattr(rasterizer, "effective_cpu_count", lambda: 16)
        assert rasterizer._get_pdf_raster_worker_count() == 2

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PDF_RASTER_WORKERS", "5")
        monkeypatch.setattr(rasterizer, "effective_cpu_count", lambda: 1)
        assert rasterizer._get_pdf_raster_worker_count() == 5

    def test_pool_size_is_scaled_share(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rasterizer, "PDF_RASTER_WORKERS", 4)
        monkeypatch.setattr(rasterizer, "scaled", lambda n: n // 2)
        assert rasterizer.pdf_raster_pool_size() == 2


def test_run_and_release_flags_the_task_for_a_trim_even_when_it_fails() -> None:
    rasterizer._task_ended.clear()
    assert rasterizer._run_and_release(sum, [1, 2]) == 3
    assert rasterizer._task_ended.is_set()

    rasterizer._task_ended.clear()
    with pytest.raises(ZeroDivisionError):
        rasterizer._run_and_release(divmod, 1, 0)
    assert rasterizer._task_ended.is_set()


def test_housekeeping_trims_twice_after_a_task_and_exits_once_orphaned() -> None:
    rasterizer._task_ended.set()
    with patch.object(rasterizer.os, "getppid", side_effect=[1, 1, 1, 1, 2]), \
            patch.object(rasterizer.time, "sleep"), \
            patch.object(rasterizer, "_release_freed_memory") as trim, \
            patch.object(rasterizer.os, "_exit") as exit_, \
            patch.object(rasterizer.threading, "Thread") as thread:
        rasterizer._start_worker_housekeeping(1)
        thread.call_args.kwargs["target"]()

    # Twice for the one task, then no more while idle.
    assert trim.call_count == 2
    exit_.assert_called_once_with(0)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_pool_workers_exit_when_their_parent_is_killed() -> None:
    code = (
        "import os, time\n"
        "from app.modules.parsers.pdf import pdf_rasterizer as r\n"
        "print(r._run_in_pool(os.getpid), flush=True)\n"
        "time.sleep(600)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(rasterizer.__file__).parents[4],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([parent.stdout], [], [], 20)
        assert ready, "the pool never reported a worker pid"
        worker_pid = int(parent.stdout.readline())
        assert _alive(worker_pid)
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()

    deadline = time.monotonic() + 10
    while _alive(worker_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(worker_pid)
