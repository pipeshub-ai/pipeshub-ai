"""The pdfium-only page count taken ahead of any whole-file inspection."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers import parse_pool
from app.modules.parsers.pdf.page_count import (
    Unavailable,
    Unreadable,
    count_pages,
    count_pdf_pages,
)
from tests.support.pdf_samples import password_protected_pdf, text_pdf

if TYPE_CHECKING:
    from collections.abc import Iterator


def _no_pages() -> bytes:
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    canvas.Canvas(buffer).save()
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _worker_runs_inline(request: pytest.FixtureRequest) -> Iterator[None]:
    """The count runs only in a worker process; these tests run the worker function in this one."""
    if "no_worker" in request.node.name or "pool" in request.node.name or "submit" in request.node.name:
        yield
        return

    async def inline(fn: object, *args: object, **_kw: object) -> object:
        return fn(*args)

    with patch.object(parse_pool, "submit", new=inline), patch.object(parse_pool, "parse_pool_workers", return_value=1):
        yield


def test_the_worker_function_counts_the_pages() -> None:
    assert count_pages(text_pdf(3)) == 3


@pytest.mark.asyncio
async def test_a_count_through_a_pool_is_an_int() -> None:
    async def through_pool(fn: object, *args: object, **kw: object) -> int:
        assert kw["process_only"] is True
        return fn(*args)

    with patch.object(parse_pool, "submit", new=through_pool), patch.object(parse_pool, "parse_pool_workers", return_value=1):
        assert await count_pdf_pages(text_pdf(4), "t.pdf") == 4


@pytest.mark.asyncio
async def test_a_locked_file_raises_the_password_error() -> None:
    with pytest.raises(PasswordProtectedError):
        await count_pdf_pages(password_protected_pdf(), "locked.pdf")


@pytest.mark.asyncio
async def test_a_file_pdfium_cannot_open_is_unreadable() -> None:
    assert isinstance(await count_pdf_pages(b"not a pdf at all", "x.pdf"), Unreadable)


def test_a_pdf_with_no_pages_right_after_a_locked_one_is_not_reported_as_locked() -> None:
    """pdfium fails a load with no pages without setting an error, so the thread's previous code (a locked file) would
    be read as this file's. ``open_pdfium_document`` resets it."""
    import pypdfium2

    with pytest.raises(PasswordProtectedError):
        count_pages(password_protected_pdf())
    with pytest.raises(pypdfium2.PdfiumError):
        count_pages(_no_pages())


@pytest.mark.asyncio
async def test_a_zero_page_count_is_unreadable_never_a_page_count() -> None:
    with patch.object(parse_pool, "submit", new=AsyncMock(return_value=0)):
        assert isinstance(await count_pdf_pages(b"x", "z.pdf"), Unreadable)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [parse_pool.ParseWorkerError("overran", "timeout"), parse_pool.ParseAbandonedError("closed"), MemoryError()],
)
async def test_an_infrastructure_failure_is_unavailable_and_never_raised(error: BaseException) -> None:
    with patch.object(parse_pool, "submit", new=AsyncMock(side_effect=error)):
        assert isinstance(await count_pdf_pages(b"x", "t.pdf"), Unavailable)


def test_concurrent_counts_on_threads_all_succeed() -> None:
    """With no worker pool the count runs on executor threads, and pdfium fails concurrent opens of one file."""
    from concurrent.futures import ThreadPoolExecutor

    content = text_pdf(1500)
    with ThreadPoolExecutor(16) as pool:
        counts = list(pool.map(lambda _: count_pages(content), range(48)))
    assert counts == [1500] * 48


@pytest.mark.asyncio
async def test_a_lane_admission_timeout_is_unavailable_not_unreadable() -> None:
    """A burst makes lane admission time out. That is the pool, not the file: it must not read as an unreadable file."""
    from app.services.messaging.consumer_concurrency import ParseAdmissionTimeout

    with patch.object(parse_pool, "submit", new=AsyncMock(side_effect=ParseAdmissionTimeout("parsing", 120))):
        assert isinstance(await count_pdf_pages(b"x", "t.pdf"), Unavailable)


@pytest.mark.asyncio
async def test_an_unreadable_file_logs_one_info_line_without_a_traceback(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.DEBUG, logger="app.modules.parsers.pdf.page_count"):
        assert isinstance(await count_pdf_pages(b"not a pdf", "junk.pdf"), Unreadable)
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) == 1 and info[0].exc_info is None
    assert all(r.exc_info is None for r in caplog.records if r.levelno >= logging.INFO)
    assert any(r.levelno == logging.DEBUG and r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_with_no_worker_process_the_count_is_unavailable_and_never_runs_on_a_thread() -> None:
    """A thread cannot be stopped by the count's timeout: a stalled open would hold the pdfium lock for every count."""
    with patch.object(parse_pool, "parse_pool_workers", return_value=0), patch.object(parse_pool, "_pool", return_value=None):
        with patch("app.modules.parsers.pdf.page_count.count_pages") as ran:
            assert isinstance(await count_pdf_pages(text_pdf(2), "t.pdf"), Unavailable)
        ran.assert_not_called()


@pytest.mark.asyncio
async def test_a_pool_that_closes_under_the_job_is_unavailable_not_a_thread_run() -> None:
    class Closed:
        async def submit(self, _job: object) -> None:
            raise parse_pool._PoolClosed

    with patch.object(parse_pool, "parse_pool_workers", return_value=1), patch.object(parse_pool, "_pool", return_value=Closed()):
        assert isinstance(await count_pdf_pages(text_pdf(2), "t.pdf"), Unavailable)


@pytest.mark.asyncio
async def test_submit_without_process_only_still_falls_back_to_a_thread() -> None:
    assert await parse_pool.submit(len, [1, 2, 3]) == 3


@pytest.mark.asyncio
async def test_a_process_with_no_pool_by_configuration_logs_one_info_line_and_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from app.modules.parsers.pdf import page_count

    page_count._LOGGED["no_pool"] = False
    with patch.object(parse_pool, "parse_pool_workers", return_value=0), caplog.at_level(logging.DEBUG, logger=page_count.__name__):
        results = [await count_pdf_pages(text_pdf(2), "t.pdf") for _ in range(3)]
    assert all(isinstance(r, Unavailable) and r.no_pool for r in results)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert caplog.records[0].exc_info is None


@pytest.mark.asyncio
async def test_a_worker_outage_is_still_unavailable_without_the_no_pool_mark() -> None:
    with patch.object(parse_pool, "parse_pool_workers", return_value=1), patch.object(
        parse_pool, "submit", new=AsyncMock(side_effect=parse_pool.ParseWorkerError("overran", "timeout"))
    ):
        result = await count_pdf_pages(b"x", "t.pdf")
    assert isinstance(result, Unavailable) and not result.no_pool
