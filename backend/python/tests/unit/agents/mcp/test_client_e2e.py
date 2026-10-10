"""`MCPClientManager` against real MCP servers: the SDK's own server over streamable HTTP and
SSE (uvicorn on a free local port) and as a local process.

The URL guard refuses loopback, so these tests approve 127.0.0.1 in its one resolution step and
skip the pre-connect check; every request still goes through the guarded transport. A small ASGI
gate in front of the HTTP server plays what real servers do: refuse a token, speak only the
initialize handshake, forget a session, redirect elsewhere.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import sys
import textwrap
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

from app.agents.mcp import client as client_module
from app.agents.mcp import url_guard
from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPConnectionLostError,
    MCPHttpStatusError,
    MCPRequestNotSentError,
    MCPSessionExpiredError,
    MCPUrlBlockedError,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport
from app.utils.url_fetcher import PublicTarget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

_TOKEN = "good-token"


def _mcp_server() -> MCPServer:
    server = MCPServer("e2e", instructions="Use search for everything.")

    @server.tool()
    def search(q: str) -> str:
        """Search."""
        return f"found {q}"

    @server.tool()
    async def slow(seconds: float) -> str:
        """Takes its time."""
        await asyncio.sleep(seconds)
        return "done"

    return server


class _Gate:
    """ASGI middleware in front of the MCP app."""

    def __init__(self, app: Any) -> None:  # noqa: ANN401
        self.app = app
        self.token: str | None = None
        self.initialize_only = False
        self.forget_sessions = False
        self.redirect_to: str | None = None
        self.methods: list[str] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        messages: list[dict[str, Any]] = []
        body = b""
        if scope["method"] == "POST":
            more = True
            while more:
                message = await receive()
                messages.append(message)
                body += message.get("body", b"")
                more = message.get("more_body", False)
        method = _method_of(body)
        if method:
            self.methods.append(method)

        if self.redirect_to is not None:
            await _respond(send, 307, b"", extra=[(b"location", self.redirect_to.encode())])
            return
        if self.token is not None and headers.get("authorization") != f"Bearer {self.token}":
            await _respond(send, 401, b'{"error": "invalid_token"}', extra=[(b"www-authenticate", b"Bearer")])
            return
        if self.initialize_only and method == "server/discover":
            await _respond(send, 400, _rpc_error(-32600, "Bad Request: No valid session ID provided"))
            return
        if self.forget_sessions and headers.get("mcp-session-id"):
            await _respond(send, 404, _rpc_error(-32001, "Session not found"))
            return

        async def _replay() -> dict[str, Any]:
            return messages.pop(0) if messages else await receive()

        await self.app(scope, _replay, send)


def _method_of(body: bytes) -> str | None:
    try:
        return json.loads(body).get("method") if body else None
    except (ValueError, AttributeError):
        return None


def _rpc_error(code: int, message: str) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}).encode()


async def _respond(send: Any, status: int, body: bytes, *, extra: list[tuple[bytes, bytes]] | None = None) -> None:  # noqa: ANN401
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), *(extra or [])]})
    await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def _serving(app: Any) -> AsyncIterator[int]:  # noqa: ANN401
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(autouse=True)
def _loopback_allowed() -> Iterator[None]:
    async def _approve(url: str, *, allow_private: bool) -> PublicTarget:
        parts = urlsplit(url)
        return PublicTarget(parts.scheme, parts.hostname or "", parts.port or 80, (ipaddress.ip_address("127.0.0.1"),))

    client_module._initialize_only.clear()
    with patch.object(url_guard, "assert_mcp_url_allowed", _approve), \
         patch.object(client_module, "check_mcp_url", new=AsyncMock()):
        yield
    client_module._initialize_only.clear()


def _config(transport: MCPTransport, url: str | None = None, **overrides: Any) -> MCPServerConfig:  # noqa: ANN401
    return MCPServerConfig(
        id="inst-e2e", org_id="org-1", created_by="u1", name="E2E", type_id="custom",
        transport=transport, url=url, auth_mode=MCPAuthMode.NONE, created_at=0, updated_at=0, **overrides,
    )


@pytest.fixture
async def http_server() -> AsyncIterator[tuple[_Gate, str]]:
    gate = _Gate(_mcp_server().streamable_http_app())
    async with _serving(gate) as port:
        yield gate, f"http://127.0.0.1:{port}/mcp"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestStreamableHttp:
    async def test_tools_are_listed_and_called(self, http_server: tuple[_Gate, str]) -> None:
        _, url = http_server
        manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url))
        assert {t.name for t in await manager.list_tools()} == {"search", "slow"}

        await manager.open()
        try:
            assert manager.server_instructions == "Use search for everything."
            result = await manager.call_tool_in_session("search", {"q": "x"})
            assert result.content[0].text == "found x"
            assert result.is_error is False
        finally:
            await manager.aclose()

    async def test_concurrent_calls_share_the_session(self, http_server: tuple[_Gate, str]) -> None:
        _, url = http_server
        manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url))
        await manager.open()
        try:
            slow, fast = await asyncio.gather(
                manager.call_tool_in_session("slow", {"seconds": 0.3}), manager.call_tool_in_session("search", {"q": "y"}),
            )
            assert (slow.content[0].text, fast.content[0].text) == ("done", "found y")
        finally:
            await manager.aclose()

    async def test_an_initialize_only_server_is_remembered_and_not_probed_again(self, http_server: tuple[_Gate, str]) -> None:
        gate, url = http_server
        gate.initialize_only = True
        for _ in range(2):
            manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url))
            await manager.open()
            assert (await manager.call_tool_in_session("search", {"q": "z"})).content[0].text == "found z"
            await manager.aclose()

        assert gate.methods.count("server/discover") == 1
        assert gate.methods.count("initialize") == 2

    async def test_a_refused_token_at_connect_is_a_401(self, http_server: tuple[_Gate, str]) -> None:
        gate, url = http_server
        gate.token = _TOKEN
        with pytest.raises(MCPHttpStatusError) as caught:
            await MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url), headers=_bearer("stale")).open()
        assert caught.value.status_code == 401

    async def test_a_session_refused_mid_turn_carries_a_fresh_token_without_reconnecting(self, http_server: tuple[_Gate, str]) -> None:
        gate, url = http_server
        gate.token = _TOKEN
        manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url), headers=_bearer(_TOKEN))
        await manager.open()
        try:
            gate.token = "rotated"
            with pytest.raises(MCPHttpStatusError) as caught:
                await manager.call_tool_in_session("search", {"q": "a"})
            assert caught.value.status_code == 401
            assert manager.is_open

            manager.update_headers(_bearer("rotated"))
            assert (await manager.call_tool_in_session("search", {"q": "b"})).content[0].text == "found b"
        finally:
            await manager.aclose()

    async def test_a_forgotten_session_is_an_expired_one(self, http_server: tuple[_Gate, str]) -> None:
        gate, url = http_server
        gate.initialize_only = True  # the initialize handshake issues a session id
        manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url))
        await manager.open()
        try:
            gate.forget_sessions = True
            with pytest.raises(MCPSessionExpiredError):
                await manager.call_tool_in_session("search", {"q": "a"})
            assert not manager.is_open
        finally:
            await manager.aclose()

    async def test_a_server_that_isnt_there_was_never_sent_anything(self) -> None:
        url = f"http://127.0.0.1:{_free_port()}/mcp"
        with pytest.raises(MCPRequestNotSentError):
            await MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url)).open()

    async def test_a_redirect_elsewhere_is_blocked(self, http_server: tuple[_Gate, str]) -> None:
        gate, url = http_server
        gate.redirect_to = f"http://127.0.0.1:{_free_port()}/mcp"
        with pytest.raises(MCPUrlBlockedError):
            await MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url)).open()

    async def test_a_call_that_times_out_tells_the_server_and_keeps_the_session(self, http_server: tuple[_Gate, str]) -> None:
        gate, url = http_server
        gate.initialize_only = True
        manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url, call_timeout_seconds=0.3))
        await manager.open()
        try:
            with pytest.raises(TimeoutError):
                await manager.call_tool_in_session("slow", {"seconds": 5})
            assert manager.is_open
            assert (await manager.call_tool_in_session("search", {"q": "c"})).content[0].text == "found c"
        finally:
            await manager.aclose()
        assert "notifications/cancelled" in gate.methods

    async def test_closing_during_a_call_ends_it(self, http_server: tuple[_Gate, str]) -> None:
        _, url = http_server
        manager = MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, url))
        await manager.open()
        call = asyncio.ensure_future(manager.call_tool_in_session("slow", {"seconds": 10}))
        await asyncio.sleep(0.2)

        await asyncio.wait_for(manager.aclose(), 15)

        with pytest.raises(MCPConnectionError):
            await asyncio.wait_for(call, 5)


class TestSse:
    @pytest.fixture
    async def sse_server(self) -> AsyncIterator[tuple[_Gate, str]]:
        gate = _Gate(_mcp_server().sse_app())
        async with _serving(gate) as port:
            yield gate, f"http://127.0.0.1:{port}/sse"

    async def test_tools_are_listed_and_called(self, sse_server: tuple[_Gate, str]) -> None:
        _, url = sse_server
        manager = MCPClientManager(_config(MCPTransport.SSE, url))
        assert {t.name for t in await manager.list_tools()} == {"search", "slow"}
        await manager.open()
        try:
            assert (await manager.call_tool_in_session("search", {"q": "s"})).content[0].text == "found s"
        finally:
            await manager.aclose()

    async def test_a_refused_token_at_connect_is_a_401(self, sse_server: tuple[_Gate, str]) -> None:
        gate, url = sse_server
        gate.token = _TOKEN
        with pytest.raises(MCPHttpStatusError) as caught:
            await MCPClientManager(_config(MCPTransport.SSE, url), headers=_bearer("stale")).open()
        assert caught.value.status_code == 401


_LOCAL_SERVER = textwrap.dedent('''
    import os
    import sys
    import threading

    from mcp.server.mcpserver import MCPServer

    if os.environ.get("E2E_FAIL_AT_START"):
        print("e2e-server: missing E2E_API_KEY", file=sys.stderr, flush=True)
        sys.exit(1)

    server = MCPServer("e2e-local", instructions="Local server.")


    @server.tool()
    def search(q: str) -> str:
        """Search."""
        return f"local {q}"


    @server.tool()
    def crash() -> str:
        """Exits the process."""
        print("e2e-server: about to crash", file=sys.stderr, flush=True)
        os._exit(3)


    @server.tool()
    def exit_after_reply() -> str:
        """Answers, then exits."""
        threading.Timer(0.3, os._exit, [0]).start()
        return "bye"


    server.run()
''')


@pytest.fixture
def local_server(tmp_path: Path) -> Iterator[Path]:
    script = tmp_path / "e2e_server.py"
    script.write_text(_LOCAL_SERVER, encoding="utf-8")
    with patch.object(client_module, "resolve_stdio_launch", lambda config: (config.command, list(config.args))):
        yield script


class TestLocalServer:
    async def test_a_session_lists_calls_and_closes(self, local_server: Path) -> None:
        manager = MCPClientManager(_config(MCPTransport.STDIO, command=sys.executable, args=[str(local_server)]))
        await manager.open()
        try:
            assert manager.server_instructions == "Local server."
            assert [t.name for t in await manager.list_tools_in_session()] == ["search", "crash", "exit_after_reply"]
            assert (await manager.call_tool_in_session("search", {"q": "y"})).content[0].text == "local y"
        finally:
            await asyncio.wait_for(manager.aclose(), 15)
        assert not manager.is_open

    async def test_a_server_that_exits_mid_call_ends_the_session(self, local_server: Path, caplog: pytest.LogCaptureFixture) -> None:
        manager = MCPClientManager(_config(MCPTransport.STDIO, command=sys.executable, args=[str(local_server)]))
        await manager.open()
        try:
            with pytest.raises(MCPConnectionLostError):
                await manager.call_tool_in_session("crash", {})
            assert not manager.is_open
            assert "about to crash" in caplog.text
        finally:
            await manager.aclose()

    async def test_a_server_that_exits_between_calls_leaves_the_next_one_unsent(self, local_server: Path) -> None:
        """So the turn reconnects and sends it, instead of reporting it as possibly run."""
        manager = MCPClientManager(_config(MCPTransport.STDIO, command=sys.executable, args=[str(local_server)]))
        await manager.open()
        try:
            assert (await manager.call_tool_in_session("exit_after_reply", {})).content[0].text == "bye"
            for _ in range(100):
                if not manager.is_open:
                    break
                await asyncio.sleep(0.05)
            assert not manager.is_open
            with pytest.raises(MCPRequestNotSentError):
                await manager.call_tool_in_session("search", {"q": "y"})
        finally:
            await manager.aclose()

    async def test_a_server_that_fails_to_start_says_why_in_the_detail(self, local_server: Path) -> None:
        config = _config(MCPTransport.STDIO, command=sys.executable, args=[str(local_server)])
        with pytest.raises(MCPConnectionError) as caught:
            await MCPClientManager(config, env={"E2E_FAIL_AT_START": "1"}).open()

        assert "missing E2E_API_KEY" in caught.value.stderr_tail
        assert "missing E2E_API_KEY" not in str(caught.value)
