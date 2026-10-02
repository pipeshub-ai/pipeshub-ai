"""The curl_cffi strategy's single hop, on the real library against a local server.

curl_cffi 0.14's stream mode corrupts the heap when a request fails before its headers arrive,
which aborted the connector service in the nightly integration run. The hop no longer streams.
"""

import asyncio
import ipaddress
import logging
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from curl_cffi.requests import Session

from app.connectors.sources.web import fetch_strategy
from app.connectors.sources.web.fetch_strategy import (
    _curl_hop,
    _hops_curl_cffi,
    _HopWalk,
)
from app.utils.url_fetcher import PublicTarget, _curl_pinned_request

BODY = b"x" * 300_000


@pytest.fixture
def port() -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/page")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            if self.path != "/undeclared":
                self.send_header("Content-Length", str(len(BODY)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(BODY)

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def trickle() -> Iterator[tuple[int, list[str]]]:
    """Declares a body far past any cap, then sends it a kilobyte at a time, slowly enough that
    no request reading it would reach the cap before its timeout."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(302 if self.path == "/moved" else 200)
            self.send_header("Content-Length", "50000000")
            if self.path == "/moved":
                self.send_header("Location", "/page")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"y" * 1000)
                    self.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, hits
    finally:
        server.shutdown()
        server.server_close()


def _loopback(port: int) -> PublicTarget:
    return PublicTarget(scheme="http", host="127.0.0.1", port=port, addresses=(ipaddress.ip_address("127.0.0.1"),))


def _hop(port: int, path: str, max_bytes: int | None = None) -> fetch_strategy._Hop:
    pin = _loopback(port)
    url, options = _curl_pinned_request(f"http://127.0.0.1:{port}{path}", pin)
    session = Session(impersonate="chrome", timeout=5, trust_env=False)
    session.curl_options = options
    try:
        return _curl_hop(session, threading.Lock(), url, {}, 5, max_bytes, pin)
    finally:
        session.close()


def _closed_port() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    number = server.server_port
    server.server_close()
    return number


def test_a_page_within_the_limit_comes_back_whole(port: int) -> None:
    hop = _hop(port, "/page", max_bytes=len(BODY))
    assert (hop.status, hop.too_large, hop.body) == (200, False, BODY)


def test_a_page_past_the_limit_without_a_declared_size_is_too_large_and_its_body_dropped(port: int) -> None:
    hop = _hop(port, "/undeclared", max_bytes=100_000)
    assert (hop.status, hop.too_large, hop.body) == (200, True, b"")


def test_a_page_declaring_a_size_past_the_limit_is_refused_at_its_headers(trickle: tuple[int, list[str]]) -> None:
    port, _ = trickle
    started = time.monotonic()
    hop = _hop(port, "/page", max_bytes=100_000)
    assert (hop.status, hop.too_large, hop.body) == (200, True, b"")
    assert time.monotonic() - started < 2, "the body was read instead of refused at the headers"


def test_a_redirect_declaring_a_large_body_is_still_handed_back(trickle: tuple[int, list[str]]) -> None:
    port, _ = trickle
    hop = _hop(port, "/moved", max_bytes=100_000)
    assert hop.status == 302
    assert fetch_strategy._header(hop.headers, "Location") == "/page"


def test_a_page_within_the_limit_is_not_capped_by_an_earlier_hops_limit(port: int) -> None:
    pin = _loopback(port)
    session = Session(impersonate="chrome", timeout=5, trust_env=False)
    try:
        for max_bytes in (100_000, None):
            url, session.curl_options = _curl_pinned_request(f"http://127.0.0.1:{port}/page", pin)
            hop = _curl_hop(session, threading.Lock(), url, {}, 5, max_bytes, pin)
        assert (hop.status, hop.too_large, hop.body) == (200, False, BODY)
    finally:
        session.close()


async def test_a_walk_skips_a_page_declaring_a_size_past_the_limit_without_trying_another_profile(
    trickle: tuple[int, list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, hits = trickle

    async def resolve(url: str) -> PublicTarget:
        return _loopback(port)

    monkeypatch.setattr(fetch_strategy, "resolve_target", resolve)
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome", "safari", "edge"])
    walk = _HopWalk(url=f"http://127.0.0.1:{port}/page", referer=None, extra_headers=None, allow_hop=None,
                    validators_for=None, max_bytes=100_000)

    result = await _hops_curl_cffi(walk, 5, logging.getLogger("test_curl_hop"))

    assert result is not None
    assert result.headers.get("X-Fetch-Skip-Reason") == "max_size_exceeded"
    assert hits == ["/page"]


def test_a_redirect_is_handed_back_not_followed(port: int) -> None:
    hop = _hop(port, "/moved")
    assert hop.status == 302
    assert fetch_strategy._header(hop.headers, "Location") == "/page"


def test_requests_refused_before_any_headers_fail_cleanly_every_time() -> None:
    # The streamed hop aborted this process within 2000 of these (glibc: double free / heap corruption).
    port = _closed_port()
    failures = 0
    for _ in range(2000):
        try:
            _hop(port, "/")
        except Exception:
            failures += 1
    assert failures == 2000


class _BlockingSession:
    """A curl_cffi Session stand-in whose request waits until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.closed_mid_request = False
        self.get_kwargs: dict = {}
        self.curl_options: dict = {}

    def get(self, url: str, **kwargs: object) -> object:
        self.get_kwargs = kwargs
        self.entered.set()
        self.release.wait(10)
        self.closed_mid_request = self.closed.is_set()
        raise ConnectionError("released")

    def close(self) -> None:
        self.closed.set()


async def test_a_cancelled_walk_closes_its_session_only_after_the_running_request_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import curl_cffi.requests

    session = _BlockingSession()
    monkeypatch.setattr(curl_cffi.requests, "Session", lambda **_: session)
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])

    async def resolve(url: str) -> PublicTarget:
        return PublicTarget(scheme="http", host="site.test", port=80, addresses=(ipaddress.ip_address("93.184.215.14"),))

    monkeypatch.setattr(fetch_strategy, "resolve_target", resolve)
    walk = _HopWalk(url="http://site.test/", referer=None, extra_headers=None, allow_hop=None,
                    validators_for=None, max_bytes=None)

    task = asyncio.create_task(_hops_curl_cffi(walk, 5, logging.getLogger("test_curl_hop")))
    assert await asyncio.to_thread(session.entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not session.closed.is_set()
    session.release.set()
    assert await asyncio.to_thread(session.closed.wait, 5)
    assert session.closed_mid_request is False
    assert "stream" not in session.get_kwargs
