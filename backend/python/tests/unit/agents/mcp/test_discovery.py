"""Unit tests for app.agents.mcp.discovery.MCPToolDiscovery."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

if "fastmcp" not in sys.modules:
    _fastmcp = types.ModuleType("fastmcp")
    _fastmcp.Client = object
    sys.modules["fastmcp"] = _fastmcp


@pytest.fixture
def mock_manager():
    return MagicMock()


class TestMCPToolDiscoveryDiscoverTools:
    async def test_discover_tools_single_server(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        t1 = SimpleNamespace(
            name="alpha",
            description="d1",
            inputSchema={"type": "object", "properties": {"x": {}}},
        )
        t2 = SimpleNamespace(name="beta", description="d2", inputSchema=None)
        client = MagicMock()
        client.list_tools = AsyncMock(return_value=[t1, t2])
        mock_manager.connect = AsyncMock(return_value=client)

        disc = MCPToolDiscovery(mock_manager)
        cfg = MCPServerConfig(
            name="myserver",
            transport=MCPTransport.STDIO,
            command="c",
        )
        tools = await disc.discover_tools("inst-1", cfg)

        assert len(tools) == 2
        assert tools[0].name == "alpha"
        assert tools[0].namespaced_name == "mcp_myserver_alpha"
        assert tools[0].server_name == "myserver"
        assert tools[0].instance_id == "inst-1"
        assert tools[1].namespaced_name == "mcp_myserver_beta"

    async def test_discover_tools_namespace_format(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        tool = SimpleNamespace(
            name="do_thing",
            description="",
            inputSchema={},
        )
        client = MagicMock()
        client.list_tools = AsyncMock(return_value=[tool])
        mock_manager.connect = AsyncMock(return_value=client)

        disc = MCPToolDiscovery(mock_manager)
        cfg = MCPServerConfig(
            name="GitHub Server",
            transport=MCPTransport.STDIO,
            command="c",
        )
        out = await disc.discover_tools("i", cfg)
        assert out[0].namespaced_name == "mcp_github_server_do_thing"

    async def test_discover_tools_server_error(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_manager.connect = AsyncMock(side_effect=RuntimeError("boom"))

        disc = MCPToolDiscovery(mock_manager)
        cfg = MCPServerConfig(name="x", transport=MCPTransport.STDIO, command="c")
        result = await disc.discover_tools("id", cfg)
        assert result == []

    async def test_input_schema_passthrough(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool = SimpleNamespace(
            name="search",
            description="find",
            inputSchema=schema,
        )
        client = MagicMock()
        client.list_tools = AsyncMock(return_value=[tool])
        mock_manager.connect = AsyncMock(return_value=client)

        disc = MCPToolDiscovery(mock_manager)
        cfg = MCPServerConfig(name="s", transport=MCPTransport.STDIO, command="c")
        out = await disc.discover_tools("i", cfg)
        assert out[0].input_schema == schema


class TestMCPToolDiscoveryDiscoverAll:
    async def test_discover_all_multiple_servers(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        clients = []

        def make_client(tools):
            c = MagicMock()
            c.list_tools = AsyncMock(return_value=tools)
            clients.append(c)
            return c

        mock_manager.connect = AsyncMock(
            side_effect=[
                make_client(
                    [
                        SimpleNamespace(
                            name="a1",
                            description="",
                            inputSchema={},
                        )
                    ]
                ),
                make_client(
                    [
                        SimpleNamespace(
                            name="b1",
                            description="",
                            inputSchema={},
                        )
                    ]
                ),
            ]
        )

        disc = MCPToolDiscovery(mock_manager)
        configs = {
            "i1": MCPServerConfig(name="srv_a", transport=MCPTransport.STDIO, command="a"),
            "i2": MCPServerConfig(name="srv_b", transport=MCPTransport.STDIO, command="b"),
        }
        results = await disc.discover_all(configs)

        assert set(results.keys()) == {"i1", "i2"}
        assert len(results["i1"]) == 1
        assert len(results["i2"]) == 1
        assert results["i1"][0].namespaced_name == "mcp_srv_a_a1"
        assert results["i2"][0].namespaced_name == "mcp_srv_b_b1"


class TestMCPToolDiscoveryResolveTool:
    async def test_resolve_tool_found(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        tool = SimpleNamespace(name="orig", description="", inputSchema={})
        client = MagicMock()
        client.list_tools = AsyncMock(return_value=[tool])
        mock_manager.connect = AsyncMock(return_value=client)

        disc = MCPToolDiscovery(mock_manager)
        cfg = MCPServerConfig(name="ServerOne", transport=MCPTransport.STDIO, command="c")
        await disc.discover_tools("inst", cfg)

        ns = "mcp_serverone_orig"
        assert disc.resolve_tool(ns) == ("ServerOne", "orig")

    def test_resolve_tool_not_found(self, mock_manager):
        from app.agents.mcp.discovery import MCPToolDiscovery

        disc = MCPToolDiscovery(mock_manager)
        assert disc.resolve_tool("mcp_unknown_tool") is None
