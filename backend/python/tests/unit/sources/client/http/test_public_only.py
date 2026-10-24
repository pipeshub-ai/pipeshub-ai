"""A public-only HTTP client, for a personal action's address (`app.sources.client.http.public_only`)."""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from app.agents.mcp import url_guard
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.public_only import (
    AddressNotAllowedError,
    PublicOnlyTransport,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_PUBLIC_IP = "93.184.216.34"


def _dns(answers: dict[str, list[str]]) -> "Callable[..., list]":
    """getaddrinfo that answers each lookup of a name with the next of its addresses."""
    calls: dict[str, int] = {}

    def _getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list:
        n = calls.get(host, 0)
        calls[host] = n + 1
        address = answers[host][min(n, len(answers[host]) - 1)]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    _getaddrinfo.calls = calls  # type: ignore[attr-defined]
    return _getaddrinfo


@pytest.fixture(autouse=True)
def _no_env_proxy() -> "Iterator[None]":
    with patch.object(url_guard.urllib.request, "getproxies", return_value={}):
        yield


def _client(seen: list[httpx.Request], responses: "dict[str, httpx.Response] | None" = None) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return (responses or {}).get(request.url.path, httpx.Response(200, json={"ok": True}))

    return httpx.AsyncClient(transport=PublicOnlyTransport(inner=httpx.MockTransport(answer)), follow_redirects=True)


class TestPublicOnlyTransport:
    async def test_it_connects_to_the_address_it_checked(self) -> None:
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({"me.atlassian.net": [_PUBLIC_IP]})):
            response = await _client(seen).get("https://me.atlassian.net/rest/api/3/myself")

        assert response.status_code == 200
        assert seen[0].url.host == _PUBLIC_IP
        assert seen[0].headers["host"] == "me.atlassian.net"
        assert seen[0].extensions["sni_hostname"] == "me.atlassian.net"

    async def test_a_private_address_is_refused(self) -> None:
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({"jira.corp": ["10.0.0.5"]})), pytest.raises(AddressNotAllowedError):
            await _client(seen).get("https://jira.corp/rest/api/3/myself")
        assert seen == []

    async def test_a_name_that_turns_private_later_still_reaches_the_address_checked(self) -> None:
        # DNS rebinding: public for the check, private for the connection. The name is looked
        # up once per client and every request goes to that address.
        seen: list[httpx.Request] = []
        dns = _dns({"evil.example": [_PUBLIC_IP, "127.0.0.1"]})
        with patch("socket.getaddrinfo", dns):
            client = _client(seen)
            await client.get("https://evil.example/a")
            await client.get("https://evil.example/b")

        assert [r.url.host for r in seen] == [_PUBLIC_IP, _PUBLIC_IP]
        assert dns.calls["evil.example"] == 1  # type: ignore[attr-defined]

    async def test_a_redirect_to_a_private_address_is_refused(self) -> None:
        seen: list[httpx.Request] = []
        redirect = httpx.Response(302, headers={"location": "https://inside.corp/admin"})
        dns = _dns({"me.atlassian.net": [_PUBLIC_IP], "inside.corp": ["169.254.169.254"]})
        with patch("socket.getaddrinfo", dns), pytest.raises(AddressNotAllowedError):
            await _client(seen, {"/go": redirect}).get("https://me.atlassian.net/go")
        assert [r.url.host for r in seen] == [_PUBLIC_IP]


class TestHTTPClient:
    async def test_it_goes_through_the_guard_once_asked(self) -> None:
        client = HTTPClient("token")
        client.require_public_addresses()
        httpx_client = await client._ensure_client()

        assert isinstance(httpx_client._transport, PublicOnlyTransport)
        # httpx's own environment proxy would route around the guard.
        assert httpx_client.trust_env is False

    async def test_it_is_unchanged_otherwise(self) -> None:
        httpx_client = await HTTPClient("token")._ensure_client()
        assert not isinstance(httpx_client._transport, PublicOnlyTransport)

    async def test_asking_after_the_first_request_is_refused(self) -> None:
        client = HTTPClient("token")
        await client._ensure_client()
        with pytest.raises(RuntimeError):
            client.require_public_addresses()

    async def test_a_subclass_that_skips_init_is_not_public_only(self) -> None:
        class Bare(HTTPClient):
            def __init__(self) -> None:  # deliberately skips HTTPClient.__init__
                self.client = None
                self.timeout = 5.0
                self.follow_redirects = True
                self.headers = {}
                self.resilience = None

        httpx_client = await Bare()._ensure_client()
        assert not isinstance(httpx_client._transport, PublicOnlyTransport)
