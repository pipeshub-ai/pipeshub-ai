"""WP88-FU-3: a server that takes the connection and never finishes the handshake did not answer in time, so its
call reads `timeout`, as the other black-holed dependencies do. A refused connection stays `unavailable`."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import pytest

from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter, mcp_error_code
from app.agents.agent_loop.mcp_tool_loader import _failure_reason, answered_listings
from app.agents.mcp import client as client_module
from app.agents.mcp import url_guard
from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.discovery import discover_tool_listing
from app.agents.mcp.errors import MCPConnectionError, MCPConnectTimeoutError
from app.agents.mcp.failure import MCPFailureReason, classify_mcp_failure
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerConfig,
    MCPToolInfo,
    MCPTransport,
)
from app.harness.mcp_hub.breaker import server_down
from app.utils.url_fetcher import PublicTarget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

_CONNECT_S = 0.2


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


@asynccontextmanager
async def _black_hole() -> AsyncIterator[int]:
    """A listener that accepts every connection, reads what it is sent and never answers."""
    held: list[asyncio.StreamWriter] = []

    async def _hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)
        while await reader.read(65536):
            pass

    server = await asyncio.start_server(_hold, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        for writer in held:
            writer.close()
        server.close()


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _config(transport: MCPTransport, url: str, **overrides: Any) -> MCPServerConfig:  # noqa: ANN401
    return MCPServerConfig(
        id="inst-bh", org_id="org-1", created_by="u1", name="BlackHole", type_id="custom", transport=transport,
        url=url, auth_mode=MCPAuthMode.NONE, created_at=0, updated_at=0, connect_timeout_seconds=_CONNECT_S,
        **overrides,
    )


def _path(transport: MCPTransport) -> str:
    return "sse" if transport is MCPTransport.SSE else "mcp"


@pytest.mark.parametrize("transport", [MCPTransport.STREAMABLE_HTTP, MCPTransport.SSE])
async def test_a_silent_handshake_is_a_timeout(transport: MCPTransport) -> None:
    async with _black_hole() as port:
        with pytest.raises(MCPConnectTimeoutError) as caught:
            await asyncio.wait_for(
                MCPClientManager(_config(transport, f"http://127.0.0.1:{port}/{_path(transport)}")).open(), 10,
            )

    error = caught.value
    assert isinstance(error, MCPConnectionError), "every `except MCPConnectionError` still catches it"
    assert isinstance(error, TimeoutError)
    assert str(error) == f"Timed out connecting to the MCP server after {_CONNECT_S:g}s"
    assert classify_mcp_failure(error) is MCPFailureReason.TIMEOUT
    assert mcp_error_code(error) == "timeout"


async def test_a_refused_connection_stays_unavailable() -> None:
    with pytest.raises(MCPConnectionError) as caught:
        await MCPClientManager(_config(MCPTransport.STREAMABLE_HTTP, f"http://127.0.0.1:{_free_port()}/mcp")).open()

    assert not isinstance(caught.value, TimeoutError)
    assert mcp_error_code(caught.value) == "unavailable"


async def test_a_listing_keeps_the_connect_timeout_it_hit() -> None:
    # The listing's own outer deadline must not re-type the server's connect timeout as a plain connection error.
    async with _black_hole() as port:
        config = _config(MCPTransport.STREAMABLE_HTTP, f"http://127.0.0.1:{port}/mcp")
        with pytest.raises(MCPConnectTimeoutError, match="Timed out connecting"):
            await discover_tool_listing(config, {}, timeout_seconds=5)


def test_an_unanswered_listing_is_still_an_outage() -> None:
    error = MCPConnectTimeoutError(f"Timed out connecting to the MCP server after {_CONNECT_S:g}s")
    assert server_down(error), "the breaker counts it (MX-08)"

    class _Context:
        mcp_servers = [{"instanceId": "inst-bh", "name": "BlackHole"}]
        mcp_tool_load_failures = [{"instanceId": "inst-bh", "name": "BlackHole", "reason": _failure_reason(error)}]

    assert _failure_reason(error) == "timeout"
    assert answered_listings(_Context(), {}) == {}, "a server whose handshake stalled did not answer (WP-79 D3)"  # type: ignore[arg-type]


async def test_the_model_reads_the_connect_timeout_not_the_call_timeout() -> None:
    error = MCPConnectTimeoutError(f"Timed out connecting to the MCP server after {_CONNECT_S:g}s")

    class _Sessions:
        async def call(self, *_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
            raise error

    server = ResolvedMCPServer(
        instance_id="inst-bh", name="BlackHole", display_name="Black Hole",
        instance={"authMode": "none"}, auth={}, owner_id="user-1", attached_tools=None,
    )
    info = MCPToolInfo(name="search", namespaced_name="mcp_bh_search", description="Search", input_schema={})
    output = await MCPToolAdapter(server, info, _Sessions()).execute(q="x")  # type: ignore[arg-type]

    assert (output.success, output.error_code) == (False, "timeout")
    assert output.error == str(error)
