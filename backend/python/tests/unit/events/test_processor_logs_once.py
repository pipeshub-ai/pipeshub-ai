"""Processor: a failure it re-raises is not logged here (the consumer's index.end writes it once), and
a failure it swallows (the Docling fallback) is one WARNING with no text. Real loggers: a mock logger
would hide lines written through log_failure/log_event."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.events.processor import Processor
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.messaging.config import IndexingEvent
from tests.unit.events.test_processor import _collect, _make_processor

LOGGER = "test.p6.processor"
NAME = "Quarterly-Secret-Plan.docx"


@pytest.fixture
def proc(caplog: pytest.LogCaptureFixture) -> Processor:
    processor, _, _, _ = _make_processor()
    processor.logger = logging.getLogger(LOGGER)
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return processor


def _texts(caplog) -> str:
    return "\n".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records if r.name == LOGGER)


async def test_a_parser_failure_is_raised_from_its_cause_without_a_line(proc, caplog) -> None:
    cause = RuntimeError(f"cannot open {NAME}")
    proc.docling_processor.parse_document = AsyncMock(side_effect=cause)

    with pytest.raises(DocumentProcessingError) as raised:
        await _collect(proc.process_docx_document(NAME, "rec-1", 1, "upload", "org-1", b"PK", "vr-1"))

    assert raised.value.__cause__ is cause
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno >= logging.WARNING] == []
    assert NAME not in _texts(caplog)


async def test_a_docling_failure_falls_back_with_one_warning_and_no_text(proc, caplog) -> None:
    proc.docling_client.parse_pdf_batched = AsyncMock(side_effect=ConnectionError(f"docling down while parsing {NAME}"))

    events = await _collect(proc.process_pdf_with_docling(NAME, "rec-1", b"%PDF", "vr-1"))

    assert [e.event for e in events] == [IndexingEvent.DOCLING_FAILED]
    (line,) = [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.WARNING]
    assert (line.getMessage(), line.levelno, line.parser, line.error_class) == (
        "index.parse_fallback", logging.WARNING, "docling", "unavailable")
    assert not hasattr(line, "error_message")
    assert NAME not in _texts(caplog)


async def test_docling_returning_nothing_falls_back_with_one_warning(proc, caplog) -> None:
    proc.docling_client.parse_pdf_batched = AsyncMock(return_value=None)

    events = await _collect(proc.process_pdf_with_docling(NAME, "rec-1", b"%PDF", "vr-1"))

    assert [e.event for e in events] == [IndexingEvent.DOCLING_FAILED]
    (line,) = [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.WARNING]
    assert (line.getMessage(), line.reason) == ("index.parse_fallback", "no_document")
