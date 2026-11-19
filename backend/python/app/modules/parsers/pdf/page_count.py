"""The page count of a PDF from a pdfium-only open, taken before any whole-file inspection.

The count runs in a parse worker and imports only light modules: opening a very large file with pdfminer is the step
that runs out of memory, pdfium reads the page tree only.
"""

from __future__ import annotations

import logging
import threading

from app.modules.indexing.extension import PageCount, Unavailable, Unreadable

_logger = logging.getLogger(__name__)

COUNT_TIMEOUT_SECONDS = 120.0

# pdfium is not thread-safe. A worker process runs one job at a time, but with no pool the count runs on the event
# loop's executor threads, where concurrent opens of the same file fail with "Data format error".
_PDFIUM_OPEN = threading.Lock()


def count_pages(content: bytes) -> int:
    """Pages of *content* as pdfium sees them; 0 when pdfium opens it but finds no pages.

    Raises ``PasswordProtectedError`` when pdfium needs a password and ``pypdfium2.PdfiumError`` for any other refusal.
    """
    from app.modules.parsers.encryption import open_pdfium_document

    with _PDFIUM_OPEN:
        document = open_pdfium_document(content)
        try:
            return len(document)
        finally:
            document.close()


async def count_pdf_pages(content: bytes, label: str = "") -> PageCount:
    """Count the pages of *content*: an ``int``, ``Unreadable`` or ``Unavailable``.

    It never raises for an infrastructure failure (a timeout, a dead worker, a closed pool): that is ``Unavailable``.
    A file pdfium cannot open, or opens with no pages, is ``Unreadable``. ``PasswordProtectedError`` is raised.
    """
    from app.exceptions.indexing_exceptions import PasswordProtectedError
    from app.modules.parsers import parse_pool
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
    from app.services.resource_governor.kinds import ParseKind

    try:
        pages = await parse_pool.submit(
            count_pages, content, label=label, size=len(content), kind=ParseKind.INSPECT, timeout=COUNT_TIMEOUT_SECONDS
        )
    except PasswordProtectedError:
        raise
    except (parse_pool.ParseWorkerError, parse_pool.ParseAbandonedError, RequeueWithoutAttempt, MemoryError, ImportError):
        _logger.warning("the page count of %s is unavailable", label, exc_info=True)
        return Unavailable()
    except Exception:
        _logger.info("pdfium could not count the pages of %s", label, exc_info=True)
        return Unreadable()
    return pages if pages >= 1 else Unreadable()
