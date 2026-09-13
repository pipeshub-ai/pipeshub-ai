"""Image URLs inside documents are untrusted: fetching one must never reach
loopback, link-local or cloud-metadata addresses, directly or via a redirect."""

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.image_parser.image_parser import ImageParser
from app.utils.ssrf_resolver import PolicyResolver
from app.utils.url_fetcher import NO_LOCAL
from tests.unit.modules.parsers.http_fakes import with_body


def _response(status: int, headers: dict[str, str] | None = None, body: bytes = b"png-bytes") -> MagicMock:
    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.release = MagicMock()
    response.raise_for_status = MagicMock()
    with_body(response, body)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.fixture
def log() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_a_redirect_to_the_metadata_service_is_refused(log: MagicMock) -> None:
    session = MagicMock()
    session.get = AsyncMock(
        return_value=_response(302, {"Location": "http://169.254.169.254/latest/meta-data/"})
    )

    assert await ImageParser._get_with_checked_redirects(session, "https://example.com/a.png", log) is None
    assert session.get.await_count == 1


@pytest.mark.asyncio
async def test_a_literal_loopback_url_is_never_requested(log: MagicMock) -> None:
    session = MagicMock()
    session.get = AsyncMock()

    assert await ImageParser._get_with_checked_redirects(session, "http://127.0.0.1:8529/x.png", log) is None
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_allowed_redirect_is_followed_without_auto_redirects(log: MagicMock) -> None:
    final = _response(200, {"content-type": "image/png"})
    session = MagicMock()
    session.get = AsyncMock(side_effect=[_response(301, {"Location": "/cdn/a.png"}), final])

    result = await ImageParser._get_with_checked_redirects(session, "https://example.com/a.png", log)

    assert result is final
    assert session.get.await_args_list[1].args[0] == "https://example.com/cdn/a.png"
    assert all(call.kwargs["allow_redirects"] is False for call in session.get.await_args_list)


@pytest.mark.asyncio
async def test_a_redirect_loop_stops(log: MagicMock) -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=_response(302, {"Location": "https://example.com/again.png"}))

    assert await ImageParser._get_with_checked_redirects(session, "https://example.com/a.png", log) is None
    assert session.get.await_count == 4  # the first request plus three redirects


@pytest.mark.asyncio
async def test_private_network_images_are_allowed(log: MagicMock) -> None:
    # Self-hosted Confluence/Jira serve attachments from RFC1918 addresses.
    ok = _response(200, {"content-type": "image/png"})
    session = MagicMock()
    session.get = AsyncMock(return_value=ok)

    assert await ImageParser._get_with_checked_redirects(session, "http://10.2.3.4/logo.png", log) is ok


def _resolved(*hosts: str) -> list[dict[str, object]]:
    return [
        {"hostname": "h", "host": host, "port": 443, "family": socket.AF_INET, "proto": 0, "flags": 0}
        for host in hosts
    ]


@pytest.mark.asyncio
async def test_resolver_drops_forbidden_addresses() -> None:
    resolver = PolicyResolver(NO_LOCAL)
    with patch.object(resolver._inner, "resolve", AsyncMock(return_value=_resolved("169.254.169.254", "10.0.0.7"))):
        results = await resolver.resolve("wiki.internal", 443)
    assert [r["host"] for r in results] == ["10.0.0.7"]


@pytest.mark.asyncio
async def test_resolver_fails_when_a_name_points_only_at_forbidden_addresses() -> None:
    # DNS rebinding: a public-looking name that resolves to loopback.
    resolver = PolicyResolver(NO_LOCAL)
    with patch.object(resolver._inner, "resolve", AsyncMock(return_value=_resolved("127.0.0.1", "::1"))):
        with pytest.raises(OSError):
            await resolver.resolve("rebind.example.com", 80)


class TestImageSizeLimit:
    """An image URL in a document can serve any amount; the fetch must not buffer it whole."""

    @pytest.mark.asyncio
    async def test_a_declared_oversize_body_is_refused_without_reading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.modules.parsers.image_parser.image_parser as image_parser

        monkeypatch.setattr(image_parser, "_MAX_IMAGE_BYTES", 16)
        response = with_body(_response(200, {"content-type": "image/png"}), b"x" * 10, declared=10_000)
        session = MagicMock()
        session.get = AsyncMock(return_value=response)

        assert await ImageParser._fetch_single_url(session, "https://example.com/big.png") is None
        assert response.content.chunks_read == 0

    @pytest.mark.asyncio
    async def test_a_body_that_outgrows_the_limit_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.modules.parsers.image_parser.image_parser as image_parser

        monkeypatch.setattr(image_parser, "_MAX_IMAGE_BYTES", 16)
        monkeypatch.setattr(image_parser, "_IMAGE_READ_CHUNK_BYTES", 8)
        # No Content-Length, as a chunked or compressed response may send.
        response = with_body(_response(200, {"content-type": "image/png"}), b"x" * 64, declared=None)
        session = MagicMock()
        session.get = AsyncMock(return_value=response)

        assert await ImageParser._fetch_single_url(session, "https://example.com/big.png") is None
        assert response.content.chunks_read == 3  # stopped just past the limit, not at the end

    @pytest.mark.asyncio
    async def test_an_image_within_the_limit_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.modules.parsers.image_parser.image_parser as image_parser

        monkeypatch.setattr(image_parser, "_MAX_IMAGE_BYTES", 16)
        response = with_body(_response(200, {"content-type": "image/png"}), b"png-bytes")
        session = MagicMock()
        session.get = AsyncMock(return_value=response)

        result = await ImageParser._fetch_single_url(session, "https://example.com/ok.png")
        assert result is not None and result.startswith("data:image/png;base64,")
