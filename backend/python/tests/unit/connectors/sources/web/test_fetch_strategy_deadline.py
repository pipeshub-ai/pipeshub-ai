"""A request that never finishes gives up at its deadline, and its thread stops with it.

curl_cffi and cloudscraper requests run on threads. Before the deadline, a request that wedged
(as curl_cffi 0.14's streamed hop did) or a site that trickles bytes slower than requests' per-read
timeout held the crawl forever. These run the real libraries against a local server, except where
a wedge is staged with a session that never returns.
"""

import asyncio
import ipaddress
import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.connectors.sources.web import fetch_strategy
from app.connectors.sources.web.fetch_strategy import (
    _hops_cloudscraper,
    _hops_curl_cffi,
    _HopWalk,
)
from app.utils.url_fetcher import PublicTarget

DEADLINE = 1.0
# Long enough that neither library's own timeout ends a trickle before the deadline does.
LIBRARY_TIMEOUT = 20


@dataclass
class Trickle:
    port: int
    requests: int = 0
    # Set when a write fails: the client closed its end of the connection.
    hung_up: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def trickle() -> Iterator[Trickle]:
    """Answers at once, then sends its body a byte at a time, never fast enough to end."""
    state = Trickle(port=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            try:
                for _ in range(600):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                state.hung_up.set()

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def one_fetch_thread(monkeypatch: pytest.MonkeyPatch) -> Iterator[ThreadPoolExecutor]:
    """A deadline of DEADLINE seconds, and a single fetch thread, so a request that kept its
    thread after giving up would leave nothing for the next one."""
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(fetch_strategy, "_hop_deadline", lambda timeout: DEADLINE, raising=False)
    monkeypatch.setattr(fetch_strategy, "_FETCH_THREADS", pool, raising=False)
    try:
        yield pool
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _loopback(port: int) -> PublicTarget:
    return PublicTarget(scheme="http", host="127.0.0.1", port=port, addresses=(ipaddress.ip_address("127.0.0.1"),))


def _walk(url: str) -> _HopWalk:
    return _HopWalk(url=url, referer=None, extra_headers=None, allow_hop=None, validators_for=None, max_bytes=None)


def _resolve_to(monkeypatch: pytest.MonkeyPatch, pin: PublicTarget) -> None:
    async def resolve(url: str) -> PublicTarget:
        return pin

    monkeypatch.setattr(fetch_strategy, "resolve_target", resolve)


async def _thread_is_free(pool: ThreadPoolExecutor) -> bool:
    try:
        await asyncio.wait_for(asyncio.wrap_future(pool.submit(lambda: None)), 3)
    except TimeoutError:
        return False
    return True


class _WedgedSession:
    """A curl_cffi Session stand-in whose request never returns until the test lets it go."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = 0
        self.closed = threading.Event()
        self.closed_mid_request = False
        self.curl_options: dict = {}

    def get(self, url: str, **kwargs: object) -> object:
        self.entered += 1
        self.release.wait(30)
        self.closed_mid_request = self.closed.is_set()
        raise ConnectionError("released")

    def close(self) -> None:
        self.closed.set()


async def test_a_curl_request_that_never_returns_gives_up_at_its_deadline(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import curl_cffi.requests

    session = _WedgedSession()
    monkeypatch.setattr(curl_cffi.requests, "Session", lambda **_: session)
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
    monkeypatch.setattr(fetch_strategy, "_hop_deadline", lambda timeout: DEADLINE, raising=False)
    _resolve_to(monkeypatch, PublicTarget(
        scheme="http", host="site.test", port=80, addresses=(ipaddress.ip_address("93.184.215.14"),),
    ))
    caplog.set_level(logging.WARNING)

    try:
        started = time.monotonic()
        result = await asyncio.wait_for(
            _hops_curl_cffi(_walk("http://site.test/"), 5, logging.getLogger("test_deadline")), 10,
        )
        assert result is None
        assert time.monotonic() - started < 5
        assert "Gave up on http://site.test/ after 1 seconds" in caplog.text
        # The session is closed only once its request ends, never under it.
        assert not session.closed.is_set()
    finally:
        session.release.set()
    assert await asyncio.to_thread(session.closed.wait, 5)
    assert session.closed_mid_request is False


async def test_a_curl_transfer_that_trickles_stops_when_the_walk_gives_up(
    trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
    _resolve_to(monkeypatch, _loopback(trickle.port))
    url = f"http://127.0.0.1:{trickle.port}/page"

    result = await asyncio.wait_for(
        _hops_curl_cffi(_walk(url), LIBRARY_TIMEOUT, logging.getLogger("test_deadline")), 10,
    )

    assert result is None
    assert await asyncio.to_thread(trickle.hung_up.wait, 3), "curl kept reading after the walk gave up"
    assert await _thread_is_free(one_fetch_thread)


async def test_a_cloudscraper_body_that_trickles_stops_when_the_walk_gives_up(
    trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # requests' timeout limits each socket read, so a byte every 50ms never trips it.
    _resolve_to(monkeypatch, _loopback(trickle.port))
    url = f"http://127.0.0.1:{trickle.port}/page"

    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(url), LIBRARY_TIMEOUT, logging.getLogger("test_deadline")), 10,
    )

    assert result is None
    assert trickle.requests == 1
    assert await asyncio.to_thread(trickle.hung_up.wait, 3), "requests kept reading after the walk gave up"
    assert await _thread_is_free(one_fetch_thread)
