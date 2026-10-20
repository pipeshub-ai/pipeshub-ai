"""fetch_url_with_fallback walks redirects itself and checks every hop's address before it is
requested. The other fetch tests replace the hop functions, so these drive the real walk
with a stand-in curl_cffi session: no network, DNS answered from a table.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.sources.web import address_guard
from app.connectors.sources.web.fetch_strategy import _curl_hop, fetch_url_with_fallback
from app.utils.url_fetcher import FetchError, PublicTarget

PUBLIC_IP = "93.184.215.14"
DNS = {"site.example": PUBLIC_IP, "intra.example": "10.0.0.9", "rebind.example": "127.0.0.1"}
log = logging.getLogger(__name__)


def _getaddrinfo(host: str, port: object = None, *args: object, **kwargs: object) -> list:
    try:
        address = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            # the resolver reads legacy IPv4 spellings (2130706433, 0x7f000001) as addresses
            address = str(ipaddress.ip_address(socket.inet_aton(host)))
        except OSError:
            if host not in DNS:
                raise socket.gaierror(f"no such host {host}") from None
            address = DNS[host]
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port or 0))]


class _Response:
    def __init__(self, url: str, status: int, headers: dict, body: bytes, peer: str) -> None:
        self.url, self.status_code, self.headers, self._body, self.primary_ip = url, status, headers, body, peer

    def iter_content(self, _chunk: int):  # noqa: ANN202
        yield self._body

    def close(self) -> None:
        pass


class _FakeCurl:
    """Stands in for curl_cffi.requests.Session: answers from a table, records every GET."""

    requested: list[str] = []
    answers: dict[str, tuple[int, dict, bytes]] = {}
    peer = PUBLIC_IP

    def __init__(self, **_kwargs: object) -> None:
        self.curl_options: dict = {}

    def get(self, url: str, **_kwargs: object) -> _Response:
        type(self).requested.append(url)
        status, headers, body = type(self).answers.get(url, (200, {"Content-Type": "text/html"}, b"<html>ok</html>"))
        # The fetcher collects the body through curl's write callback, not from the response.
        collect = _kwargs.get("content_callback")
        if collect is not None and body:
            collect(body)
        return _Response(url, status, headers, body, type(self).peer)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.delenv(address_guard.ALLOWED_HOSTS_ENV, raising=False)
    address_guard._allowed_hosts.cache_clear()
    _FakeCurl.requested, _FakeCurl.answers, _FakeCurl.peer = [], {}, PUBLIC_IP
    session = MagicMock()
    session.get.side_effect = AssertionError("aiohttp was used")
    session.head.side_effect = AssertionError("aiohttp HEAD was used")
    with patch("curl_cffi.requests.Session", _FakeCurl), patch(
        "cloudscraper.create_scraper", side_effect=AssertionError("cloudscraper was used")
    ):
        yield session
    address_guard._allowed_hosts.cache_clear()


async def _fetch(session: MagicMock, url: str):  # noqa: ANN202
    return await fetch_url_with_fallback(url, session, log, max_retries_per_strategy=1)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5/",
        "http://127.0.0.1:8088/api/v1/health",
        "http://localhost:8091/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
        "http://[::ffff:10.0.0.5]/",
        "http://2130706433/",
        "http://intra.example/wiki",
        "http://rebind.example/",
        "file:///etc/passwd",
        "ftp://site.example/x",
    ],
)
async def test_a_non_public_start_url_is_never_requested(offline: MagicMock, url: str) -> None:
    result = await _fetch(offline, url)

    assert _FakeCurl.requested == []
    assert result is not None
    assert result.headers.get("X-Fetch-Skip-Reason") == "unsafe_address"
    assert result.status_code == 400
    assert result.success is False


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://intra.example/secret",
        "//127.0.0.1:8088/",
        "http://[fd00:ec2::254]/",
    ],
)
async def test_a_redirect_to_a_non_public_address_is_not_followed(offline: MagicMock, location: str) -> None:
    _FakeCurl.answers = {"https://site.example/start": (302, {"Location": location}, b"")}

    result = await _fetch(offline, "https://site.example/start")

    assert _FakeCurl.requested == ["https://site.example/start"]
    assert result is not None
    assert result.headers.get("X-Fetch-Skip-Reason") == "unsafe_address"
    assert result.content_bytes == b""


async def test_a_public_page_behind_a_public_redirect_is_fetched(offline: MagicMock) -> None:
    _FakeCurl.answers = {"https://site.example/start": (301, {"location": "/landing"}, b"")}

    result = await _fetch(offline, "https://site.example/start")

    assert _FakeCurl.requested == ["https://site.example/start", "https://site.example/landing"]
    assert result is not None
    assert result.status_code == 200
    assert result.final_url == "https://site.example/landing"
    assert result.content_bytes == b"<html>ok</html>"
    assert result.strategy.startswith("curl_cffi")


async def test_an_allowed_internal_host_is_fetched_but_not_its_metadata_redirect(
    offline: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(address_guard.ALLOWED_HOSTS_ENV, "intra.example")
    address_guard._allowed_hosts.cache_clear()
    _FakeCurl.peer = "10.0.0.9"
    _FakeCurl.answers = {"http://intra.example/next": (302, {"Location": "http://169.254.169.254/"}, b"")}

    allowed = await _fetch(offline, "http://intra.example/wiki")
    refused = await _fetch(offline, "http://intra.example/next")

    assert allowed is not None and allowed.status_code == 200
    assert refused is not None and refused.headers.get("X-Fetch-Skip-Reason") == "unsafe_address"
    assert "http://169.254.169.254/" not in _FakeCurl.requested


def test_an_answer_from_another_address_than_the_checked_one_is_dropped() -> None:
    pin = PublicTarget("https", "site.example", 443, (ipaddress.ip_address(PUBLIC_IP),))
    client = MagicMock()
    client.curl_options = {}
    client.get.return_value = _Response("https://site.example/", 200, {}, b"secret", "10.0.0.5")

    with pytest.raises(FetchError):
        _curl_hop(client, contextlib.nullcontext(), "https://site.example/", {}, 5, None, pin)
