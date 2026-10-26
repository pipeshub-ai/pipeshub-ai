"""Reading PDF bytes nobody has vetted, without letting one file stall the service.

pdfminer is pure Python, and a damaged file does not make it give up. Sent to
the wrong offsets by a broken cross-reference table, it tokenises the binary
content one byte at a time; with no ``startxref`` near the end, it scans back
through the file re-copying everything read so far, which grows with the
square of the file size. A thread doing either holds the GIL: one 200 MB file
kept the loops sharing the indexing process stalled for over an hour, until
their Redis calls timed out, their leases lapsed and every record in flight
was cancelled.

So pdfminer first reads a file in a parse worker process, under a deadline,
and answers two questions separately:

- Can it be opened cheaply? Every later pdfplumber read pays the open again,
  some of them on the event loop. A file that overruns, fails to open, opens
  slowly or shows no pages is rewritten by pdfium (C: seconds where pdfminer
  took hours) and opened again; one neither can read is unreadable.
- Does it need OCR? Decided from a sample of pages, and only for a copy that
  opens cleanly: OCR opens the file again on the event loop. Any other file,
  and pages too costly to analyse in time, go to the layout parser.

Callers parse the bytes returned here, never the ones they passed in.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    PasswordProtectedError,
)
from app.modules.parsers import parse_pool
from app.modules.parsers.parse_pool import ParseAbandonedError, ParseWorkerError
from app.modules.parsers.pdf.pdf_inspection import (
    PdfStructure,
    detect_ocr,
    open_pdf,
    repair_pdf,
)
from app.utils.env_config import env_float, env_int

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

_logger = logging.getLogger(__name__)

INSPECTION_TIMEOUT_ENV = "PDF_INSPECTION_TIMEOUT_SECONDS"
DEFAULT_INSPECTION_TIMEOUT_SECONDS = 120.0
SAMPLE_PAGES_ENV = "PDF_OCR_DETECTION_SAMPLE_PAGES"
DEFAULT_SAMPLE_PAGES = 30
# A sound file opens in well under a second even at 200 MB.
SLOW_OPEN_SECONDS = 2.0


class UnreadablePdfError(DocumentProcessingError):
    """Neither the file as sent nor pdfium's rewrite of it could be opened in time."""


@dataclass(frozen=True)
class InspectedPdf:
    content: bytes
    needs_ocr: bool
    repaired: bool
    # None when only pdfium can open the file.
    page_count: int | None = None


@dataclass(frozen=True)
class _Copy:
    content: bytes
    # None when only pdfium can open it.
    structure: PdfStructure | None
    repaired: bool


def inspection_timeout_seconds() -> float:
    return env_float(
        INSPECTION_TIMEOUT_ENV, DEFAULT_INSPECTION_TIMEOUT_SECONDS, low=1.0, high=3600.0
    )


def ocr_sample_pages() -> int:
    return max(1, env_int(SAMPLE_PAGES_ENV, DEFAULT_SAMPLE_PAGES))


async def inspect_untrusted_pdf(content: bytes, *, label: str = "") -> InspectedPdf:
    """The bytes to parse, whether they need OCR, and the page count.

    Raises ``PasswordProtectedError`` when the file needs a password, and
    ``UnreadablePdfError`` when it cannot be opened either way. A
    worker that died (most likely out of memory) or a cancelled caller is
    raised as it is, so those are retried rather than read as a bad file.
    """
    copy = await _readable_copy(content, label)
    page_count = copy.structure.page_count if copy.structure else None
    return InspectedPdf(copy.content, await _needs_ocr(copy, label), copy.repaired, page_count)


async def _readable_copy(content: bytes, label: str) -> _Copy:
    original = await _try(open_pdf, content, label=label)
    if isinstance(original, PdfStructure) and _sound(original):
        return _Copy(content, original, repaired=False)
    _logger.warning(
        "PDF %s (%d bytes) %s; rewriting it with pdfium", label, len(content), _why(original)
    )

    try:
        rewritten_content = await _in_worker(repair_pdf, content, label=label)
    except Exception as exc:
        if not _about_the_file(exc):
            raise
        if isinstance(original, PdfStructure):
            return _Copy(content, original, repaired=False)
        raise UnreadablePdfError(f"pdfium could not open the PDF either: {exc}") from exc

    rewritten = await _try(open_pdf, rewritten_content, label=label)
    if isinstance(rewritten, PdfStructure):
        if _sound(rewritten):
            _logger.info("PDF %s opens cleanly after the pdfium rewrite", label)
        else:
            _logger.warning("PDF %s still %s after the pdfium rewrite", label, _why(rewritten))
        return _Copy(rewritten_content, rewritten, repaired=True)
    if isinstance(original, PdfStructure):
        return _Copy(content, original, repaired=False)
    if _timed_out(rewritten):
        raise UnreadablePdfError(f"the rewritten PDF also overran: {rewritten}") from rewritten
    # pdfium reads the file and pdfminer does not; Docling reads PDFs with pdfium.
    _logger.warning("pdfminer cannot open PDF %s even rewritten (%s)", label, rewritten)
    return _Copy(rewritten_content, None, repaired=True)


async def _needs_ocr(copy: _Copy, label: str) -> bool:
    # VLM OCR opens the file again with pdfplumber on the event loop, so a
    # copy that opens slowly is left to the layout parser, which reads it in
    # a parse worker.
    if copy.structure is None or not _sound(copy.structure):
        return False
    decision = await _try(detect_ocr, copy.content, ocr_sample_pages(), label=label)
    if isinstance(decision, bool):
        return decision
    _logger.warning(
        "Could not decide whether PDF %s needs OCR: it %s; using the layout parser",
        label, _why(decision),
    )
    return False


async def _try(fn: Callable[..., T], content: bytes, *args: object, label: str) -> T | Exception:
    """*fn*'s answer, or the exception that says the file itself is the problem."""
    try:
        return await _in_worker(fn, content, *args, label=label)
    except Exception as exc:
        if not _about_the_file(exc):
            raise
        return exc


async def _in_worker(fn: Callable[..., T], content: bytes, *args: object, label: str) -> T:
    return await parse_pool.submit(
        fn, content, *args, label=label, size=len(content), timeout=inspection_timeout_seconds()
    )


def _sound(structure: PdfStructure) -> bool:
    # pdfminer reads a file whose cross-reference table points at the wrong
    # offsets as having no pages, where pdfium finds them.
    return structure.page_count > 0 and structure.open_seconds < SLOW_OPEN_SECONDS


def _about_the_file(exc: Exception) -> bool:
    """False for failures a rewrite cannot help: a dead worker, a stop, a broken install."""
    if isinstance(exc, ParseWorkerError):
        return _timed_out(exc)
    # A password is a fact about the file, but no rewrite or second reader can help.
    return not isinstance(
        exc, (ParseAbandonedError, MemoryError, ImportError, PasswordProtectedError)
    )


def _timed_out(exc: Exception) -> bool:
    return isinstance(exc, ParseWorkerError) and exc.cause == "timed_out"


def _why(outcome: PdfStructure | Exception) -> str:
    if isinstance(outcome, PdfStructure):
        if outcome.page_count == 0:
            return "shows no pages"
        return f"took {outcome.open_seconds:.1f}s to open"
    if _timed_out(outcome):
        return f"was not read within {inspection_timeout_seconds():.0f}s"
    return f"could not be read ({type(outcome).__name__}: {outcome})"
