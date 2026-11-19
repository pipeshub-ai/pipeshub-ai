"""The Docling client raises a typed error for every failure instead of returning None.

Driven through BaseServiceClient's real retry loop: ``_make_client`` hands it an httpx client whose
transport answers as the Docling service would, and counts the requests.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from app.services.base_client import ServiceCallError
from app.services.docling.client import (
    DoclingClient,
    DoclingParseError,
    DoclingUnavailableError,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.utils import error_class
from tests.support.log_capture import assert_not_logged

NAME = "Quarterly-Layoff-Plan-ACME.pdf"
PDF = b"%PDF-1.4 small"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, requests: list[httpx.Request]) -> DoclingClient:
    client = DoclingClient(service_url="http://docling.test", timeout=5.0, max_retries=3, retry_delay=0.0)

    def counted(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(counted))  # type: ignore[method-assign]
    return client


def _answer(status: int, body: object = None, text: str | None = None) -> Handler:
    if text is not None:
        return lambda _r: httpx.Response(status, text=text, headers={"content-type": "text/html"})
    return lambda _r: httpx.Response(status, json=body)


def _raise(exc_type: type[httpx.TransportError], message: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type(message, request=request)

    return handler


def _failure_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


async def _parse(client: DoclingClient, page_count: int = 1, batch_size: int = 10) -> object:
    with patch("app.services.docling.client.get_pdf_page_count", return_value=page_count):
        return await client.parse_pdf_batched(NAME, PDF, batch_size=batch_size)


# Docling's own answer for a PDF it could not parse (docling_service._parse_failure_response), and the
# 200 without a code the service answered before #3270.
@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (422, {"success": False, "error": f"failed on {NAME}", "errorCode": "PARSE_FAILED"}, "parse_failed"),
        (422, {"success": False, "error": "Parsing timed out", "errorCode": "PARSE_TIMEOUT"}, "parse_timeout"),
        (200, {"success": False, "error": f"Parsing failed: {NAME}"}, None),
    ],
    ids=["422-parse-failed", "422-parse-timeout", "200-no-code"],
)
async def test_a_pdf_docling_could_not_parse_is_raised_once_with_its_status_and_code(
    status: int, body: dict, reason: str | None, caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.DEBUG), pytest.raises(DoclingParseError) as raised:
        await _parse(_client(_answer(status, body), requests))

    exc = raised.value
    assert (exc.status_code, exc.reason) == (status, reason)
    assert len(requests) == 1  # an answer about the file is never asked again
    assert NAME not in str(exc)
    assert error_class.classify_error(exc) == "bad_request"
    assert MessageErrorClassifier.classify_by_exception(exc) == MessageErrorType.TERMINAL
    assert not _failure_lines(caplog)  # the caller that handles it logs it


# A non-2xx answer without Docling's errorCode: the two services disagree on the API (version skew, a
# wrong URL behind a proxy, a request the route's validation refused). Our fault, not the file's. So is
# any answer that is not JSON, a 200 included: Docling always answers JSON.
@pytest.mark.parametrize(
    ("handler", "status"),
    [
        (_answer(400, {"detail": f"bad request for {NAME}"}), 400),
        (_answer(422, {"detail": [{"type": "missing", "loc": ["body", "record_name"]}]}), 422),
        (_answer(404, text="<html>Not Found</html>"), 404),
        (_answer(200, text="<html>Sign in to the proxy</html>"), 200),
        (_answer(200, {"success": True, "parse_result": None}), 200),
    ],
    ids=["400", "422-validation", "404-html", "200-html", "200-null-document"],
)
async def test_an_answer_that_is_not_doclings_own_is_raised_as_misconfigured(
    handler: Handler, status: int, caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.DEBUG), pytest.raises(DoclingParseError) as raised:
        await _parse(_client(handler, requests))

    assert len(requests) == 1
    assert raised.value.status_code == status
    assert raised.value.reason is None
    assert NAME not in str(raised.value)
    assert error_class.classify_error(raised.value) == "misconfigured"
    assert not _failure_lines(caplog)


async def test_a_500_is_retried_then_raised_from_the_service_error(caplog: pytest.LogCaptureFixture) -> None:
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.DEBUG), pytest.raises(DoclingParseError) as raised:
        await _parse(_client(_answer(500, {"detail": "Docling service not available"}), requests))

    assert len(requests) == 3
    assert raised.value.status_code == 500
    assert isinstance(raised.value.__cause__, ServiceCallError)
    assert error_class.classify_error(raised.value) == "upstream_5xx"
    [summary] = _failure_lines(caplog)  # only the base client's summary of the retries
    assert summary.name == "app.services.base_client.DoclingService"
    assert_not_logged(caplog.records, NAME)


async def test_a_bare_429_is_retried_then_raised_as_rate_limited_not_as_an_outage() -> None:
    """A 429 without Retry-After is a transient status, not backpressure: 3 requests, then the record falls back."""
    requests: list[httpx.Request] = []

    with pytest.raises(DoclingParseError) as raised:
        await _parse(_client(_answer(429, {"detail": "busy"}), requests))

    assert len(requests) == 3
    assert raised.value.status_code == 429
    assert error_class.classify_error(raised.value) == "rate_limited"


async def test_a_connection_lost_mid_answer_is_classified_by_its_cause() -> None:
    """The base client does not retry a RemoteProtocolError: one request, no status, not the file's fault."""
    requests: list[httpx.Request] = []

    with pytest.raises(DoclingParseError) as raised:
        await _parse(_client(_raise(httpx.RemoteProtocolError, "Server disconnected without sending a response."), requests))

    assert len(requests) == 1
    assert raised.value.status_code is None
    assert str(raised.value) == "Docling request failed"
    assert error_class.classify_error(raised.value) == "unavailable"


# Characterisation: main already raised DoclingUnavailableError after 3 requests; `reason` and the
# name-free cause text are new.
@pytest.mark.parametrize(
    ("handler", "expected_class", "status"),
    [
        (_answer(503, {"detail": "down"}), "unavailable", 503),
        (_raise(httpx.ReadTimeout, "read timed out"), "timeout", None),
    ],
    ids=["503", "read-timeout"],
)
async def test_a_service_that_is_not_serving_stays_unavailable(
    handler: Handler, expected_class: str, status: int | None, caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.DEBUG), pytest.raises(DoclingUnavailableError) as raised:
        await _parse(_client(handler, requests))

    assert len(requests) == 3
    assert raised.value.reason == "unavailable"
    assert raised.value.status_code == status
    assert error_class.classify_error(raised.value) == expected_class
    assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TRANSIENT
    assert_not_logged(caplog.records, NAME)
    assert NAME not in str(raised.value)


@pytest.mark.parametrize(
    ("content", "reason"),
    [("not bytes", "invalid_input"), (b"%PDF-1.4 four", "too_large")],
)
async def test_input_docling_cannot_take_is_raised_without_a_request(
    content: object, reason: str, caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []
    client = _client(_answer(200, {"success": True, "parse_result": "{}"}), requests)

    with caplog.at_level(logging.DEBUG), patch("app.services.docling.client.MAX_PDF_BYTES", 5), \
            pytest.raises(DoclingParseError) as raised:
        await client.parse_pdf_batched(NAME, content)  # type: ignore[arg-type]

    assert raised.value.reason == reason
    assert error_class.classify_error(raised.value) == "bad_request"
    assert requests == []
    assert not _failure_lines(caplog)


async def test_a_pdf_whose_pages_cannot_be_counted_is_raised_from_the_reader_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []
    unreadable = ValueError("Failed to load document (PDFium: Data format error).")
    client = _client(_answer(200, {"success": True, "parse_result": "{}"}), requests)

    with caplog.at_level(logging.DEBUG), \
            patch("app.services.docling.client.get_pdf_page_count", side_effect=unreadable), \
            pytest.raises(DoclingParseError) as raised:
        await client.parse_pdf_batched(NAME, PDF)

    assert raised.value.reason == "unreadable"
    assert raised.value.__cause__ is unreadable
    assert error_class.classify_error(raised.value) == "bad_request"
    assert requests == []
    assert not _failure_lines(caplog)


async def test_a_failing_batch_stops_the_parse_before_the_next_batch() -> None:
    requests: list[httpx.Request] = []
    answers = iter([
        httpx.Response(200, json={"success": True, "parse_result": "{}"}),
        httpx.Response(422, json={"success": False, "error": "Parsing failed", "errorCode": "PARSE_FAILED"}),
    ])

    with patch("app.services.docling.client.DoclingDocument"), pytest.raises(DoclingParseError):
        await _parse(_client(lambda _r: next(answers), requests), page_count=5, batch_size=2)

    assert len(requests) == 2  # pages 5-5 are never sent
