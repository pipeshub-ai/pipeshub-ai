"""The page count of a PDF from a pdfium-only open, taken before any whole-file inspection.

The count runs in a parse worker and imports only light modules: opening a very large file with pdfminer is the step
that runs out of memory, pdfium reads the page tree only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

_logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class Unreadable:
    """pdfium says it cannot open the file."""


@dataclass(frozen=True)
class Unavailable:
    """The count could not be taken now: a timeout, a dead worker, a closed pool or no worker process."""


PageCount = int | Unreadable | Unavailable

COUNT_TIMEOUT_SECONDS = 120.0

# pdfium is not thread-safe: concurrent opens of the same file in one process fail with "Data format error". A worker
# process runs one job at a time; the lock covers a caller that runs ``count_pages`` in its own process (a test).
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

    It never raises for an infrastructure failure (a timeout, a dead worker, a closed pool, no worker process): that is
    ``Unavailable``. The count runs only in a worker process, where ``COUNT_TIMEOUT_SECONDS`` can stop it: a thread cannot be
    stopped, so a stalled open there would hold the pdfium lock and every count behind it.
    A file pdfium cannot open, or opens with no pages, is ``Unreadable``. ``PasswordProtectedError`` is raised.
    """
    from app.exceptions.indexing_exceptions import PasswordProtectedError
    from app.modules.parsers import parse_pool
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
    from app.services.resource_governor.kinds import ParseKind

    try:
        pages = await parse_pool.submit(
            count_pages, content, label=label, size=len(content), kind=ParseKind.INSPECT, timeout=COUNT_TIMEOUT_SECONDS,
            process_only=True,
        )
    except PasswordProtectedError:
        raise
    except (parse_pool.ParseWorkerError, parse_pool.ParseAbandonedError, RequeueWithoutAttempt, MemoryError, ImportError):
        _logger.warning("the page count of %s is unavailable", label, exc_info=True)
        return Unavailable()
    except Exception:
        _logger.info("pdfium could not count the pages of %s", label)
        _logger.debug("pdfium count failure of %s", label, exc_info=True)
        return Unreadable()
    return pages if pages >= 1 else Unreadable()
