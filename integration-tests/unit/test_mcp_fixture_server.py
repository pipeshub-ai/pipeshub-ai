"""The mcp-fixture server speaks MCP well enough for the client PipesHub uses.

PipesHub reaches external MCP servers through fastmcp's streamable HTTP
transport (backend/python/app/agents/mcp/client.py), the same client these
tests drive, so a server this accepts is one the agent can call. Runs the
real server in a thread; no stack needed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from helper.mcp_fixture import ORDER_TOOL, McpFixture

pytestmark = pytest.mark.unit

_SERVER = Path(__file__).resolve().parents[1] / "mcp_fixture" / "server.py"
_spec = importlib.util.spec_from_file_location("mcp_fixture_server", _SERVER)
assert _spec and _spec.loader
server_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server_module
_spec.loader.exec_module(server_module)


@pytest.fixture
def fixture_url() -> Iterator[str]:
    server_module._calls.clear()
    server = server_module.serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


async def _list_and_call(url: str, order_id: str) -> tuple[list[str], str, bool]:
    async with Client(StreamableHttpTransport(url)) as client:
        tools = [tool.name for tool in await client.list_tools()]
        result = await client.call_tool_mcp(ORDER_TOOL, {"order_id": order_id})
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        return tools, text, bool(result.isError)


def test_the_mcp_client_lists_and_calls_the_tool(fixture_url: str) -> None:
    tools, text, is_error = asyncio.run(_list_and_call(f"{fixture_url}/mcp", "KO-4417"))

    assert tools == [ORDER_TOOL]
    assert not is_error
    assert text == server_module.order_status("KO-4417")


def test_each_call_is_logged_until_reset(fixture_url: str) -> None:
    fixture = McpFixture(test_url=fixture_url, connector_url=f"{fixture_url}/mcp")
    assert fixture.healthy()

    asyncio.run(_list_and_call(f"{fixture_url}/mcp", "KO-1"))
    calls = fixture.calls()
    assert [(c["tool"], c["arguments"]) for c in calls] == [(ORDER_TOOL, {"order_id": "KO-1"})]

    fixture.reset()
    assert fixture.calls() == []


def test_an_unknown_tool_is_an_error_not_a_crash() -> None:
    reply = server_module.handle_rpc(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope"}}
    )
    assert reply["error"]["code"] == -32602


def test_a_notification_gets_no_reply() -> None:
    assert server_module.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
