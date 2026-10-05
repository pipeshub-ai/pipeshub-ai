"""A request that never finishes is given up on, and its thread stops with it; one that is slow
but still arriving is not.

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
# Long enough that curl's own timeout doesn't end a trickle before the deadline does.
LIBRARY_TIMEOUT = 20
# requests' timeout is per socket read: a byte every 50ms never trips it.
SCRAPER_TIMEOUT = 0.5
# The slowest body cloudscraper keeps reading, scaled down with the timeout.
MIN_RATE = 200_000
STEADY_CHUNK = 50_000
STEADY_CHUNKS = 50  # every 50ms: 1 MB a second, for 2.5 seconds


@dataclass
class Trickle:
    port: int
    requests: int = 0
    agents: list[str] = field(default_factory=list)
    # Set when a write fails: the client closed its end of the connection.
    hung_up: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def trickle() -> Iterator[Trickle]:
    """Sends a byte every 50ms, never fast enough to end: the body of a 200 at ``/page``, the
    body of a Cloudflare 503 at ``/cloudflare``, the headers at ``/headers``. ``/steady`` sends
    a body above MIN_RATE that takes longer than DEADLINE; ``/plain`` is an ordinary page."""
    state = Trickle(port=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests += 1
            state.agents.append(self.headers.get("User-Agent", ""))
            try:
                if self.path == "/plain":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY)
                    return
                if self.path == "/headers":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    self._trickle()
                    return
                self.send_response(503 if self.path == "/cloudflare" else 200)
                self.send_header("Content-Type", "text/html")
                length = STEADY_CHUNK * STEADY_CHUNKS if self.path == "/steady" else 1_000_000
                self.send_header("Content-Length", str(length))
                self.end_headers()
                if self.path == "/steady":
                    for _ in range(STEADY_CHUNKS):
                        self.wfile.write(b"s" * STEADY_CHUNK)
                        time.sleep(0.05)
                else:
                    self._trickle()
            except OSError:
                state.hung_up.set()

        def version_string(self) -> str:
            # The Server header cloudscraper checks before reading a body for a challenge.
            return "cloudflare" if self.path == "/cloudflare" else super().version_string()

        def _trickle(self) -> None:
            for _ in range(600):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.05)

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
    monkeypatch.setattr(fetch_strategy, "_MIN_BODY_RATE", MIN_RATE, raising=False)
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


@pytest.mark.parametrize("path", [
    "/page",
    # cloudscraper reads this body itself, inside its request, to look for a challenge.
    "/cloudflare",
    "/headers",
])
async def test_a_cloudscraper_request_that_trickles_stops_when_the_walk_gives_up(
    path: str, trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, _loopback(trickle.port))
    url = f"http://127.0.0.1:{trickle.port}{path}"

    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(url), SCRAPER_TIMEOUT, logging.getLogger("test_deadline")), 10,
    )

    assert result is None
    assert trickle.requests == 1
    assert await asyncio.to_thread(trickle.hung_up.wait, 3), "requests kept reading after the walk gave up"
    assert await _thread_is_free(one_fetch_thread)


async def test_a_cloudscraper_body_that_keeps_arriving_is_read_to_the_end(
    trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, _loopback(trickle.port))
    url = f"http://127.0.0.1:{trickle.port}/steady"

    started = time.monotonic()
    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(url), SCRAPER_TIMEOUT, logging.getLogger("test_deadline")), 10,
    )

    assert time.monotonic() - started > 2 * DEADLINE
    assert result is not None
    assert (result.status_code, len(result.content_bytes)) == (200, STEADY_CHUNK * STEADY_CHUNKS)
    assert await _thread_is_free(one_fetch_thread)


class _WedgedScraper(_WedgedSession):
    """A cloudscraper scraper stand-in whose request never returns until the test lets it go."""

    def __init__(self) -> None:
        from requests.adapters import HTTPAdapter

        super().__init__()
        self.adapters = {"https://": HTTPAdapter()}

    def mount(self, prefix: str, adapter: object) -> None:
        self.adapters[prefix] = adapter


async def test_a_scraper_given_up_on_is_closed_only_once_its_request_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import SimpleNamespace

    scraper = _WedgedScraper()
    monkeypatch.setitem(sys.modules, "cloudscraper", SimpleNamespace(create_scraper=lambda **_: scraper))
    monkeypatch.setattr(fetch_strategy, "_hop_deadline", lambda timeout: DEADLINE, raising=False)
    _resolve_to(monkeypatch, PublicTarget(
        scheme="http", host="site.test", port=80, addresses=(ipaddress.ip_address("93.184.215.14"),),
    ))

    try:
        result = await asyncio.wait_for(
            _hops_cloudscraper(_walk("http://site.test/"), 5, logging.getLogger("test_deadline")), 10,
        )
        assert result is None
        assert not scraper.closed.is_set()
    finally:
        scraper.release.set()
    assert await asyncio.to_thread(scraper.closed.wait, 5)
    assert scraper.closed_mid_request is False


# -- HTTPS ------------------------------------------------------------------


def _https_pin(port: int) -> PublicTarget:
    return PublicTarget(scheme="https", host="site.test", port=port, addresses=(ipaddress.ip_address("127.0.0.1"),))


@pytest.fixture
def slow_handshake() -> Iterator[Trickle]:
    """Takes the ClientHello, then answers with a TLS record a byte every 50ms, never finishing it."""
    import socket

    state = Trickle(port=0)
    listener = socket.create_server(("127.0.0.1", 0))
    state.port = listener.getsockname()[1]

    def serve(conn: socket.socket) -> None:
        with conn:
            try:
                conn.recv(65536)
                conn.sendall(b"\x16\x03\x03\x40\x00")  # a handshake record of 16 KB
                for _ in range(600):
                    conn.sendall(b"\x02")
                    time.sleep(0.05)
            except OSError:
                state.hung_up.set()

    def accept() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            state.requests += 1
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield state
    finally:
        listener.close()


async def test_a_cloudscraper_tls_handshake_that_trickles_ends_at_requests_timeout(
    slow_handshake: Trickle, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CPython gives the whole handshake the socket's timeout (urllib3's connect timeout) as one
    # deadline, so a byte every 50ms doesn't keep it going. It ends before the hop deadline would.
    monkeypatch.setattr(fetch_strategy, "_hop_deadline", lambda timeout: 30.0, raising=False)
    _resolve_to(monkeypatch, _https_pin(slow_handshake.port))
    url = f"https://site.test:{slow_handshake.port}/page"

    started = time.monotonic()
    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(url), 1, logging.getLogger("test_deadline")), 10,
    )

    assert result is None
    # Held for the whole timeout, not refused at once: the handshake really waited on the trickle.
    assert 0.9 < time.monotonic() - started < 3
    assert slow_handshake.requests == 1
    assert slow_handshake.hung_up.wait(3)


def test_curl_ends_a_tls_handshake_that_trickles_at_its_own_timeout(slow_handshake: Trickle) -> None:
    # CURLOPT_TIMEOUT covers the whole transfer, the handshake included.
    from curl_cffi.requests import Session

    from app.utils.url_fetcher import _curl_pinned_request

    pin = _https_pin(slow_handshake.port)
    url, options = _curl_pinned_request(f"https://site.test:{slow_handshake.port}/", pin)
    session = Session(impersonate="chrome", timeout=1, trust_env=False)
    session.curl_options = options
    started = time.monotonic()
    try:
        with pytest.raises(Exception, match="(?i)timed? ?out"):
            fetch_strategy._curl_hop(session, threading.Lock(), url, {}, 1, None, pin)
    finally:
        session.close()
    assert time.monotonic() - started < 5
    assert slow_handshake.hung_up.wait(3)


@dataclass
class Certificates:
    ca: str
    site: tuple[str, str]  # certificate and key for site.test
    other: tuple[str, str]  # for other.test, from the same authority


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Certificates:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder = tmp_path_factory.mktemp("tls")
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test authority")])
    ca_cert = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = folder / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    def leaf(host: str) -> tuple[str, str]:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cert_path, key_path = folder / f"{host}.pem", folder / f"{host}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        return str(cert_path), str(key_path)

    return Certificates(ca=str(ca_path), site=leaf("site.test"), other=leaf("other.test"))


@pytest.fixture
def https_site(certificates: Certificates, request: pytest.FixtureRequest) -> Iterator[int]:
    """An HTTPS page at ``/page``, with the certificate the test names (site.test by default)."""
    import ssl

    cert, key = getattr(certificates, getattr(request, "param", "site"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)

        def log_message(self, *_: object) -> None:
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


BODY = b"<html><body>Over TLS</body></html>"


def _trust(monkeypatch: pytest.MonkeyPatch, ca: str) -> None:
    import cloudscraper

    real = cloudscraper.create_scraper

    def create(**kwargs: object) -> object:
        scraper = real(**kwargs)
        scraper.verify = ca
        return scraper

    monkeypatch.setattr(cloudscraper, "create_scraper", create)


async def test_a_cloudscraper_https_page_is_fetched_with_its_certificate_checked(
    https_site: int, certificates: Certificates, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust(monkeypatch, certificates.ca)
    _resolve_to(monkeypatch, _https_pin(https_site))

    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(f"https://site.test:{https_site}/page"), 5, logging.getLogger("test_deadline")), 10,
    )

    assert result is not None
    assert (result.status_code, result.content_bytes) == (200, BODY)


@pytest.mark.parametrize("https_site", ["other"], indirect=True)
async def test_a_cloudscraper_https_page_with_another_hosts_certificate_is_refused(
    https_site: int, certificates: Certificates, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust(monkeypatch, certificates.ca)
    _resolve_to(monkeypatch, _https_pin(https_site))

    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(f"https://site.test:{https_site}/page"), 5, logging.getLogger("test_deadline")), 10,
    )

    assert result is None


async def test_a_cloudscraper_https_page_from_an_unknown_authority_is_refused(
    https_site: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_to(monkeypatch, _https_pin(https_site))

    result = await asyncio.wait_for(
        _hops_cloudscraper(_walk(f"https://site.test:{https_site}/page"), 5, logging.getLogger("test_deadline")), 10,
    )

    assert result is None


# -- The fetch pool and session cleanup --------------------------------------


async def test_a_request_waiting_for_a_fetch_thread_is_not_given_up_on_for_the_wait(
    trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The only thread is busy with a live body for longer than the deadline; the deadline counts
    # from when the second request starts, not from when it was queued.
    monkeypatch.setattr(fetch_strategy, "_max_queue_wait", lambda timeout: 30.0, raising=False)
    _resolve_to(monkeypatch, _loopback(trickle.port))
    url = f"http://127.0.0.1:{trickle.port}/steady"
    logger = logging.getLogger("test_deadline")

    first = asyncio.create_task(_hops_cloudscraper(_walk(url), SCRAPER_TIMEOUT, logger))
    await asyncio.sleep(0.2)
    second = asyncio.create_task(_hops_cloudscraper(_walk(url), SCRAPER_TIMEOUT, logger))
    results = await asyncio.wait_for(asyncio.gather(first, second), 15)

    for result in results:
        assert result is not None
        assert (result.status_code, len(result.content_bytes)) == (200, STEADY_CHUNK * STEADY_CHUNKS)
    assert trickle.requests == 2


async def test_a_request_given_up_on_while_queued_is_never_sent(
    trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_strategy, "_max_queue_wait", lambda timeout: 0.5, raising=False)
    _resolve_to(monkeypatch, _loopback(trickle.port))
    url = f"http://127.0.0.1:{trickle.port}/steady"
    logger = logging.getLogger("test_deadline")

    first = asyncio.create_task(_hops_cloudscraper(_walk(url), SCRAPER_TIMEOUT, logger))
    await asyncio.sleep(0.2)
    with pytest.raises(fetch_strategy.FetchPoolBusy, match="no web fetch thread came free"):
        await asyncio.wait_for(_hops_cloudscraper(_walk(url), SCRAPER_TIMEOUT, logger), 10)

    assert (await asyncio.wait_for(first, 10)) is not None
    assert await _thread_is_free(one_fetch_thread)
    assert trickle.requests == 1


async def test_a_session_whose_request_ends_late_is_still_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import curl_cffi.requests

    session = _WedgedSession()
    monkeypatch.setattr(curl_cffi.requests, "Session", lambda **_: session)
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
    monkeypatch.setattr(fetch_strategy, "_hop_deadline", lambda timeout: DEADLINE, raising=False)
    _resolve_to(monkeypatch, PublicTarget(
        scheme="http", host="site.test", port=80, addresses=(ipaddress.ip_address("93.184.215.14"),),
    ))

    try:
        result = await asyncio.wait_for(
            _hops_curl_cffi(_walk("http://site.test/"), 5, logging.getLogger("test_deadline")), 10,
        )
        assert result is None
        # Well past any wait the close might make for the request.
        await asyncio.sleep(3 * DEADLINE)
        assert not session.closed.is_set()
    finally:
        session.release.set()
    assert await asyncio.to_thread(session.closed.wait, 5), "the session was never closed"
    assert session.closed_mid_request is False


async def test_a_page_goes_to_aiohttp_once_no_fetch_thread_comes_free(
    trickle: Trickle, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import aiohttp

    # The only thread is held by a request that won't end; the queue gives up once, and neither
    # the other curl profiles, curl's second attempt nor cloudscraper wait for it again.
    cap = 0.5
    monkeypatch.setattr(fetch_strategy, "_max_queue_wait", lambda timeout: cap, raising=False)
    monkeypatch.setattr(fetch_strategy, "_MAX_QUEUE_WAIT", cap, raising=False)  # the previous head's name
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome", "safari", "edge"])
    _resolve_to(monkeypatch, _loopback(trickle.port))
    held = threading.Event()
    one_fetch_thread.submit(held.wait, 30)
    caplog.set_level(logging.WARNING)

    try:
        async with aiohttp.ClientSession() as session:
            started = time.monotonic()
            result = await asyncio.wait_for(fetch_strategy.fetch_url_with_fallback(
                f"http://127.0.0.1:{trickle.port}/plain", session, logging.getLogger("test_deadline"), timeout=5,
            ), 20)
            elapsed = time.monotonic() - started
    finally:
        held.set()

    assert result is not None
    assert (result.status_code, result.content_bytes, result.strategy) == (200, BODY, "aiohttp")
    assert cap < elapsed < 3 * cap
    assert len(trickle.agents) == 1 and "aiohttp" in trickle.agents[0]
    assert caplog.text.count("Gave up waiting for a thread to fetch") == 1


class _ClosingSession:
    """A curl_cffi Session or cloudscraper scraper stand-in that only records being closed."""

    def __init__(self) -> None:
        from requests.adapters import HTTPAdapter

        self.closed = threading.Event()
        self.curl_options: dict = {}
        self.adapters = {"https://": HTTPAdapter()}

    def mount(self, prefix: str, adapter: object) -> None:
        self.adapters[prefix] = adapter

    def get(self, url: str, **kwargs: object) -> object:
        raise AssertionError("no request should have been sent")

    def close(self) -> None:
        self.closed.set()


@pytest.mark.parametrize("strategy", ["curl_cffi", "cloudscraper"])
async def test_sessions_of_pages_that_found_no_free_thread_are_closed_at_once(
    strategy: str, one_fetch_thread: ThreadPoolExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import SimpleNamespace

    import curl_cffi.requests

    sessions: list[_ClosingSession] = []

    def new_session(**_: object) -> _ClosingSession:
        sessions.append(_ClosingSession())
        return sessions[-1]

    monkeypatch.setattr(curl_cffi.requests, "Session", new_session)
    monkeypatch.setitem(sys.modules, "cloudscraper", SimpleNamespace(create_scraper=new_session))
    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
    monkeypatch.setattr(fetch_strategy, "_max_queue_wait", lambda timeout: 0.3, raising=False)
    _resolve_to(monkeypatch, PublicTarget(
        scheme="http", host="site.test", port=80, addresses=(ipaddress.ip_address("93.184.215.14"),),
    ))
    hops = fetch_strategy._hops_curl_cffi if strategy == "curl_cffi" else fetch_strategy._hops_cloudscraper
    held = threading.Event()
    one_fetch_thread.submit(held.wait, 30)

    try:
        for page in range(3):
            with pytest.raises(fetch_strategy.FetchPoolBusy):
                await asyncio.wait_for(
                    hops(_walk(f"http://site.test/{page}"), 5, logging.getLogger("test_deadline")), 10,
                )
        # The only fetch thread is still held: the closes must not need it.
        for session in sessions:
            assert await asyncio.to_thread(session.closed.wait, 3), "a session waited for a fetch thread"
        assert len(sessions) == 3
    finally:
        held.set()
