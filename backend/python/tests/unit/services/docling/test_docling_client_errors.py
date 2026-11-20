"""The Docling client raises a typed error for every failure instead of returning None.

Driven through BaseServiceClient's real retry loop: ``_make_client`` hands it an httpx client whose
transport answers as the Docling service would, and counts the requests.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.base_client import ServiceCallError
from app.services.docling.client import (
    DoclingClient,
    DoclingNotServing,
    DoclingParseError,
    DoclingUnavailableError,
)
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
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
        (_answer(501, {"detail": "Not Implemented"}), 501),
    ],
    ids=["400", "422-validation", "404-html", "200-html", "200-null-document", "501"],
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


async def test_a_client_side_request_error_is_ours_not_an_outage() -> None:
    """A wrong URL scheme is our configuration, not Docling down: one request, no status, not retried later."""
    requests: list[httpx.Request] = []

    with pytest.raises(DoclingParseError) as raised:
        await _parse(_client(_raise(httpx.UnsupportedProtocol, "Request URL has an unsupported protocol"), requests))

    assert not isinstance(raised.value, DoclingUnavailableError)
    assert len(requests) == 1
    assert raised.value.status_code is None
    assert str(raised.value) == "Docling request failed"


# Docling answers a file it cannot parse with a 422 and its errorCode, never a 5xx: a status the retries gave up on
# (its own 500 when unwired, Starlette's for a crash outside the handler, a proxy's 5xx or bare 429) and a connection
# dropped mid-answer say it was not serving. The base client retries a status 3 times and a drop not at all.
@pytest.mark.parametrize(
    ("handler", "expected_class", "status", "expected_requests"),
    [
        (_answer(503, {"detail": "down"}), "unavailable", 503, 3),
        (_raise(httpx.ReadTimeout, "read timed out"), "timeout", None, 3),
        (_answer(500, {"detail": "Docling service not available"}), "upstream_5xx", 500, 3),
        (_answer(500, text="Internal Server Error"), "upstream_5xx", 500, 3),
        (_answer(429, {"detail": "busy"}), "rate_limited", 429, 3),
        (_answer(507, text="Insufficient Storage"), "upstream_5xx", 507, 3),
        (_answer(520, text="Web server returned an unknown error"), "upstream_5xx", 520, 3),
        (_raise(httpx.ReadError, "connection reset by peer"), "unavailable", None, 1),
        (_raise(httpx.RemoteProtocolError, "Server disconnected without sending a response."), "unavailable", None, 1),
    ],
    ids=["503", "read-timeout", "500-unwired", "500-crash", "429-no-retry-after", "507", "520", "read-error",
         "dropped-mid-answer"],
)
async def test_a_service_that_is_not_serving_is_unavailable(
    handler: Handler, expected_class: str, status: int | None, expected_requests: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.DEBUG), pytest.raises(DoclingUnavailableError) as raised:
        await _parse(_client(handler, requests))

    assert len(requests) == expected_requests
    assert isinstance(raised.value.__cause__, ServiceCallError)
    [line] = _failure_lines(caplog)  # only the base client's own line: the retries' summary, or the drop
    assert line.name == "app.services.base_client.DoclingService"
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


class _ConversionFails:
    async def parse_pdf_only(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("pdfium could not open the file")


@pytest.mark.parametrize(
    ("service", "expected_type", "status", "reason", "expected_requests"),
    [
        (None, DoclingUnavailableError, 500, "unavailable", 3),
        (_ConversionFails(), DoclingParseError, 422, "parse_failed", 1),
    ],
    ids=["unwired-500", "conversion-fails-422"],
)
async def test_the_docling_services_own_answers_map_to_the_rule(
    service: object | None, expected_type: type[Exception], status: int, reason: str, expected_requests: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real /parse-pdf route behind the real client: a file it cannot parse is its 422, a 500 is the service itself."""
    from app.services.docling import docling_service as service_module

    # Process globals other tests set: no gate, no member budget, no lanes, so the answer cannot become a 429.
    monkeypatch.setattr(service_module, "docling_service", service)
    monkeypatch.setattr(service_module, "_resource_governor", None)
    monkeypatch.setattr(service_module, "reserve_or_adopt", AsyncMock(return_value=None))
    monkeypatch.setattr(service_module, "lanes_active", lambda: False)
    requests: list[httpx.Request] = []
    asgi = httpx.ASGITransport(app=service_module.app)

    class _Counted(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return await asgi.handle_async_request(request)

    client = DoclingClient(service_url="http://docling.test", timeout=5.0, max_retries=3, retry_delay=0.0)
    client._make_client = lambda: httpx.AsyncClient(transport=_Counted())  # type: ignore[method-assign]

    with pytest.raises((DoclingParseError, DoclingUnavailableError)) as raised:
        await _parse(client)

    assert type(raised.value) is expected_type
    assert (raised.value.status_code, raised.value.reason) == (status, reason)
    assert len(requests) == expected_requests


# Docling never received the file: the record waits for it (a hand-back, no attempt counted) instead of failing.
def _assert_waits_for_docling(exc: BaseException) -> None:
    assert isinstance(exc, DoclingNotServing)
    assert isinstance(exc, DoclingUnavailableError), "the PdfPlumber fallback catches the base type"
    assert isinstance(exc, RequeueWithoutAttempt)
    assert exc.delay_s == DoclingNotServing.HANDBACK_DELAY_S == 60.0
    assert exc.counts_toward_backstop and not exc.pauses_consumer
    assert exc.give_up_label == "Docling stayed unavailable"
    assert exc.log_level == logging.INFO
    assert exc.reason == "unavailable"
    assert isinstance(exc.__cause__, ServiceCallError)


@pytest.mark.parametrize(
    "handler",
    [_raise(httpx.ConnectError, "connection refused"), _raise(httpx.ConnectTimeout, "connect timed out")],
    ids=["refused", "connect-timeout"],
)
async def test_a_docling_that_never_received_the_file_is_waited_for(handler: Handler) -> None:
    requests: list[httpx.Request] = []

    with pytest.raises(DoclingUnavailableError) as raised:
        await _parse(_client(handler, requests))

    _assert_waits_for_docling(raised.value)
    assert len(requests) == 3
    assert NAME not in str(raised.value)


async def test_an_open_circuit_is_waited_for_without_a_request() -> None:
    requests: list[httpx.Request] = []
    client = _client(_answer(200, {"success": True, "parse_result": "{}"}), requests)
    client.health_check = AsyncMock(return_value=False)  # type: ignore[method-assign]  # the probe never resolves a host
    for _ in range(client.circuit_breaker.failure_threshold):
        client.circuit_breaker.record_failure()

    with pytest.raises(DoclingUnavailableError) as raised:
        await _parse(client)

    _assert_waits_for_docling(raised.value)
    assert requests == []


async def test_load_shed_with_retry_after_is_waited_for() -> None:
    requests: list[httpx.Request] = []
    client = DoclingClient(
        service_url="http://docling.test", timeout=5.0, max_retries=3, retry_delay=0.0, backpressure_coordinator=MagicMock(),
    )
    client.max_backpressure_attempts = 0  # give up at the first 429 instead of waiting it out
    client._make_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(429, headers={"Retry-After": "5"})),
    )

    with pytest.raises(DoclingUnavailableError) as raised:
        await _parse(client)

    _assert_waits_for_docling(raised.value)


# One attempt of the call reached Docling, so the call counts, whatever the later attempts met: the file may be the cause.
@pytest.mark.parametrize(
    "first",
    [
        lambda r: httpx.Response(503, json={"detail": "down"}),
        _raise(httpx.ReadTimeout, "read timed out"),
    ],
    ids=["503-then-refused", "read-timeout-then-refused"],
)
async def test_a_call_that_reached_docling_once_stays_counted(first: Handler) -> None:
    requests: list[httpx.Request] = []
    refused = _raise(httpx.ConnectError, "connection refused")

    def handler(request: httpx.Request) -> httpx.Response:
        return (first if len(requests) == 1 else refused)(request)

    with pytest.raises(DoclingUnavailableError) as raised:
        await _parse(_client(handler, requests))

    assert type(raised.value) is DoclingUnavailableError
    assert not isinstance(raised.value, RequeueWithoutAttempt)
    assert len(requests) == 3
    assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TRANSIENT


@pytest.mark.parametrize(
    "handler",
    [
        _raise(httpx.ReadError, "connection reset by peer"),
        _raise(httpx.RemoteProtocolError, "Server disconnected without sending a response."),
        _answer(500, {"detail": "Docling service not available"}),
        _answer(503, {"detail": "down"}),
        _raise(httpx.WriteError, "broken pipe while sending the file"),
        _raise(httpx.WriteTimeout, "timed out sending the file"),
        _answer(401, {"detail": "invalid token"}),
    ],
    ids=["dropped-read-error", "dropped-mid-answer", "500", "503", "partial-send", "send-timeout", "auth-refused"],
)
async def test_a_failure_docling_saw_or_refused_by_auth_is_not_waited_for(handler: Handler) -> None:
    with pytest.raises(DoclingUnavailableError) as raised:
        await _parse(_client(handler, []))

    assert type(raised.value) is DoclingUnavailableError


async def test_a_refusal_partway_through_a_batched_parse_is_waited_for() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if len(requests) == 1:
            return httpx.Response(200, json={"success": True, "parse_result": "{}"})
        raise httpx.ConnectError("connection refused", request=request)

    with patch("app.services.docling.client.DoclingDocument"), pytest.raises(DoclingUnavailableError) as raised:
        await _parse(_client(handler, requests), page_count=5, batch_size=2)

    _assert_waits_for_docling(raised.value)
    assert len(requests) == 4  # batch 1 once, batch 2 three times, batch 3 never


def _answers(*answers: httpx.Response) -> tuple[DoclingClient, list[httpx.Request]]:
    """A client whose transport gives these answers in turn (the last one repeats)."""
    requests: list[httpx.Request] = []
    client = DoclingClient(
        service_url="http://docling.test", timeout=5.0, max_retries=3, retry_delay=0.0, backpressure_coordinator=MagicMock(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answers[min(len(requests), len(answers)) - 1]

    client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return client, requests


SHED = httpx.Response(429, headers={"Retry-After": "5"}, json={"detail": "busy"})


async def test_load_shed_after_docling_answered_a_5xx_stays_counted() -> None:
    """The 503 may have been Docling failing on this file; the 429 that follows does not make the call unseen."""
    client, requests = _answers(httpx.Response(503, json={"detail": "down"}), SHED)
    client.max_backpressure_attempts = 0  # give up at the first 429

    with pytest.raises(DoclingUnavailableError) as raised:
        await _parse(client)

    assert type(raised.value) is DoclingUnavailableError
    assert raised.value.__cause__.delivered is True
    assert len(requests) == 2


@pytest.mark.parametrize("first_answered", [True, False], ids=["5xx-then-shed", "shed-from-the-first-answer"])
async def test_load_shed_past_the_budget_keeps_whether_docling_saw_the_file(first_answered: bool) -> None:
    """The budget branch of the retry loop, which raises before waiting out a Retry-After that cannot fit."""
    from app.services.base_client import ServiceBackpressureError

    answers = (httpx.Response(503, json={"detail": "down"}), SHED) if first_answered else (SHED,)
    client, _ = _answers(*answers)

    with pytest.raises(ServiceBackpressureError) as raised:
        await client._request_with_retry(
            "POST", "http://docling.test/parse-pdf", operation="parse_pdf", budget_seconds=1.5, read_timeout=1.0,
        )

    assert raised.value.delivered is first_answered
