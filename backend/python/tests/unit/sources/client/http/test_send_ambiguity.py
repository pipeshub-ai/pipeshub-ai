"""AM-1..AM-4: the shared connector clients report sends whose outcome they could not learn."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
import httpx
import pytest

from app.sources.client.graphql.client import is_mutation
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.http.send_ambiguity import (
    classify_aiohttp_failure,
    classify_httpx_failure,
    classify_status,
)
from app.sources.client.resilience import ResiliencePolicy
from harness_core.effects.ambiguity import track_sends

if TYPE_CHECKING:
    from collections.abc import Callable

_REQ = httpx.Request("POST", "https://api.example.test/issues")


@pytest.mark.parametrize(
    ("method", "error", "expected"),
    [
        ("POST", httpx.ConnectError("refused", request=_REQ), "not_sent"),
        ("POST", httpx.ConnectTimeout("slow connect", request=_REQ), "not_sent"),
        ("POST", httpx.PoolTimeout("pool", request=_REQ), "not_sent"),
        ("POST", httpx.ReadTimeout("no answer", request=_REQ), "maybe_sent"),
        ("PUT", httpx.WriteTimeout("half sent", request=_REQ), "maybe_sent"),
        ("PATCH", httpx.ReadError("reset", request=_REQ), "maybe_sent"),
        ("DELETE", httpx.WriteError("broken pipe", request=_REQ), "maybe_sent"),
        ("POST", httpx.RemoteProtocolError("closed without response", request=_REQ), "maybe_sent"),
        ("GET", httpx.ReadTimeout("no answer", request=_REQ), "definite"),
        ("HEAD", httpx.RemoteProtocolError("closed", request=_REQ), "definite"),
        ("post", httpx.ReadTimeout("lower-case method", request=_REQ), "maybe_sent"),
        ("POST", httpx.DecodingError("bad body", request=_REQ), "definite"),
    ],
)
def test_am1_httpx_failures(method: str, error: BaseException, expected: str) -> None:
    assert classify_httpx_failure(method, error) == expected


@pytest.mark.parametrize(
    ("method", "status", "expected"),
    [
        ("POST", 502, "maybe_sent"),
        ("POST", 504, "maybe_sent"),
        ("POST", 500, "maybe_sent"),
        ("POST", 503, "maybe_sent"),
        ("POST", 429, "not_sent"),
        ("POST", 400, "definite"),
        ("POST", 201, "definite"),
        ("GET", 504, "definite"),
    ],
)
def test_am1_statuses(method: str, status: int, expected: str) -> None:
    assert classify_status(method, status) == expected


@pytest.mark.parametrize(
    ("unsafe", "error", "expected"),
    [
        (True, aiohttp.ServerDisconnectedError(), "maybe_sent"),
        (True, TimeoutError(), "maybe_sent"),
        (True, aiohttp.ClientPayloadError("cut"), "maybe_sent"),
        (False, aiohttp.ServerDisconnectedError(), "definite"),
        (True, aiohttp.InvalidURL("x"), "not_sent"),
    ],
)
def test_am1_aiohttp_failures(unsafe: bool, error: BaseException, expected: str) -> None:
    assert classify_aiohttp_failure(error, unsafe=unsafe) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("mutation { create { id } }", True),
        ("  # comment\n mutation Create($a: Int) { x }", True),
        ("query { viewer { id } }", False),
        ("{ viewer { id } }", False),
    ],
)
def test_am1_graphql_mutations_are_the_unsafe_operations(query: str, expected: bool) -> None:
    assert is_mutation(query) is expected


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HTTPClient:
    client = HTTPClient("token")
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _raising(error: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("simulated", request=request)

    return handler


@pytest.mark.parametrize(
    ("method", "error", "marked"),
    [
        ("POST", httpx.ReadTimeout, True),
        ("GET", httpx.ReadTimeout, False),
        ("POST", httpx.ConnectError, False),
    ],
)
async def test_am2_http_client_marks_the_tracked_call(
    method: str, error: type[httpx.TransportError], marked: bool
) -> None:
    client = _client(_raising(error))
    with track_sends() as sends, pytest.raises(error):
        await client.execute(HTTPRequest(method=method, url="https://api.example.test/x"))
    assert sends.ambiguous is marked


async def test_am2_a_gateway_timeout_status_marks_but_still_returns_the_response() -> None:
    client = _client(lambda request: httpx.Response(504))
    with track_sends() as sends:
        response = await client.execute(HTTPRequest(method="POST", url="https://api.example.test/x"))
    assert response.status == 504
    assert sends.reasons == ["http_504"]


async def test_am4_outside_a_tracked_call_nothing_changes() -> None:
    client = _client(_raising(httpx.ReadTimeout))
    with pytest.raises(httpx.ReadTimeout):
        await client.execute(HTTPRequest(method="POST", url="https://api.example.test/x"))


def _policy(retries: int) -> ResiliencePolicy:
    return ResiliencePolicy(max_retries=retries, base_delay=0.0, max_delay=0.0, name="test")


def _scripted_send(
    monkeypatch: pytest.MonkeyPatch, script: list[BaseException | httpx.Response]
) -> list[str]:
    sent: list[str] = []

    async def send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    return sent


async def test_sr_9_a_write_whose_read_timed_out_is_sent_once_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _scripted_send(monkeypatch, [httpx.ReadTimeout("no answer", request=_REQ), httpx.Response(201)])
    async with httpx.AsyncClient(transport=ResilientHTTPTransport(_policy(2))) as client:
        with track_sends() as sends, pytest.raises(httpx.ReadTimeout):
            await client.post("https://api.example.test/issues")
    assert sent == ["POST"]
    assert sends.reasons == ["ReadTimeout"]


@pytest.mark.parametrize(
    ("method", "first", "sent", "reasons"),
    [
        ("POST", httpx.Response(502), ["POST"], ["http_502"]),
        ("POST", httpx.Response(503), ["POST"], ["http_503"]),
        ("POST", httpx.Response(429), ["POST", "POST"], []),
        ("POST", httpx.ConnectError("refused", request=_REQ), ["POST", "POST"], []),
        ("GET", httpx.ReadTimeout("no answer", request=_REQ), ["GET", "GET"], []),
        ("GET", httpx.Response(502), ["GET", "GET"], []),
        ("PROPFIND", httpx.Response(503), ["PROPFIND", "PROPFIND"], []),
    ],
)
async def test_sr_9_reads_and_refused_writes_are_retried_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    first: BaseException | httpx.Response,
    sent: list[str],
    reasons: list[str],
) -> None:
    attempts = _scripted_send(monkeypatch, [first, httpx.Response(201)])
    async with httpx.AsyncClient(transport=ResilientHTTPTransport(_policy(2))) as client:
        with track_sends() as sends:
            _ = await client.request(method, "https://api.example.test/issues")
    assert attempts == sent
    assert sends.reasons == reasons
