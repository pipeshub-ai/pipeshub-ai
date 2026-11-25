"""``http.client.request.completed``: the shared HTTP client wrappers announce each outbound request with a bounded
name of who was called, and never a URL, a header or a body."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import httpx
import pytest

from app.platform_events import catalog
from app.services.base_client import BaseServiceClient
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.utils import http_client_events
from tests.support.platform_event_capture import captured_events, facts

SECRET = "SEEDED-path-and-token"
FIELDS = set(catalog.EVENTS["http.client.request.completed"].fields)


def _request(method: str = "GET") -> HTTPRequest:
    return HTTPRequest(method=method, url=f"https://tenant.example.invalid/{SECRET}", headers={"X-Key": SECRET})


def _http_client(cls: type[HTTPClient], answer: object) -> HTTPClient:
    client = cls(token=SECRET)
    client.client = MagicMock(request=AsyncMock(side_effect=answer if isinstance(answer, BaseException) else None,
                                                return_value=answer))
    return client


class TestStatusClass:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [(200, "2xx"), (204, "2xx"), (302, "3xx"), (404, "4xx"), (429, "4xx"), (503, "5xx"),
         (None, "error"), (0, "error"), (700, "error"), ("200", "error"), (True, "error")],
    )
    def test_a_status_is_its_class_and_anything_else_an_error(self, status: object, expected: str) -> None:
        assert http_client_events.status_class(status) == expected


class TestHTTPClient:
    async def test_a_request_is_announced_without_its_url_or_headers(self) -> None:
        client = _http_client(HTTPClient, MagicMock(status_code=201))
        with captured_events("http.client.request.completed") as events:
            await client.execute(_request("post"))

        (request,) = facts(events, "http.client.request.completed")
        assert set(request) == FIELDS
        assert (request["upstream"], request["method"], request["status_class"], request["library"]) == (
            "HTTPClient", "POST", "2xx", "httpx")
        assert 0 <= request["duration_seconds"] < 5
        assert SECRET not in repr(request) and "example.invalid" not in repr(request)

    async def test_a_request_that_got_no_answer_is_announced_and_still_raised(self) -> None:
        client = _http_client(HTTPClient, httpx.ConnectError("refused"))
        with captured_events("http.client.request.completed") as events, pytest.raises(httpx.ConnectError):
            await client.execute(_request())

        assert [r["status_class"] for r in facts(events, "http.client.request.completed")] == ["error"]

    async def test_a_source_client_is_named_after_its_package_and_an_odd_method_is_other(self) -> None:
        from app.sources.client.jira.jira import JiraRESTClientViaToken

        client = JiraRESTClientViaToken.__new__(JiraRESTClientViaToken)
        HTTPClient.__init__(client, token=SECRET)
        client.client = MagicMock(request=AsyncMock(return_value=MagicMock(status_code=404)))
        with captured_events("http.client.request.completed") as events:
            await client.execute(_request("PROPFIND " + SECRET))

        (request,) = facts(events, "http.client.request.completed")
        assert (request["upstream"], request["method"], request["status_class"]) == ("jira", "OTHER", "4xx")

    async def test_nothing_is_built_when_nobody_listens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(http_client_events, "status_class", lambda _s: pytest.fail("built a payload nobody hears"))

        await _http_client(HTTPClient, MagicMock(status_code=200)).execute(_request())


class TestServiceClient:
    async def test_each_attempt_is_announced_under_the_service_name(self) -> None:
        client = BaseServiceClient("http://parsing.internal", service_name="ParsingService", max_retries=3, retry_delay=0.0)
        answers = iter([httpx.Response(503), httpx.ConnectError("down"), httpx.Response(200, json={"ok": True})])

        async def request(_method: str, _url: str, **_kwargs: object) -> httpx.Response:
            answer = next(answers)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        sent = AsyncMock()
        sent.__aenter__ = AsyncMock(return_value=sent)
        sent.__aexit__ = AsyncMock(return_value=False)
        sent.request = request
        with patch.object(client, "_make_client", return_value=sent), captured_events("http.client.request.completed") as events:
            response = await client._post_json(f"/{SECRET}", {"text": SECRET})

        assert response.status_code == 200
        requests = facts(events, "http.client.request.completed")
        assert [(r["upstream"], r["method"], r["status_class"]) for r in requests] == [
            ("ParsingService", "POST", "5xx"), ("ParsingService", "POST", "error"), ("ParsingService", "POST", "2xx"),
        ]
        assert all(set(r) == FIELDS for r in requests) and SECRET not in repr(requests)


def _aiohttp_session(status: int | None, verb: str) -> MagicMock:
    """An aiohttp session whose one request is answered with ``status``, or fails to connect when it is None."""
    response = AsyncMock(status=status, headers={"Content-Type": "application/json"})
    response.json = AsyncMock(return_value={"data": {}})
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    setattr(session, verb, MagicMock(return_value=response, side_effect=None if status else aiohttp.ClientConnectionError("down")))
    return session


class TestAiohttpWrappers:
    async def test_a_graphql_call_is_announced_under_its_source(self) -> None:
        from app.sources.client.linear.linear import LinearGraphQLClientViaToken

        client = LinearGraphQLClientViaToken(SECRET)
        with captured_events("http.client.request.completed") as events:
            with patch("app.sources.client.graphql.client.aiohttp.ClientSession", return_value=_aiohttp_session(200, "post")):
                await client.execute(f"query {{ {SECRET} }}")
            with patch("app.sources.client.graphql.client.aiohttp.ClientSession", return_value=_aiohttp_session(None, "post")):
                assert (await client.execute("query { viewer }")).success is False

        requests = facts(events, "http.client.request.completed")
        assert [(r["upstream"], r["method"], r["status_class"], r["library"]) for r in requests] == [
            ("linear", "POST", "2xx", "aiohttp"), ("linear", "POST", "error", "aiohttp"),
        ]
        assert all(set(r) == FIELDS for r in requests) and SECRET not in repr(requests)

    async def test_a_call_to_a_service_of_this_product_is_announced_with_the_class_of_its_answer(self) -> None:
        from app.utils.api_call import ApiCallError, make_api_call

        once = make_api_call.retry_with(stop=lambda _state: True)
        with captured_events("http.client.request.completed") as events:
            with patch("app.utils.api_call.aiohttp.ClientSession", return_value=_aiohttp_session(200, "get")):
                await once(f"http://node.internal/{SECRET}", SECRET)
            with patch("app.utils.api_call.aiohttp.ClientSession", return_value=_aiohttp_session(404, "get")), pytest.raises(ApiCallError):
                await once(f"http://node.internal/{SECRET}", SECRET)

        requests = facts(events, "http.client.request.completed")
        assert [(r["upstream"], r["method"], r["status_class"], r["library"]) for r in requests] == [
            ("internal_api", "GET", "2xx", "aiohttp"), ("internal_api", "GET", "4xx", "aiohttp"),
        ]
        assert SECRET not in repr(requests) and "node.internal" not in repr(requests)
