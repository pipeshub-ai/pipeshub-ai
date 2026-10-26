"""Image URLs inside documents are untrusted: fetching one must never reach
internal, loopback, link-local or cloud-metadata addresses, directly or via a
redirect, and must not buffer an unbounded body.

DNS is faked and every request goes to an ``httpx.MockTransport``.
"""

import socket
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

import app.modules.parsers.image_parser.image_parser as image_parser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.utils.public_http import PublicFetchLimits, PublicUrlFetcher

_DNS: dict[str, list[str]] = {
    "example.com": ["93.184.215.14"],
    "wiki.internal.example.com": ["10.0.0.7"],
}


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *_: object, **__: object) -> list[tuple]:
        if host not in _DNS:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in _DNS[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[PublicUrlFetcher, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return PublicUrlFetcher(transport=httpx.MockTransport(record)), seen


def _png(body: bytes = b"png-bytes") -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(200, headers={"content-type": "image/png"}, content=body)


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


@pytest.mark.asyncio
async def test_a_redirect_to_the_metadata_service_is_refused() -> None:
    fetcher, seen = _fetcher(
        lambda _req: httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    )

    assert await ImageParser._fetch_single_url(fetcher, "https://example.com/a.png") is None
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_literal_loopback_url_is_never_requested() -> None:
    fetcher, seen = _fetcher(_png())

    assert await ImageParser._fetch_single_url(fetcher, "http://127.0.0.1:8529/x.png") is None
    assert seen == []


@pytest.mark.asyncio
async def test_a_name_resolving_to_a_private_address_is_never_requested() -> None:
    fetcher, seen = _fetcher(_png())

    assert await ImageParser._fetch_single_url(fetcher, "https://wiki.internal.example.com/logo.png") is None
    assert seen == []


@pytest.mark.asyncio
async def test_an_allowed_redirect_is_followed_and_pinned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a.png":
            return httpx.Response(301, headers={"Location": "/cdn/a.png"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png-bytes")

    fetcher, seen = _fetcher(handler)

    result = await ImageParser._fetch_single_url(fetcher, "https://example.com/a.png")

    assert result is not None and result.startswith("data:image/png;base64,")
    assert [r.url.path for r in seen] == ["/a.png", "/cdn/a.png"]
    assert all(r.url.host == "93.184.215.14" for r in seen)


@pytest.mark.asyncio
async def test_a_redirect_loop_stops() -> None:
    fetcher, seen = _fetcher(
        lambda _req: httpx.Response(302, headers={"Location": "https://example.com/again.png"})
    )

    assert await ImageParser._fetch_single_url(fetcher, "https://example.com/a.png") is None
    assert len(seen) == image_parser._IMAGE_FETCH_LIMITS.max_redirects + 1


class TestImageSizeLimit:
    """An image URL in a document can serve any amount; the fetch must not buffer it whole."""

    def test_images_are_capped_at_twenty_mebibytes(self) -> None:
        assert image_parser._IMAGE_FETCH_LIMITS.max_bytes == 20 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_a_declared_oversize_body_is_refused_without_reading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(image_parser, "_IMAGE_FETCH_LIMITS", PublicFetchLimits(max_bytes=16))
        stream = _ChunkStream([b"x" * 10])
        fetcher, _ = _fetcher(
            lambda _req: httpx.Response(
                200, headers={"content-type": "image/png", "content-length": "10000"}, stream=stream
            )
        )

        assert await ImageParser._fetch_single_url(fetcher, "https://example.com/big.png") is None
        assert stream.yielded == 0

    @pytest.mark.asyncio
    async def test_a_body_that_outgrows_the_limit_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(image_parser, "_IMAGE_FETCH_LIMITS", PublicFetchLimits(max_bytes=16))
        # No Content-Length, as a chunked or compressed response may send.
        stream = _ChunkStream([b"x" * 8] * 8)
        fetcher, _ = _fetcher(
            lambda _req: httpx.Response(200, headers={"content-type": "image/png"}, stream=stream)
        )

        assert await ImageParser._fetch_single_url(fetcher, "https://example.com/big.png") is None
        assert stream.yielded == 3  # stopped just past the limit, not at the end

    @pytest.mark.asyncio
    async def test_an_image_within_the_limit_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(image_parser, "_IMAGE_FETCH_LIMITS", PublicFetchLimits(max_bytes=16))
        fetcher, _ = _fetcher(_png(b"png-bytes"))

        result = await ImageParser._fetch_single_url(fetcher, "https://example.com/ok.png")
        assert result is not None and result.startswith("data:image/png;base64,")
