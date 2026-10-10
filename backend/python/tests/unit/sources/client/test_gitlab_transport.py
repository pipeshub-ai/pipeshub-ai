"""The GitLab session against a real local server: read timeouts and stale
keep-alive connections (N4GIT-01)."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import app.sources.client.gitlab.gitlab as gitlab_client
from app.sources.client.gitlab.gitlab import GitLabClientViaToken, _secure_session
from app.sources.external.gitlab.gitlab_data_source import GitLabDataSource

_CLIENT_TIMEOUT = 0.5
_PROJECT = {"id": 7, "name": "Demo Repository", "path_with_namespace": "test/demo-repository"}


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, hang: Callable[[int, float | None, float], bool]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hang = hang
        self.lock = threading.Lock()
        self.requests = 0
        self.connections = 0
        self.hung = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self._last_request_at: float | None = None
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.connections += 1  # type: ignore[attr-defined]

    def log_message(self, *args: object) -> None:
        pass

    def _answer(self) -> None:
        server: _Server = self.server  # type: ignore[assignment]
        now = time.monotonic()
        previous, self._last_request_at = self._last_request_at, now
        with server.lock:
            server.requests += 1
            n = server.requests
        if server.hang(n, previous, now):
            # A request that went into a dropped connection: no answer ever comes.
            with server.lock:
                server.hung += 1
            time.sleep(_CLIENT_TIMEOUT * 4)
            self.close_connection = True
            return
        body = json.dumps(_PROJECT).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._answer()


@pytest.fixture
def serve() -> Iterator[Callable[[Callable[[int, float | None, float], bool]], _Server]]:
    servers: list[_Server] = []

    def start(hang: Callable[[int, float | None, float], bool]) -> _Server:
        server = _Server(hang)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitlab_client, "_READ_TIMEOUT_RETRY_DELAY_SECONDS", 0.0, raising=False)


def _data_source(server: _Server) -> GitLabDataSource:
    client = GitLabClientViaToken(
        "token", server.url, _CLIENT_TIMEOUT, retry_transient_errors=True, auth_type="API_TOKEN",
    )
    client.create_client()
    return GitLabDataSource(client, base_url=server.url)


class TestReadTimeouts:
    def test_a_get_that_times_out_once_is_retried_and_reads_the_project(self, serve) -> None:
        server = serve(lambda n, _previous, _now: n == 1)

        res = _data_source(server).get_project("test/demo-repository")

        assert res.success, res.error
        assert res.data.id == 7
        assert server.requests == 2

    def test_a_get_that_keeps_timing_out_fails_after_one_retry(self, serve) -> None:
        server = serve(lambda _n, _previous, _now: True)

        res = _data_source(server).get_project("test/demo-repository")

        assert not res.success
        assert res.status_code is None
        assert server.requests == 2

    def test_a_post_that_times_out_is_not_sent_again(self, serve) -> None:
        server = serve(lambda _n, _previous, _now: True)

        with pytest.raises(requests.exceptions.ReadTimeout):
            _secure_session().post(f"{server.url}/api/v4/things", json={}, timeout=_CLIENT_TIMEOUT)

        assert server.requests == 1


class TestIdleConnections:
    def test_a_connection_idle_past_the_limit_is_not_reused(self, serve, monkeypatch) -> None:
        # The path drops a connection idle for 0.3 s without telling the client.
        server = serve(lambda _n, previous, now: previous is not None and now - previous > 0.3)
        monkeypatch.setattr(gitlab_client, "_POOL_IDLE_LIMIT_SECONDS", 0.2, raising=False)
        monkeypatch.setattr(gitlab_client, "_READ_TIMEOUT_RETRIES", 0, raising=False)
        data_source = _data_source(server)

        assert data_source.get_project("test/demo-repository").success
        time.sleep(0.5)
        started = time.monotonic()
        res = data_source.get_project("test/demo-repository")

        assert res.success, res.error
        assert time.monotonic() - started < _CLIENT_TIMEOUT
        assert server.hung == 0
        assert server.connections == 2

    def test_a_connection_used_within_the_limit_is_reused(self, serve) -> None:
        server = serve(lambda _n, _previous, _now: False)
        data_source = _data_source(server)

        assert data_source.get_project("test/demo-repository").success
        assert data_source.get_project("test/demo-repository").success

        assert server.connections == 1
