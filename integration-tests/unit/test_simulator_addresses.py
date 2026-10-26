"""The test process reaches the simulators it is configured for, and no other loopback address.

``allow_the_simulators`` widens the connector address guard for this process only, so a mistake
here is a test process that can reach more than it should, or a suite whose setup fails on
"The address localhost isn't allowed". Requests go through the real guard to a real local server.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.agents.mcp import url_guard
from app.sources.client.http import address_guard
from app.sources.client.http.address_guard import AddressNotAllowedError, guarded_async_client
from helper import simulator_addresses
from helper.simulator_addresses import SIMULATOR_URL_SETTINGS, allow_the_simulators, simulator_origins


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def simulator() -> Iterator[int]:
    server = HTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SIMULATOR_URL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    # The root conftest may already have patched these; each test starts from the real lookup.
    real = simulator_addresses.url_fetcher.resolve_public_http_target
    monkeypatch.setattr(address_guard, "resolve_public_http_target", real)
    monkeypatch.setattr(url_guard, "resolve_public_http_target", real)


async def _get(url: str) -> int:
    async with guarded_async_client(allow_private=True) as client:
        return (await client.get(url)).status_code


async def test_a_configured_simulator_on_localhost_is_reached(monkeypatch: pytest.MonkeyPatch, simulator: int) -> None:
    monkeypatch.setenv("JIRA_TEST_BASE_URL", f"http://localhost:{simulator}")
    allow_the_simulators()
    assert await _get(f"http://localhost:{simulator}/rest/api/3/project/QE") == 200


async def test_without_the_exception_the_guard_refuses_the_simulator(simulator: int) -> None:
    with pytest.raises(AddressNotAllowedError):
        await _get(f"http://localhost:{simulator}/rest/api/3/project/QE")


async def test_another_port_on_loopback_is_still_refused(monkeypatch: pytest.MonkeyPatch, simulator: int) -> None:
    monkeypatch.setenv("JIRA_TEST_BASE_URL", "http://localhost:8102")
    allow_the_simulators()
    with pytest.raises(AddressNotAllowedError):
        await _get(f"http://localhost:{simulator}/")


async def test_the_same_port_under_another_spelling_is_still_refused(
    monkeypatch: pytest.MonkeyPatch, simulator: int
) -> None:
    monkeypatch.setenv("JIRA_TEST_BASE_URL", f"http://localhost:{simulator}")
    allow_the_simulators()
    with pytest.raises(AddressNotAllowedError):
        await _get(f"http://127.0.0.1:{simulator}/")


def test_only_loopback_simulator_settings_become_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUENCE_TEST_BASE_URL", "http://localhost:8101/wiki/api/v2")
    monkeypatch.setenv("NOTION_API_BASE_URL", "http://127.0.0.1:8103/v1")
    monkeypatch.setenv("JIRA_TEST_BASE_URL", "https://lumenfold.atlassian.net")
    monkeypatch.setenv("GMAIL_SIM_URL", "not a url")
    assert simulator_origins() == {
        ("http", "localhost", 8101): "127.0.0.1",
        ("http", "127.0.0.1", 8103): "127.0.0.1",
    }


def test_with_no_simulator_configured_the_guard_is_untouched() -> None:
    before = address_guard.resolve_public_http_target, url_guard.resolve_public_http_target
    allow_the_simulators()
    assert (address_guard.resolve_public_http_target, url_guard.resolve_public_http_target) == before
