"""Unit tests for app.agents.mcp.client."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp.client import (
    MCPClientManager,
    MCPConnectionError,
    build_transport,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport


def _config(**overrides) -> MCPServerConfig:
    defaults = dict(
        id="inst-1",
        org_id="org-1",
        created_by="user-1",
        name="Server",
        transport=MCPTransport.STDIO,
        auth_mode=MCPAuthMode.NONE,
        created_at=0,
        updated_at=0,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


class TestBuildTransport:
    def test_stdio_builds_stdio_transport(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx", args=["-y", "server"])
        with patch("app.agents.mcp.client.StdioTransport") as mock_cls:
            build_transport(config, env={"KEY": "value"})
            mock_cls.assert_called_once_with(
                command="npx", args=["-y", "server"], env={"KEY": "value"}, log_file=None, keep_alive=None,
            )

    def test_stdio_forwards_stderr_log_file_and_keep_alive(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx", args=["-y", "server"])
        with patch("app.agents.mcp.client.StdioTransport") as mock_cls:
            build_transport(config, stderr_log_file=Path("/tmp/mcp-stderr.log"), keep_alive=False)
            mock_cls.assert_called_once_with(
                command="npx", args=["-y", "server"], env={}, log_file=Path("/tmp/mcp-stderr.log"), keep_alive=False,
            )

    def test_stdio_without_command_raises(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command=None)
        with pytest.raises(MCPConnectionError, match="no command"):
            build_transport(config)

    def test_sse_builds_sse_transport(self) -> None:
        config = _config(transport=MCPTransport.SSE, url="https://example.com/sse")
        with patch("app.agents.mcp.client.SSETransport") as mock_cls:
            build_transport(config, headers={"Authorization": "Bearer x"})
            mock_cls.assert_called_once_with(url="https://example.com/sse", headers={"Authorization": "Bearer x"})

    def test_sse_without_url_raises(self) -> None:
        config = _config(transport=MCPTransport.SSE, url=None)
        with pytest.raises(MCPConnectionError, match="no url"):
            build_transport(config)

    def test_streamable_http_builds_transport(self) -> None:
        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url="https://example.com/mcp")
        with patch("app.agents.mcp.client.StreamableHttpTransport") as mock_cls:
            build_transport(config)
            mock_cls.assert_called_once_with(url="https://example.com/mcp", headers={})

    def test_streamable_http_without_url_raises(self) -> None:
        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url=None)
        with pytest.raises(MCPConnectionError, match="no url"):
            build_transport(config)


class TestMCPClientManagerConnect:
    @pytest.mark.asyncio
    async def test_connect_yields_client_and_closes(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            async with manager.connect() as client:
                assert client is mock_client
            mock_client.__aenter__.assert_awaited_once()
            mock_client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_wraps_unexpected_errors(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="boom"):
                async with manager.connect():
                    pass

    @pytest.mark.asyncio
    async def test_connect_builds_transport_with_keep_alive_false(self) -> None:
        """One-shot `connect()` must not leave the STDIO subprocess running — see
        `build_transport`'s docstring for why `keep_alive=True` (fastmcp's default)
        would orphan it here."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()) as mock_build, \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            async with manager.connect():
                pass
            assert mock_build.call_args.kwargs["keep_alive"] is False
            assert mock_build.call_args.kwargs["stderr_log_file"] is not None

    @pytest.mark.asyncio
    async def test_connect_annotates_error_with_captured_stderr(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock(return_value=False)

        captured_path: dict[str, Path] = {}

        def _fake_build_transport(*_args, stderr_log_file: Path, **_kwargs) -> MagicMock:
            captured_path["path"] = stderr_log_file
            return MagicMock()

        async def _write_stderr_then_raise() -> None:
            captured_path["path"].write_text("exa-mcp-server: missing EXA_API_KEY\n")
            raise RuntimeError("boom")

        with patch("app.agents.mcp.client.build_transport", side_effect=_fake_build_transport), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            mock_client.__aenter__ = AsyncMock(side_effect=_write_stderr_then_raise)
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="missing EXA_API_KEY"):
                async with manager.connect():
                    pass

    @pytest.mark.asyncio
    async def test_connect_propagates_mcp_connection_error_unwrapped(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command=None)
        manager = MCPClientManager(config)
        with pytest.raises(MCPConnectionError, match="no command"):
            async with manager.connect():
                pass

    @pytest.mark.asyncio
    async def test_list_tools_delegates_to_client(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.list_tools = AsyncMock(return_value=["tool1", "tool2"])

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            tools = await manager.list_tools()
            assert tools == ["tool1", "tool2"]

    @pytest.mark.asyncio
    async def test_call_tool_delegates_to_client(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.call_tool = AsyncMock(return_value={"result": "ok"})

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            result = await manager.call_tool("search", {"q": "test"})
            assert result == {"result": "ok"}
            mock_client.call_tool.assert_awaited_once_with("search", {"q": "test"})


class TestMCPClientManagerSession:
    """The long-lived `open()` / `call_tool_in_session()` / `aclose()` trio used by
    the agent-loop runtime (`MCPSessionManager`) to reuse one connection across every
    tool call an instance receives within a chat turn."""

    @pytest.mark.asyncio
    async def test_open_builds_transport_with_keep_alive_true(self) -> None:
        """Unlike one-shot `connect()`, a session must survive between calls."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()) as mock_build, \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            assert mock_build.call_args.kwargs["keep_alive"] is True
            assert mock_build.call_args.kwargs["stderr_log_file"] is not None

    @pytest.mark.asyncio
    async def test_open_is_idempotent(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()) as mock_build, \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            first = await manager.open()
            second = await manager.open()
            assert first is second is mock_client
            mock_build.assert_called_once()

    @pytest.mark.asyncio
    async def test_open_annotates_error_with_captured_stderr_and_cleans_up(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()

        captured_path: dict[str, Path] = {}

        def _fake_build_transport(*_args, stderr_log_file: Path, **_kwargs) -> MagicMock:
            captured_path["path"] = stderr_log_file
            return MagicMock()

        async def _write_stderr_then_raise() -> None:
            captured_path["path"].write_text("exa-mcp-server: missing EXA_API_KEY\n")
            raise RuntimeError("boom")

        with patch("app.agents.mcp.client.build_transport", side_effect=_fake_build_transport), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            mock_client.__aenter__ = AsyncMock(side_effect=_write_stderr_then_raise)
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="missing EXA_API_KEY"):
                await manager.open()

        assert not captured_path["path"].exists()

    @pytest.mark.asyncio
    async def test_call_tool_in_session_raises_when_not_open(self) -> None:
        manager = MCPClientManager(_config())
        with pytest.raises(MCPConnectionError, match="is not open"):
            await manager.call_tool_in_session("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_in_session_delegates_to_open_client(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.call_tool = AsyncMock(return_value={"result": "ok"})

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            result = await manager.call_tool_in_session("search", {"q": "test"})
            assert result == {"result": "ok"}
            mock_client.call_tool.assert_awaited_once_with("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_in_session_annotates_error_with_captured_stderr(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("Connection closed"))

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            manager._session_stderr_path.write_text("exa-mcp-server crashed: rate limited\n")
            with pytest.raises(RuntimeError, match="rate limited"):
                await manager.call_tool_in_session("search", {"q": "test"})
            await manager.aclose()

    @pytest.mark.asyncio
    async def test_aclose_calls_client_close_not_aexit(self) -> None:
        """`__aexit__()` alone would leave a `keep_alive=True` STDIO subprocess running
        forever — see `aclose()`'s docstring."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.close = AsyncMock(return_value=None)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            stderr_path = manager._session_stderr_path
            await manager.aclose()
            mock_client.close.assert_awaited_once()
            mock_client.__aexit__.assert_not_awaited()
            assert manager._session_client is None
            assert stderr_path is not None and not stderr_path.exists()

    @pytest.mark.asyncio
    async def test_aclose_is_a_noop_when_never_opened(self) -> None:
        manager = MCPClientManager(_config())
        await manager.aclose()  # must not raise

    @pytest.mark.asyncio
    async def test_aclose_swallows_close_errors(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.close = AsyncMock(side_effect=RuntimeError("already dead"))

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            await manager.aclose()  # must not raise
            assert manager._session_client is None
