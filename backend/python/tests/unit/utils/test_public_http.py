"""Tests for app.utils.public_http — the SSRF-safe async fetch primitive.

DNS is faked and every request goes to an ``httpx.MockTransport``; nothing touches the network.
"""

import ipaddress
import socket
import urllib.request
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.utils import public_http
from app.utils.public_http import (
    PublicFetchError,
    PublicFetchLimits,
    PublicUrlFetcher,
    ResponseTooLargeError,
    TooManyRedirectsError,
    UnsafeUrlError,
    plan_hop,
)
from app.utils.url_fetcher import PublicTarget

_DNS: dict[str, list[str]] = {
    "example.com": ["93.184.215.14"],
    "cdn.example.com": ["93.184.215.15"],
    "internal.example.com": ["10.0.0.5"],
    "mixed.example.com": ["8.8.8.8", "10.0.0.1"],
    "v6.example.com": ["2606:4700::1"],
}
_LIMITS = PublicFetchLimits(max_bytes=1024)


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *_: object, **__: object) -> list[tuple]:
        if host not in _DNS:
            raise socket.gaierror(f"unknown host {host}")
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in _DNS[host]
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture(autouse=True)
def _no_system_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})


def _recording_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(record), seen


def _target(
    host: str = "example.com",
    *addresses: str,
    scheme: str = "https",
    port: int = 443,
) -> PublicTarget:
    ips = tuple(ipaddress.ip_address(a) for a in (addresses or ("93.184.215.14",)))
    return PublicTarget(scheme, host, port, ips)


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


class _UnreadableStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise AssertionError("body must not be read")
        yield b""  # pragma: no cover


class TestPlanHop:
    def test_direct_hop_pins_ip_and_keeps_host_for_tls(self) -> None:
        plan = plan_hop("https://example.com:8443/a?b=1", _target(port=8443), proxies={})
        assert str(plan.request_url) == "https://93.184.215.14:8443/a?b=1"
        assert plan.headers == {"Host": "example.com:8443"}
        assert plan.extensions == {"sni_hostname": "example.com"}
        assert plan.proxy is None

    def test_plain_http_hop_has_no_sni_extension(self) -> None:
        plan = plan_hop("http://example.com/", _target(scheme="http", port=80), proxies={})
        assert plan.request_url.host == "93.184.215.14"
        assert plan.headers == {"Host": "example.com"}
        assert plan.extensions == {}

    def test_ipv6_address_is_pinned_with_brackets(self) -> None:
        plan = plan_hop("https://v6.example.com/x", _target("v6.example.com", "2606:4700::1"), proxies={})
        assert str(plan.request_url) == "https://[2606:4700::1]/x"
        assert plan.headers == {"Host": "v6.example.com"}

    def test_configured_proxy_sends_hostname_url_unpinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
        plan = plan_hop("https://example.com/a", _target(), proxies={"https": "http://proxy.corp:3128"})
        assert plan.proxy == "http://proxy.corp:3128"
        assert str(plan.request_url) == "https://example.com/a"
        assert plan.headers == {}
        assert plan.extensions == {}

    def test_no_proxy_bypass_pins_the_hop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("https_proxy", "http://proxy.corp:3128")
        monkeypatch.setenv("no_proxy", "example.com")
        plan = plan_hop("https://example.com/a", _target(), proxies={"https": "http://proxy.corp:3128"})
        assert plan.proxy is None
        assert plan.request_url.host == "93.184.215.14"

    def test_host_mismatch_between_parsers_is_rejected(self) -> None:
        with pytest.raises(UnsafeUrlError):
            plan_hop("https://other.example/", _target(), proxies={})


class TestPublicUrlFetcher:
    async def test_fetches_public_url_pinned_to_validated_ip(self) -> None:
        transport, seen = _recording_transport(lambda r: httpx.Response(200, content=b"ok"))
        response = await PublicUrlFetcher(transport).get("https://example.com/pack.zip", _LIMITS)

        assert (response.status_code, response.content) == (200, b"ok")
        assert response.url == "https://example.com/pack.zip"
        (request,) = seen
        assert request.url.host == "93.184.215.14"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"

    async def test_ipv6_target_is_requested_with_brackets(self) -> None:
        transport, seen = _recording_transport(lambda r: httpx.Response(200))
        await PublicUrlFetcher(transport).get("https://v6.example.com/x", _LIMITS)
        (request,) = seen
        assert str(request.url) == "https://[2606:4700::1]/x"
        assert request.headers["host"] == "v6.example.com"

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.1/",
            "http://127.0.0.1:8088/health",
            "http://169.254.169.254/latest/meta-data",
            "http://internal.example.com/",
            "http://mixed.example.com/",
            "http://localhost/",
            "ftp://example.com/pack.zip",
        ],
    )
    async def test_unsafe_url_is_rejected_before_any_request(self, url: str) -> None:
        transport, seen = _recording_transport(lambda r: httpx.Response(200))
        with pytest.raises(UnsafeUrlError):
            await PublicUrlFetcher(transport).get(url, _LIMITS)
        assert seen == []

    async def test_redirect_to_metadata_address_is_rejected_at_the_hop(self) -> None:
        transport, seen = _recording_transport(
            lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        )
        with pytest.raises(UnsafeUrlError):
            await PublicUrlFetcher(transport).get("https://example.com/pack.zip", _LIMITS)
        assert len(seen) == 1

    async def test_relative_redirect_is_followed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/final"})
            return httpx.Response(200, content=b"done")

        transport, seen = _recording_transport(handler)
        response = await PublicUrlFetcher(transport).get("https://example.com/start", _LIMITS)

        assert response.content == b"done"
        assert response.url == "https://example.com/final"
        assert [r.url.path for r in seen] == ["/start", "/final"]
        assert all(r.headers["host"] == "example.com" for r in seen)

    async def test_cross_host_redirect_is_pinned_to_the_new_host(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["host"] == "example.com":
                return httpx.Response(301, headers={"location": "https://cdn.example.com/pack.zip"})
            return httpx.Response(200, content=b"zip")

        transport, seen = _recording_transport(handler)
        await PublicUrlFetcher(transport).get("https://example.com/pack.zip", _LIMITS)

        assert seen[1].url.host == "93.184.215.15"
        assert seen[1].headers["host"] == "cdn.example.com"
        assert seen[1].extensions["sni_hostname"] == "cdn.example.com"

    async def test_too_many_redirects_raise(self) -> None:
        transport, seen = _recording_transport(lambda r: httpx.Response(302, headers={"location": "/again"}))
        limits = PublicFetchLimits(max_bytes=1024, max_redirects=2)
        with pytest.raises(TooManyRedirectsError):
            await PublicUrlFetcher(transport).get("https://example.com/", limits)
        assert len(seen) == 3

    async def test_declared_content_length_over_limit_is_rejected_before_reading(self) -> None:
        transport, _ = _recording_transport(
            lambda r: httpx.Response(200, headers={"content-length": "4096"}, stream=_UnreadableStream())
        )
        with pytest.raises(ResponseTooLargeError):
            await PublicUrlFetcher(transport).get("https://example.com/pack.zip", _LIMITS)

    async def test_oversized_stream_without_content_length_is_aborted(self) -> None:
        stream = _ChunkStream([b"x" * 600] * 10)
        transport, _ = _recording_transport(lambda r: httpx.Response(200, stream=stream))
        with pytest.raises(ResponseTooLargeError):
            await PublicUrlFetcher(transport).get("https://example.com/pack.zip", _LIMITS)
        assert stream.yielded == 2

    async def test_transport_error_becomes_public_fetch_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport, _ = _recording_transport(handler)
        with pytest.raises(PublicFetchError) as exc_info:
            await PublicUrlFetcher(transport).get("https://example.com/", _LIMITS)
        assert not isinstance(exc_info.value, UnsafeUrlError)

    async def test_proxied_hop_goes_through_explicit_proxy_unpinned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://proxy.corp:3128"})
        monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
        client_kwargs: dict[str, object] = {}
        real_client = httpx.AsyncClient

        def client_factory(**kwargs: object) -> httpx.AsyncClient:
            client_kwargs.update(kwargs)
            # Keep the request on the mock transport instead of a real proxy connection.
            return real_client(**{**kwargs, "proxy": None})  # type: ignore[arg-type]

        monkeypatch.setattr(public_http.httpx, "AsyncClient", client_factory)
        transport, seen = _recording_transport(lambda r: httpx.Response(200))
        await PublicUrlFetcher(transport).get("https://example.com/pack.zip", _LIMITS)

        assert client_kwargs["proxy"] == "http://proxy.corp:3128"
        assert client_kwargs["trust_env"] is False
        assert client_kwargs["follow_redirects"] is False
        (request,) = seen
        assert request.url.host == "example.com"
        assert "sni_hostname" not in request.extensions
