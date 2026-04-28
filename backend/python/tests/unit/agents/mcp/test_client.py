"""Unit tests for app.agents.mcp.client.MCPClientManager."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "fastmcp" not in sys.modules:
    _fastmcp = types.ModuleType("fastmcp")
    _fastmcp.Client = object
    sys.modules["fastmcp"] = _fastmcp


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.list_tools = AsyncMock(return_value=[])
    client.call_tool = AsyncMock(return_value="ok")
    return client


class TestMCPClientManagerConnect:
    async def test_connect_stores_client(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_client = _mock_client()
        with patch("app.agents.mcp.client.Client", return_value=mock_client):
            mgr = MCPClientManager()
            cfg = MCPServerConfig(
                name="s1",
                transport=MCPTransport.STDIO,
                command="node",
                args=["server.js"],
            )
            out = await mgr.connect(cfg)
            assert out is mock_client
            assert mgr.get_client("s1") is mock_client
            mock_client.__aenter__.assert_awaited_once()

    async def test_connect_reuses_existing(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_client = _mock_client()
        with patch("app.agents.mcp.client.Client", return_value=mock_client) as client_cls:
            mgr = MCPClientManager()
            cfg = MCPServerConfig(
                name="same",
                transport=MCPTransport.STDIO,
                command="cmd",
            )
            first = await mgr.connect(cfg)
            second = await mgr.connect(cfg)
            assert first is second is mock_client
            assert client_cls.call_count == 1
            mock_client.__aenter__.assert_awaited_once()


class TestMCPClientManagerDisconnect:
    async def test_disconnect_removes_client(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_client = _mock_client()
        with patch("app.agents.mcp.client.Client", return_value=mock_client):
            mgr = MCPClientManager()
            cfg = MCPServerConfig(name="x", transport=MCPTransport.STDIO, command="c")
            await mgr.connect(cfg)
            await mgr.disconnect("x")
            assert mgr.get_client("x") is None
            mock_client.__aexit__.assert_awaited_once_with(None, None, None)

    async def test_disconnect_nonexistent_is_noop(self):
        from app.agents.mcp.client import MCPClientManager

        mgr = MCPClientManager()
        await mgr.disconnect("not-there")

    async def test_disconnect_all(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        c1, c2 = _mock_client(), _mock_client()
        with patch("app.agents.mcp.client.Client", side_effect=[c1, c2]):
            mgr = MCPClientManager()
            await mgr.connect(
                MCPServerConfig(name="a", transport=MCPTransport.STDIO, command="a")
            )
            await mgr.connect(
                MCPServerConfig(name="b", transport=MCPTransport.STDIO, command="b")
            )
            await mgr.disconnect_all()
            assert mgr.list_servers() == []


class TestMCPClientManagerTools:
    async def test_list_tools_success(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_client = _mock_client()
        tools = [MagicMock(name="t1")]
        mock_client.list_tools = AsyncMock(return_value=tools)
        with patch("app.agents.mcp.client.Client", return_value=mock_client):
            mgr = MCPClientManager()
            await mgr.connect(
                MCPServerConfig(name="srv", transport=MCPTransport.STDIO, command="c")
            )
            result = await mgr.list_tools("srv")
            assert result is tools
            mock_client.list_tools.assert_awaited_once()

    async def test_list_tools_not_connected(self):
        from app.agents.mcp.client import MCPClientManager

        mgr = MCPClientManager()
        with pytest.raises(RuntimeError, match="not connected"):
            await mgr.list_tools("missing")

    async def test_call_tool_success(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_client = _mock_client()
        mock_client.call_tool = AsyncMock(return_value={"r": 1})
        with patch("app.agents.mcp.client.Client", return_value=mock_client):
            mgr = MCPClientManager()
            await mgr.connect(
                MCPServerConfig(name="srv", transport=MCPTransport.STDIO, command="c")
            )
            out = await mgr.call_tool("srv", "do_it", {"a": 1})
            assert out == {"r": 1}
            mock_client.call_tool.assert_awaited_once_with("do_it", {"a": 1})

    async def test_call_tool_not_connected(self):
        from app.agents.mcp.client import MCPClientManager

        mgr = MCPClientManager()
        with pytest.raises(RuntimeError, match="not connected"):
            await mgr.call_tool("nope", "tool", {})


class TestMCPClientManagerBuildClient:
    def test_build_client_stdio_creates_stdio_transport(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(
            name="s",
            transport=MCPTransport.STDIO,
            command="npx",
            args=["-y", "@mcp/server"],
            env={"FOO": "bar"},
        )
        with patch("app.agents.mcp.client.StdioTransport") as transport_cls, \
             patch("app.agents.mcp.client.Client") as client_cls:
            mock_transport = MagicMock()
            transport_cls.return_value = mock_transport
            MCPClientManager._build_client(cfg)

        transport_cls.assert_called_once_with(
            command="npx",
            args=["-y", "@mcp/server"],
            env={"FOO": "bar"},
        )
        client_cls.assert_called_once_with(mock_transport)

    def test_build_client_sse_creates_sse_transport(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(
            name="remote",
            transport=MCPTransport.SSE,
            url="https://mcp.example/sse",
            headers={"X-Custom": "1"},
        )
        with patch("app.agents.mcp.client.SSETransport") as transport_cls, \
             patch("app.agents.mcp.client.Client") as client_cls:
            mock_transport = MagicMock()
            transport_cls.return_value = mock_transport
            MCPClientManager._build_client(cfg)

        transport_cls.assert_called_once_with(
            url="https://mcp.example/sse",
            headers={"X-Custom": "1"},
        )
        client_cls.assert_called_once_with(mock_transport)

    def test_build_client_streamable_http_creates_streamable_transport(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(
            name="remote",
            transport=MCPTransport.STREAMABLE_HTTP,
            url="https://mcp.example/mcp",
            headers={"Authorization": "Bearer tok"},
        )
        with patch("app.agents.mcp.client.StreamableHttpTransport") as transport_cls, \
             patch("app.agents.mcp.client.Client") as client_cls:
            mock_transport = MagicMock()
            transport_cls.return_value = mock_transport
            MCPClientManager._build_client(cfg)

        transport_cls.assert_called_once_with(
            url="https://mcp.example/mcp",
            headers={"Authorization": "Bearer tok"},
        )
        client_cls.assert_called_once_with(mock_transport)

    def test_build_client_http_no_url_raises(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(
            name="bad",
            transport=MCPTransport.SSE,
            url="",
        )
        with pytest.raises(ValueError, match="no URL"):
            MCPClientManager._build_client(cfg)

    def test_build_client_oauth_missing_authorization_header_logs_warning(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(
            name="oauth_srv",
            transport=MCPTransport.STREAMABLE_HTTP,
            url="https://mcp.example/mcp",
            auth_mode=MCPAuthMode.OAUTH,
            headers={},  # No Authorization header
        )
        with patch("app.agents.mcp.client.StreamableHttpTransport"), \
             patch("app.agents.mcp.client.Client"), \
             patch("app.agents.mcp.client.logger") as mock_logger:
            MCPClientManager._build_client(cfg)

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "OAuth" in warning_msg or "Authorization" in warning_msg


class TestMCPClientManagerAdditional:
    """Extra tests covering list_servers and disconnect error-swallowing."""

    async def test_list_servers_returns_connected_names(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        c1, c2 = _mock_client(), _mock_client()
        with patch("app.agents.mcp.client.Client", side_effect=[c1, c2]):
            mgr = MCPClientManager()
            await mgr.connect(
                MCPServerConfig(name="alpha", transport=MCPTransport.STDIO, command="a")
            )
            await mgr.connect(
                MCPServerConfig(name="beta", transport=MCPTransport.STDIO, command="b")
            )

        assert set(mgr.list_servers()) == {"alpha", "beta"}

    async def test_list_servers_empty_when_nothing_connected(self):
        from app.agents.mcp.client import MCPClientManager

        mgr = MCPClientManager()
        assert mgr.list_servers() == []

    async def test_disconnect_swallows_aexit_error(self):
        from app.agents.mcp.client import MCPClientManager
        from app.agents.mcp.models import MCPServerConfig, MCPTransport

        mock_client = _mock_client()
        mock_client.__aexit__ = AsyncMock(side_effect=RuntimeError("cleanup failed"))

        with patch("app.agents.mcp.client.Client", return_value=mock_client):
            mgr = MCPClientManager()
            cfg = MCPServerConfig(name="x", transport=MCPTransport.STDIO, command="c")
            await mgr.connect(cfg)
            # Must not propagate the RuntimeError from __aexit__
            await mgr.disconnect("x")

        # Client must have been removed from the registry before __aexit__ was called
        assert mgr.get_client("x") is None
