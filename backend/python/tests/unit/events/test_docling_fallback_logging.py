"""A Docling failure reaches the layer that falls back as an exception, and is logged there once.

Before: the client returned None, so the indexing path wrote a client ERROR, a processor ERROR and an
events WARNING (the record name in each, the cause in none of the handled lines).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.base_client import ServiceAuthRefusedError, ServiceUnavailableError
from app.services.docling.client import (
    DoclingClient,
    DoclingParseError,
    DoclingUnavailableError,
)
from app.services.parsing.interface import ParseError, ParseErrorCode
from app.services.parsing.providers.docling_service_parser import DoclingServiceParser
from app.utils.error_class import mark_error
from tests.support.log_capture import assert_not_logged
from tests.unit.events.test_events import (
    DedupDecision,
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
    _pdf_inspection,
)
from tests.unit.events.test_processor import _make_processor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

NAME = "Quarterly-Layoff-Plan-ACME.pdf"


def _raising(exc: BaseException) -> Callable[..., AsyncIterator[object]]:
    async def gen(*_args: object, **_kwargs: object) -> AsyncIterator[object]:
        raise exc
        yield  # unreachable: makes this an async generator, as process_pdf_with_docling is

    return gen


async def _index_pdf(process_pdf_with_docling) -> tuple[MagicMock, list]:
    ep, _, processor, gp = _make_event_processor()
    ep.logger = logging.getLogger("test.f57.events")
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    processor.process_pdf_with_docling = process_pdf_with_docling
    processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)
    processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)
    with patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false", "PDF_DOCLING_MAX_PAGES": "30"}), \
            patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                         return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
            _pdf_inspection(needs_ocr=False, page_count=1):
        events = await _drain(ep.on_event(_make_event_payload(
            extension="pdf", mime_type="application/pdf", record_name=NAME,
        )))
    return processor, events


def _failure_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def _unavailable() -> DoclingUnavailableError:
    """As BaseServiceClient raises it once its retries ran out on a refused connection."""
    try:
        try:
            raise httpx.ConnectError("All connection attempts failed")
        except httpx.ConnectError as refused:
            raise ServiceUnavailableError(f"DoclingService parse_pdf failed after 3 attempts: {refused}") from refused
    except ServiceUnavailableError as cause:
        try:
            raise DoclingUnavailableError(f"Docling could not parse {NAME}: {cause}") from cause
        except DoclingUnavailableError as exc:
            return exc


@pytest.mark.parametrize(
    ("exc", "error_class", "reason", "status"),
    [
        (DoclingParseError("Docling could not parse the document", status_code=422, reason="parse_timeout"),
         "bad_request", "parse_timeout", 422),
        (_unavailable(), "unavailable", "unavailable", None),
    ],
    ids=["parse-error", "unavailable"],
)
async def test_a_docling_failure_falls_back_with_one_warning_that_says_why(
    exc: Exception, error_class: str, reason: str, status: int | None, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        processor, events = await _index_pdf(MagicMock(side_effect=_raising(exc)))

    processor.process_pdf_with_pdf_plumber.assert_called_once()
    assert len(events) == 3
    [line] = _failure_lines(caplog)
    assert line.levelno == logging.WARNING
    assert line.getMessage() == "index.parse_fallback"
    assert (line.parser, line.error_class, line.reason) == ("docling", error_class, reason)
    assert getattr(line, "status", None) == status
    assert getattr(line, "error_message", None) is None
    assert_not_logged(caplog.records, NAME)


@pytest.mark.parametrize(
    "exc",
    [
        DoclingParseError("Docling refused the request (status 422)", status_code=422),
        DoclingUnavailableError(f"Docling could not parse {NAME}"),
    ],
    ids=["api-skew", "token-refused"],
)
async def test_a_docling_failure_that_is_our_fault_falls_back_at_error(
    exc: Exception, caplog: pytest.LogCaptureFixture,
) -> None:
    if isinstance(exc, DoclingParseError):
        mark_error(exc, "misconfigured")  # as the client stamps a non-2xx answer without Docling's errorCode
    else:
        exc.__cause__ = ServiceAuthRefusedError("refused", status_code=401)

    with caplog.at_level(logging.DEBUG):
        processor, _ = await _index_pdf(MagicMock(side_effect=_raising(exc)))

    processor.process_pdf_with_pdf_plumber.assert_called_once()
    [line] = _failure_lines(caplog)
    assert (line.levelno, line.getMessage(), line.error_class) == (logging.ERROR, "index.parse_fallback", "misconfigured")
    assert_not_logged(caplog.records, NAME)


async def test_the_processor_lets_a_docling_parse_error_reach_the_fallback_without_a_line() -> None:
    proc, logger, _, _ = _make_processor()
    proc.docling_client.parse_pdf_batched = AsyncMock(
        side_effect=DoclingParseError("Docling could not parse the document", status_code=422, reason="parse_failed"),
    )

    with pytest.raises(DoclingParseError):
        await _drain(proc.process_pdf_with_docling(NAME, "rec-1", b"%PDF", "vr-1"))

    logger.error.assert_not_called()
    logger.warning.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "reason", "status", "error_class"),
    [
        ({"json": {"success": False, "error": "Parsing failed", "errorCode": "PARSE_FAILED"}},
         "parse_failed", 422, "bad_request"),
        # Docling never answers 500 for a file: a crash outside the conversion is the service not serving.
        ({"text": "Internal Server Error"}, "unavailable", 500, "upstream_5xx"),
    ],
    ids=["422-parse-failed", "500-crash"],
)
async def test_one_line_per_failed_docling_parse_through_the_real_client(
    answer: dict[str, object], reason: str, status: int, error_class: str, caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole indexing chain: real DoclingClient over HTTP, real Processor, real EventProcessor."""
    proc, _, _, _ = _make_processor()
    proc.logger = logging.getLogger("test.f57.processor")
    proc.docling_client = DoclingClient(service_url="http://docling.test", timeout=5.0, retry_delay=0.0)
    proc.docling_client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(  # type: ignore[method-assign]
        lambda _r: httpx.Response(status, **answer),
    ))

    with caplog.at_level(logging.DEBUG), \
            patch("app.services.docling.client.get_pdf_page_count", return_value=1):
        processor, _ = await _index_pdf(proc.process_pdf_with_docling)

    processor.process_pdf_with_pdf_plumber.assert_called_once()
    [line] = [r for r in _failure_lines(caplog) if r.getMessage() == "index.parse_fallback"]
    assert (line.levelno, line.reason, line.status, line.error_class) == (logging.WARNING, reason, status, error_class)
    # The processor's INFO start line still names the record (a later module PR); the failure line must not.
    assert_not_logged([line], NAME)


async def test_the_parsing_service_provider_keeps_the_docling_cause() -> None:
    cause = DoclingParseError("Docling could not parse the document (PARSE_TIMEOUT, status 422)",
                              status_code=422, reason="parse_timeout")
    client = MagicMock(parse_pdf_batched=AsyncMock(side_effect=cause))

    with pytest.raises(ParseError) as raised:
        await DoclingServiceParser(client).parse(b"%PDF", NAME)

    assert raised.value.code == ParseErrorCode.PARSE_FAILED
    assert raised.value.__cause__ is cause
    assert "PARSE_TIMEOUT" in raised.value.message
    assert NAME not in raised.value.message


async def test_the_parsing_service_provider_passes_an_outage_through() -> None:
    down = DoclingUnavailableError("down")
    client = MagicMock(parse_pdf_batched=AsyncMock(side_effect=down))

    with pytest.raises(DoclingUnavailableError) as raised:
        await DoclingServiceParser(client).parse(b"%PDF", NAME)

    assert raised.value is down


async def test_a_pdf_waiting_for_docling_falls_back_at_info_through_the_real_client(caplog: pytest.LogCaptureFixture) -> None:
    """Docling's port is closed: the client reports the outage once at WARNING; the fallback line of a record that is
    handed back to wait for Docling is INFO, so a waiting PDF does not add a WARNING every minute."""
    proc, _, _, _ = _make_processor()
    proc.logger = logging.getLogger("test.f57.processor")
    proc.docling_client = DoclingClient(service_url="http://docling.test", timeout=5.0, retry_delay=0.0)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    proc.docling_client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(refuse))  # type: ignore[method-assign]

    with caplog.at_level(logging.DEBUG), \
            patch("app.services.docling.client.get_pdf_page_count", return_value=1):
        processor, _ = await _index_pdf(proc.process_pdf_with_docling)

    processor.process_pdf_with_pdf_plumber.assert_called_once()
    [line] = [r for r in caplog.records if r.getMessage() == "index.parse_fallback"]
    assert (line.levelno, line.reason, line.error_class) == (logging.INFO, "unavailable", "unavailable")
    [outage] = _failure_lines(caplog)
    assert outage.name == "app.services.base_client.DoclingService"
    assert_not_logged([line], NAME)
