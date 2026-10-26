"""Reading untrusted PDF bytes: what is rewritten, what fails and how, and that the loop keeps running.

Three layers: the policy against scripted worker outcomes, real files read in
threads (no pool), and real files through real parse worker processes.
"""
from __future__ import annotations

import asyncio
import sys
import time
from contextlib import contextmanager
from statistics import median
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.parse_pool import ParseAbandonedError, ParseWorkerError
from app.modules.parsers.pdf import untrusted_pdf
from app.modules.parsers.pdf.pdf_inspection import (
    PdfStructure,
    detect_ocr,
    open_pdf,
    repair_pdf,
)
from app.modules.parsers.pdf.untrusted_pdf import (
    INSPECTION_TIMEOUT_ENV,
    SAMPLE_PAGES_ENV,
    InspectedPdf,
    UnreadablePdfError,
    inspect_untrusted_pdf,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.utils.user_errors import (
    PARSE_WORKER_OUT_OF_MEMORY,
    PASSWORD_PROTECTED,
    PROCESSING_TIMED_OUT,
    UNREADABLE_FILE,
    to_user_reason,
)
from tests.support.loop_probes import IoProbe
from tests.support.pdf_samples import (
    MIB,
    NOT_PDFS,
    blank_pdf,
    broken_xref_pdf,
    garbage_tail_pdf,
    owner_password_pdf,
    password_protected_pdf,
    text_pdf,
    truncated_pdf,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ORIGINAL = b"%PDF-original"
REWRITTEN = b"%PDF-rewritten"
SOUND = PdfStructure(page_count=3, open_seconds=0.1)
SLOW = PdfStructure(page_count=3, open_seconds=untrusted_pdf.SLOW_OPEN_SECONDS + 1)
NO_PAGES = PdfStructure(page_count=0, open_seconds=0.1)


def _timed_out() -> ParseWorkerError:
    return ParseWorkerError(PROCESSING_TIMED_OUT, "timed_out")


def _crashed() -> ParseWorkerError:
    return ParseWorkerError(PARSE_WORKER_OUT_OF_MEMORY, "crashed")


class _Worker:
    """Stands in for ``parse_pool.submit``, answering each job from a script keyed by job and input."""

    def __init__(self, script: dict[tuple, object]) -> None:
        self._script = script
        self.jobs: list[tuple[str, bytes, tuple, dict]] = []

    async def submit(self, fn: Callable[..., object], content: bytes, *args: object, **kwargs: object) -> object:
        assert fn in (open_pdf, detect_ocr, repair_pdf)
        self.jobs.append((fn.__name__, content, args, kwargs))
        outcome = self._script[(fn.__name__, content)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def calls(self) -> list[tuple[str, bytes]]:
        return [(name, content) for name, content, _, _ in self.jobs]


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Worker]:
    def install(**script: object) -> _Worker:
        """``open=``, ``repair=``, ``reopen=``, ``detect=`` (on the copy that is kept)."""
        jobs = {("open_pdf", ORIGINAL): script["open"], ("repair_pdf", ORIGINAL): script.get("repair", REWRITTEN)}
        if "reopen" in script:
            jobs[("open_pdf", REWRITTEN)] = script["reopen"]
        for content in (ORIGINAL, REWRITTEN):
            jobs[("detect_ocr", content)] = script.get("detect", True)
        fake = _Worker(jobs)
        monkeypatch.setattr(parse_pool, "submit", fake.submit)
        return fake

    return install


class TestStructure:
    async def test_a_sound_file_is_opened_once_and_passed_on_unchanged(self, worker) -> None:
        jobs = worker(open=SOUND, detect=True)

        result = await inspect_untrusted_pdf(ORIGINAL, label="a.pdf")

        assert result == InspectedPdf(ORIGINAL, needs_ocr=True, repaired=False)
        assert jobs.calls == [("open_pdf", ORIGINAL), ("detect_ocr", ORIGINAL)]

    @pytest.mark.parametrize(
        "first",
        [SLOW, NO_PAGES, _timed_out(), ValueError("No /Root object!")],
        ids=["opens slowly", "shows no pages", "overran", "fails to open"],
    )
    async def test_a_suspect_file_is_rewritten_and_the_rewrite_is_used(self, worker, first) -> None:
        jobs = worker(open=first, reopen=SOUND, detect=False)

        result = await inspect_untrusted_pdf(ORIGINAL)

        assert result == InspectedPdf(REWRITTEN, needs_ocr=False, repaired=True)
        assert jobs.calls == [
            ("open_pdf", ORIGINAL),
            ("repair_pdf", ORIGINAL),
            ("open_pdf", REWRITTEN),
            ("detect_ocr", REWRITTEN),
        ]

    @pytest.mark.parametrize("first", [_timed_out(), ValueError("Unexpected EOF")], ids=["overran", "fails"])
    async def test_a_file_pdfium_cannot_open_either_is_unreadable(self, worker, first) -> None:
        worker(open=first, repair=RuntimeError("Failed to load document (PDFium: Format error)"))

        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(ORIGINAL)

        assert to_user_reason(raised.value) == UNREADABLE_FILE
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    async def test_a_password_keeps_its_own_reason(self, worker) -> None:
        # pdfminer raises an empty error; pdfium names the password.
        worker(open=ValueError(""), repair=RuntimeError("Failed to load document (PDFium: Incorrect password error)."))

        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(ORIGINAL)

        assert to_user_reason(raised.value) == PASSWORD_PROTECTED

    async def test_a_rewrite_that_also_overruns_reads_as_too_slow(self, worker) -> None:
        worker(open=_timed_out(), reopen=_timed_out())

        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(ORIGINAL)

        assert to_user_reason(raised.value) == PROCESSING_TIMED_OUT
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    async def test_a_rewrite_only_pdfium_opens_goes_to_the_layout_parser_undecided(self, worker) -> None:
        """Docling reads PDFs with pdfium, so a file pdfminer refuses may still parse."""
        jobs = worker(open=ValueError("bad"), reopen=ValueError("still bad"))

        result = await inspect_untrusted_pdf(ORIGINAL)

        assert result == InspectedPdf(REWRITTEN, needs_ocr=False, repaired=True)
        assert ("detect_ocr", REWRITTEN) not in jobs.calls

    @pytest.mark.parametrize(
        "rewrite",
        [{"repair": RuntimeError("pdfium refused")}, {"reopen": ValueError("bad")}, {"reopen": _timed_out()}],
        ids=["repair fails", "rewrite will not open", "rewrite overruns"],
    )
    async def test_a_slow_file_that_cannot_be_improved_is_read_as_sent(self, worker, rewrite) -> None:
        worker(open=SLOW, detect=False, **rewrite)

        assert await inspect_untrusted_pdf(ORIGINAL) == InspectedPdf(ORIGINAL, False, repaired=False)

    @pytest.mark.parametrize(
        "rewrite",
        [{"repair": RuntimeError("pdfium refused")}, {"reopen": ValueError("bad")}, {"reopen": SLOW}],
        ids=["repair fails", "rewrite will not open", "rewrite also slow"],
    )
    async def test_a_copy_that_opens_slowly_is_never_sent_to_ocr(self, worker, rewrite) -> None:
        """OCR opens the file again on the event loop; the layout parser reads it in a worker."""
        jobs = worker(open=SLOW, detect=True, **rewrite)

        result = await inspect_untrusted_pdf(ORIGINAL)

        assert result.needs_ocr is False
        assert [name for name, _ in jobs.calls if name == "detect_ocr"] == []

    async def test_a_rewritten_file_with_no_pages_is_passed_on_as_empty(self, worker) -> None:
        """pdfium agrees there is nothing in it, so it is empty rather than damaged."""
        jobs = worker(open=NO_PAGES, reopen=NO_PAGES)

        assert await inspect_untrusted_pdf(ORIGINAL) == InspectedPdf(REWRITTEN, False, repaired=True)
        assert ("detect_ocr", REWRITTEN) not in jobs.calls

    @pytest.mark.parametrize(
        "failure",
        [_crashed(), ParseAbandonedError("parse abandoned"), MemoryError(), ImportError("No module named 'pdfplumber'")],
        ids=["worker died", "abandoned", "out of memory", "broken install"],
    )
    async def test_failures_a_rewrite_cannot_help_are_raised_as_they_are(self, worker, failure) -> None:
        jobs = worker(open=failure)

        with pytest.raises(type(failure)):
            await inspect_untrusted_pdf(ORIGINAL)

        assert jobs.calls == [("open_pdf", ORIGINAL)]

    async def test_a_worker_that_dies_during_the_repair_is_raised_as_it_is(self, worker) -> None:
        worker(open=_timed_out(), repair=_crashed())

        with pytest.raises(ParseWorkerError) as raised:
            await inspect_untrusted_pdf(ORIGINAL)

        assert raised.value.cause == "crashed"

    async def test_a_cancelled_caller_is_not_swallowed(self, worker) -> None:
        worker(open=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await inspect_untrusted_pdf(ORIGINAL)


class TestOcrDecision:
    @pytest.mark.parametrize("decision", [True, False])
    async def test_the_sampled_decision_is_used(self, worker, decision: bool) -> None:
        worker(open=SOUND, detect=decision)

        assert (await inspect_untrusted_pdf(ORIGINAL)).needs_ocr is decision

    @pytest.mark.parametrize("failure", [_timed_out(), ValueError("bad content stream")], ids=["overran", "fails"])
    async def test_pages_too_costly_to_analyse_go_to_the_layout_parser(self, worker, failure) -> None:
        """A sound file is never failed for its content: it is parsed without OCR, as before."""
        worker(open=SOUND, detect=failure)

        assert await inspect_untrusted_pdf(ORIGINAL) == InspectedPdf(ORIGINAL, False, repaired=False)

    async def test_a_worker_that_dies_deciding_is_raised_as_it_is(self, worker) -> None:
        worker(open=SOUND, detect=_crashed())

        with pytest.raises(ParseWorkerError):
            await inspect_untrusted_pdf(ORIGINAL)


class TestSettings:
    async def test_every_job_carries_the_deadline_and_the_sample_size(
        self, worker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(INSPECTION_TIMEOUT_ENV, "45")
        monkeypatch.setenv(SAMPLE_PAGES_ENV, "7")
        jobs = worker(open=_timed_out(), reopen=SOUND)

        await inspect_untrusted_pdf(ORIGINAL, label="report.pdf")

        for _name, content, _args, kwargs in jobs.jobs:
            assert kwargs == {"label": "report.pdf", "size": len(content), "timeout": 45.0}
        assert [args for name, _, args, _ in jobs.jobs if name == "detect_ocr"] == [(7,)]

    @pytest.mark.parametrize(("raw", "expected"), [("0.01", 1.0), ("99999", 3600.0), ("soon", 120.0)])
    def test_the_deadline_setting_is_bounded(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
    ) -> None:
        monkeypatch.setenv(INSPECTION_TIMEOUT_ENV, raw)

        assert untrusted_pdf.inspection_timeout_seconds() == expected

    @pytest.mark.parametrize(("raw", "expected"), [("0", 1), ("-3", 1), ("12", 12), ("many", 30)])
    def test_the_sample_setting_is_at_least_one_page(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
    ) -> None:
        monkeypatch.setenv(SAMPLE_PAGES_ENV, raw)

        assert untrusted_pdf.ocr_sample_pages() == expected


# Without a pool (the query service, other platforms) the same jobs run in threads.
class TestRealFilesInThreads:
    async def test_a_text_pdf_is_passed_on_unchanged(self) -> None:
        content = text_pdf(pages=2)

        assert await inspect_untrusted_pdf(content) == InspectedPdf(content, False, repaired=False)

    async def test_a_scanned_looking_pdf_goes_to_ocr(self) -> None:
        assert (await inspect_untrusted_pdf(blank_pdf())).needs_ocr is True

    async def test_an_owner_password_does_not_stop_indexing(self) -> None:
        content = owner_password_pdf()

        assert await inspect_untrusted_pdf(content) == InspectedPdf(content, False, repaired=False)

    async def test_a_password_protected_pdf_fails_with_the_password_reason(self) -> None:
        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(password_protected_pdf())

        assert to_user_reason(raised.value) == PASSWORD_PROTECTED
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    @pytest.mark.parametrize(
        "content", [*NOT_PDFS.values(), truncated_pdf()], ids=[*NOT_PDFS, "truncated"]
    )
    async def test_a_file_that_is_not_a_readable_pdf_fails_as_unreadable(self, content: bytes) -> None:
        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(content)

        assert to_user_reason(raised.value) == UNREADABLE_FILE
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    async def test_a_broken_cross_reference_table_is_rewritten_into_a_readable_file(self) -> None:
        result = await inspect_untrusted_pdf(broken_xref_pdf(16 * 1024))

        assert result.repaired is True
        assert open_pdf(result.content).page_count == 1


# Long enough for several whole probe bursts to fall inside the stall. One
# file read once is not: a fast runner finished it before any burst did.
PDFMINER_BUSY_SECONDS = 1.0


@contextmanager
def _untraced() -> Iterator[None]:
    """Pause this thread's tracer, such as coverage's, and resume it after.

    A tracer runs on every line in the pdfminer thread and in the probe alike,
    which evens out the convoy: under CI's coverage run the probe saw 0.09 to
    0.21 s, where the same read untraced gives it 0.5 to 1.2 s.
    """
    tracer = sys.gettrace()
    sys.settrace(None)
    try:
        yield
    finally:
        sys.settrace(tracer)


# How long a thread may hold the GIL before it must offer it back, while the
# control runs. Each I/O call the probe makes waits up to this to get the GIL
# back, so a longer interval makes the stall the control looks for larger.
GIL_SWITCH_SECONDS = 0.05

# The control compares the stall with the same loop left idle, not with a fixed
# time: the stall's size depends on the runner. Pinned to two cores the worst
# burst was 0.12 to 0.24 s, under the old 0.25 s bar, and with another process
# busy on those cores 0.06 to 0.11 s, while the idle loop stayed near 0.01 s.
STALL_OVER_IDLE = 3
MIN_STALL_SECONDS = 0.05
# The idle baseline is the median of a few windows, so one scheduling pause in
# a single window cannot raise the bar the stall has to clear.
IDLE_WINDOWS = 3


@contextmanager
def _slow_gil_handoff(seconds: float) -> Iterator[None]:
    """Set the interpreter's thread switch interval to *seconds*, and restore it after."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(seconds)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _keep_pdfminer_busy(seconds: float) -> None:
    """Read a damaged file with pdfminer, again and again, for at least *seconds*."""
    content = broken_xref_pdf(256 * 1024)
    deadline = time.monotonic() + seconds
    with _untraced():
        while time.monotonic() < deadline:
            open_pdf(content)


def _governor() -> MagicMock:
    governor = MagicMock()
    governor.ceilings = SimpleNamespace(heavy=4)
    return governor


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """One real parse worker process and a short inspection deadline.

    Long enough for a fresh worker to import the PDF libraries, which it does
    inside its first job's deadline; each damaged file here takes pdfminer
    well over ten seconds.
    """
    monkeypatch.setenv(parse_pool.WORKERS_ENV, "1")
    monkeypatch.setenv(INSPECTION_TIMEOUT_ENV, "3")
    parse_pool.set_resource_governor(_governor())
    try:
        yield
    finally:
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)


class TestRealFilesInWorkers:
    @pytest.mark.parametrize(
        "damaged",
        [broken_xref_pdf(4 * MIB), garbage_tail_pdf(32 * MIB)],
        ids=["broken cross-reference table", "no startxref near the end"],
    )
    async def test_a_file_that_would_stall_pdfminer_is_handled_without_stalling_the_loop(
        self, pool: None, damaged: bytes
    ) -> None:
        """The outage: read in a thread, each of these held the GIL for tens of seconds."""
        await parse_pool.submit(len, b"warm up the worker")

        with IoProbe() as io_probe:
            await asyncio.sleep(0.1)
            started = time.monotonic()
            result = await inspect_untrusted_pdf(damaged, label="damaged.pdf")
            elapsed = time.monotonic() - started

        assert result.repaired is True
        assert open_pdf(result.content).page_count == 1
        assert elapsed < 20
        assert 0 < io_probe.worst < 0.25

    async def test_the_probe_sees_the_stall_a_pdfminer_thread_causes(self) -> None:
        """Without this, a probe that measured nothing would pass the test above."""
        with _untraced(), _slow_gil_handoff(GIL_SWITCH_SECONDS):
            idle_worsts = []
            for _ in range(IDLE_WINDOWS):
                with IoProbe() as idle_probe:
                    await asyncio.sleep(PDFMINER_BUSY_SECONDS / IDLE_WINDOWS)
                idle_worsts.append(idle_probe.worst)
            with IoProbe() as io_probe:
                await asyncio.sleep(0.1)
                await asyncio.to_thread(_keep_pdfminer_busy, PDFMINER_BUSY_SECONDS)
                await asyncio.sleep(0.1)

        assert io_probe.worst > max(STALL_OVER_IDLE * median(idle_worsts), MIN_STALL_SECONDS)

    async def test_the_pool_keeps_working_after_a_worker_was_stopped(self, pool: None) -> None:
        await inspect_untrusted_pdf(garbage_tail_pdf(32 * MIB))

        content = text_pdf()
        assert await inspect_untrusted_pdf(content) == InspectedPdf(content, False, repaired=False)

    async def test_a_password_protected_pdf_fails_the_same_way_in_a_worker(self, pool: None) -> None:
        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(password_protected_pdf())

        assert to_user_reason(raised.value) == PASSWORD_PROTECTED

    @pytest.mark.parametrize("content", NOT_PDFS.values(), ids=NOT_PDFS.keys())
    async def test_a_file_that_is_not_a_pdf_fails_the_same_way_in_a_worker(
        self, pool: None, content: bytes
    ) -> None:
        with pytest.raises(UnreadablePdfError) as raised:
            await inspect_untrusted_pdf(content)

        assert to_user_reason(raised.value) == UNREADABLE_FILE
