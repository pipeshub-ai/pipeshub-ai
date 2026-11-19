"""The pdfium-only page count taken ahead of any whole-file inspection."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.indexing.extension import Unavailable, Unreadable
from app.modules.parsers import parse_pool
from app.modules.parsers.pdf.page_count import count_pages, count_pdf_pages
from tests.support.pdf_samples import password_protected_pdf, text_pdf


def _no_pages() -> bytes:
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    canvas.Canvas(buffer).save()
    return buffer.getvalue()


def test_the_worker_function_counts_the_pages() -> None:
    assert count_pages(text_pdf(3)) == 3


@pytest.mark.asyncio
async def test_a_real_pool_count_is_an_int() -> None:
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
