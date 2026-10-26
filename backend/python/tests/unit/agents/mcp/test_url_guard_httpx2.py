"""The URL guard on httpx2, the mcp SDK's HTTP library, and the wire record MCP traffic fills.

The guard is one implementation built for each library (`test_url_guard.py` covers the httpx
build); these pin what's specific to MCP traffic: a request that never reached the server is
answered with a stand-in 502 and its cause recorded, so it can't end the SDK's whole session.
"""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import httpx
import httpx2
import pytest

from app.agents.mcp import url_guard, wire
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import (
    McpGuardedTransport,
    guarded_mcp_http_client,
    guarded_mcp_http_client_factory,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_PUBLIC_IP = "93.184.216.34"
_URL = "https://mcp.example.com/mcp"


def _resolves_to(*addresses: str) -> "Callable[..., list]":
    def _getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]
    return _getaddrinfo


class _Recorder:
    """Inner transport: records what actually went to the network."""

    def __init__(self, response: "httpx2.Response | None" = None, error: "Exception | None" = None) -> None:
        self.requests: list[httpx2.Request] = []
        self._response = response
        self._error = error

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._response or httpx2.Response(200, json={"ok": True})


@pytest.fixture(autouse=True)
def _no_env_proxy() -> "Iterator[None]":
    with patch.object(url_guard.urllib.request, "getproxies", return_value={}):
        yield


class TestTheGuardOnHttpx2:
    async def test_a_request_is_pinned_to_the_validated_address(self) -> None:
        recorder = _Recorder()
        transport = McpGuardedTransport(_URL, allow_private=False, inner=httpx2.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.post(_URL, json={})

        assert response.status_code == 200
        sent = recorder.requests[0]
        assert sent.url.host == _PUBLIC_IP
        assert sent.headers["host"] == "mcp.example.com"
        assert sent.extensions["sni_hostname"] == "mcp.example.com"

    async def test_another_origin_gets_a_stand_in_502_and_the_block_is_recorded(self) -> None:
        recorder = _Recorder()
        transport = McpGuardedTransport(_URL, allow_private=False, inner=httpx2.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)), wire.recording() as record:
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.get("https://evil.example.net/steal")

        assert response.status_code == 502
        assert isinstance(record.unsent, MCPUrlBlockedError)
        assert recorder.requests == []

    async def test_a_host_that_resolves_to_loopback_is_refused_the_same_way(self) -> None:
        recorder = _Recorder()
        transport = McpGuardedTransport(
            "https://rebind.example.com/", allow_private=True, inner=httpx2.MockTransport(recorder),
        )
        with patch("socket.getaddrinfo", _resolves_to("127.0.0.1")), wire.recording() as record:
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.get("https://rebind.example.com/")

        assert response.status_code == 502
        assert isinstance(record.unsent, MCPUrlBlockedError)
        assert recorder.requests == []

    async def test_a_connection_that_fails_on_every_address_is_recorded_as_never_sent(self) -> None:
        recorder = _Recorder(error=httpx2.ConnectError("connection refused"))
        transport = McpGuardedTransport(_URL, allow_private=False, inner=httpx2.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP, "93.184.216.35")), wire.recording() as record:
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.post(_URL, json={})

        assert response.status_code == 502
        assert isinstance(record.unsent, httpx2.ConnectError)
        # Every validated address was tried before giving up.
        assert [r.url.host for r in recorder.requests] == [_PUBLIC_IP, "93.184.216.35"]

    @pytest.mark.parametrize("error", [httpx2.ReadTimeout("slow"), httpx2.ReadError("reset"), httpx2.RemoteProtocolError("bad")])
    async def test_a_failure_after_the_request_went_out_is_recorded_as_lost(self, error: Exception) -> None:
        """The server may have acted on it, so it isn't reported as never sent. Raised, it would
        end the SDK's session: a legacy server's call that outlived its timeout still hits the
        read timeout later."""
        transport = McpGuardedTransport(_URL, allow_private=False, inner=httpx2.MockTransport(_Recorder(error=error)))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)), wire.recording() as record:
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.post(_URL, json={})

        assert response.status_code == 502
        assert record.lost is error
        assert record.unsent is None

    @pytest.mark.parametrize("error", [httpx2.PoolTimeout("busy"), httpx2.UnsupportedProtocol("ftp")])
    async def test_a_failure_before_anything_left_is_recorded_as_never_sent(self, error: Exception) -> None:
        transport = McpGuardedTransport(_URL, allow_private=False, inner=httpx2.MockTransport(_Recorder(error=error)))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)), wire.recording() as record:
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.post(_URL, json={})

        assert response.status_code == 502
        assert record.unsent is error
        assert record.lost is None

    async def test_outside_a_recorded_operation_the_502_still_stands_in(self) -> None:
        transport = McpGuardedTransport(_URL, allow_private=False, inner=httpx2.MockTransport(_Recorder()))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.get("https://evil.example.net/")

        assert response.status_code == 502


class TestTheMcpHttpClient:
    async def test_every_response_and_a_sent_session_id_are_recorded(self) -> None:
        recorder = _Recorder(httpx2.Response(404, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "Session not found"}}))
        client = guarded_mcp_http_client(_URL, allow_private=False, inner=httpx2.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)), wire.recording() as record:
            async with client:
                await client.post(_URL, json={}, headers={"mcp-session-id": "s-1"})

        assert record.statuses == [404]
        assert record.error_status == 404
        assert record.sent_session_id is True

    def test_uses_the_mcp_sdk_default_timeouts_and_ignores_the_environment(self) -> None:
        client = guarded_mcp_http_client(_URL, allow_private=False)
        assert client.timeout.connect == 30.0
        assert client.timeout.read == 300.0
        assert client.trust_env is False

    def test_a_longer_read_timeout_replaces_the_default(self) -> None:
        client = guarded_mcp_http_client(_URL, allow_private=False, read_timeout=630.0)
        assert client.timeout.read == 630.0

    def test_headers_and_auth_are_used(self) -> None:
        auth = httpx2.BasicAuth("u", "p")
        client = guarded_mcp_http_client(_URL, allow_private=False, headers={"X-API-Key": "k"}, auth=auth)
        assert client.headers["x-api-key"] == "k"
        assert client.auth is auth

    def test_the_sse_factory_keeps_what_the_sdk_passes_in(self) -> None:
        factory = guarded_mcp_http_client_factory(_URL, allow_private=False, read_timeout=630.0)
        client = factory(headers={"X-API-Key": "k"}, timeout=httpx2.Timeout(5.0, read=45.0))
        assert client.timeout.read == 45.0
        assert client.headers["x-api-key"] == "k"
        assert isinstance(client._transport, McpGuardedTransport)


class TestCertificatesAreCheckedLikeHttpx:
    @pytest.fixture(autouse=True)
    def _fresh_contexts(self) -> "Iterator[None]":
        url_guard._ssl_context_for.cache_clear()
        yield
        url_guard._ssl_context_for.cache_clear()

    @pytest.mark.parametrize("lib", [httpx, httpx2])
    def test_the_inner_transport_uses_httpxs_certificate_settings(self, lib: object) -> None:
        """certifi, or SSL_CERT_FILE / SSL_CERT_DIR: httpx2 alone would use the OS trust store."""
        context = MagicMock(name="ssl-context")
        with patch.object(url_guard.httpx, "create_ssl_context", return_value=context) as create, \
             patch.object(lib, "AsyncHTTPTransport") as transport_cls:
            url_guard._inner_transport(lib, None)

        create.assert_called_once_with()
        assert transport_cls.call_args.kwargs["verify"] is context

    def test_the_context_is_built_once_and_shared(self) -> None:
        """Loading the bundle takes about 0.2 s on the event loop."""
        with patch.object(url_guard.httpx, "create_ssl_context", side_effect=lambda: MagicMock()) as create, \
             patch.object(httpx2, "AsyncHTTPTransport") as transport_cls:
            url_guard._inner_transport(httpx2, None)
            url_guard._inner_transport(httpx2, "http://proxy:3128")

        assert create.call_count == 1
        first, second = (call.kwargs["verify"] for call in transport_cls.call_args_list)
        assert first is second

    def test_another_certificate_setting_gets_its_own_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(url_guard.httpx, "create_ssl_context", side_effect=lambda: MagicMock()) as create, \
             patch.object(httpx2, "AsyncHTTPTransport"):
            monkeypatch.delenv("SSL_CERT_FILE", raising=False)
            url_guard._inner_transport(httpx2, None)
            monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/company.pem")
            url_guard._inner_transport(httpx2, None)

        assert create.call_count == 2

    def test_httpxs_settings_honour_ssl_cert_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        import certifi

        bundle = tmp_path / "company-ca.pem"
        bundle.write_text(open(certifi.where(), encoding="utf-8").read(), encoding="utf-8")
        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        with patch("ssl.SSLContext.load_verify_locations") as load:
            httpx.create_ssl_context()

        assert any(call.args and call.args[0] == str(bundle) for call in load.call_args_list)


class TestTheWireRecord:
    def test_each_operation_gets_its_own_record(self) -> None:
        with wire.recording() as outer:
            with wire.recording() as inner:
                assert wire.current_record() is inner
            assert wire.current_record() is outer
        assert wire.current_record() is None

    async def test_each_401_and_403_keeps_its_own_challenge(self) -> None:
        from types import SimpleNamespace

        def _response(status: int, *challenges: str) -> SimpleNamespace:
            return SimpleNamespace(
                status_code=status, request=SimpleNamespace(headers={}),
                headers=SimpleNamespace(get_list=lambda _name: list(challenges)),
            )

        with wire.recording() as record:
            await wire.record_response(_response(401, 'Bearer resource_metadata="https://a.example/prm"'))
            await wire.record_response(_response(200))
            await wire.record_response(_response(403, 'Bearer error="insufficient_scope", scope="admin"'))
            await wire.record_response(_response(500, 'Bearer error="nope"'))

        assert record.challenges == {
            401: {"resource_metadata": "https://a.example/prm"},
            403: {"error": "insufficient_scope", "scope": "admin"},
        }

    async def test_a_strange_response_never_raises(self) -> None:
        with wire.recording() as record:
            await wire.record_response(object())
        assert record.statuses == []

    def test_the_first_unsent_cause_is_kept(self) -> None:
        with wire.recording() as record:
            first = httpx2.ConnectError("one")
            wire.record_unsent(first)
            wire.record_unsent(httpx2.ConnectError("two"))
        assert record.unsent is first

    @pytest.mark.parametrize("statuses,error_status", [
        ([401], 401),
        ([400, 401], 401),
        # A legacy server's refusal of the version probe, then a good handshake.
        ([400, 200], None),
        ([200], None),
        ([], None),
    ])
    def test_the_error_status_is_the_one_the_operation_ended_with(self, statuses: list[int], error_status: int | None) -> None:
        assert wire.WireRecord(statuses=statuses).error_status == error_status

    def test_a_lost_request_is_recorded_once(self) -> None:
        with wire.recording() as record:
            first = httpx2.ReadError("one")
            wire.record_lost(first)
            wire.record_lost(httpx2.ReadError("two"))
        assert record.lost is first

    def test_nothing_is_recorded_outside_an_operation(self) -> None:
        wire.record_unsent(httpx2.ConnectError("x"))
        wire.record_lost(httpx2.ReadError("x"))
        assert wire.current_record() is None
