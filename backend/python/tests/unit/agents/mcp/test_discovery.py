"""Unit tests for app.agents.mcp.discovery."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.discovery import (
    build_auth_env_and_headers,
    build_namespaced_tool_name,
    discover_tools,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport


def _config(**overrides) -> MCPServerConfig:
    defaults = dict(
        id="inst-1",
        org_id="org-1",
        created_by="user-1",
        name="Server",
        type_id="brave_search",
        transport=MCPTransport.STDIO,
        auth_mode=MCPAuthMode.NONE,
        created_at=0,
        updated_at=0,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


class TestBuildNamespacedToolName:
    def test_normalizes_server_type(self) -> None:
        assert build_namespaced_tool_name("Brave Search", "web_search") == "mcp_brave_search_web_search"

    def test_hyphenated_type(self) -> None:
        assert build_namespaced_tool_name("google-drive", "list_files") == "mcp_google_drive_list_files"


class TestBuildAuthEnvAndHeaders:
    def test_none_mode_returns_empty(self) -> None:
        config = _config(auth_mode=MCPAuthMode.NONE)
        env, headers = build_auth_env_and_headers(config, {})
        assert env == {}
        assert headers == {}

    def test_api_token_stdio_uses_required_env_var(self) -> None:
        config = _config(
            auth_mode=MCPAuthMode.API_TOKEN,
            transport=MCPTransport.STDIO,
            required_env=["BRAVE_API_KEY"],
        )
        env, headers = build_auth_env_and_headers(config, {"apiToken": "secret"})
        assert env == {"BRAVE_API_KEY": "secret"}
        assert headers == {}

    def test_api_token_stdio_defaults_env_var_name(self) -> None:
        config = _config(auth_mode=MCPAuthMode.API_TOKEN, transport=MCPTransport.STDIO, required_env=[])
        env, _ = build_auth_env_and_headers(config, {"apiToken": "secret"})
        assert env == {"API_TOKEN": "secret"}

    def test_api_token_stdio_multi_env_map(self) -> None:
        config = _config(
            auth_mode=MCPAuthMode.API_TOKEN,
            transport=MCPTransport.STDIO,
            required_env=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
            optional_env=["SLACK_CHANNEL_IDS"],
        )
        env, headers = build_auth_env_and_headers(
            config,
            {
                "apiToken": "xoxb-token",
                "env": {
                    "SLACK_BOT_TOKEN": "xoxb-token",
                    "SLACK_TEAM_ID": "T01234567",
                    "SLACK_CHANNEL_IDS": "C1,C2",
                    "IGNORED": "nope",
                },
            },
        )
        assert env == {
            "SLACK_BOT_TOKEN": "xoxb-token",
            "SLACK_TEAM_ID": "T01234567",
            "SLACK_CHANNEL_IDS": "C1,C2",
        }
        assert headers == {}

    def test_api_token_stdio_env_map_falls_back_to_api_token_for_primary(self) -> None:
        config = _config(
            auth_mode=MCPAuthMode.API_TOKEN,
            transport=MCPTransport.STDIO,
            required_env=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        )
        env, _ = build_auth_env_and_headers(
            config,
            {"apiToken": "xoxb-token", "env": {"SLACK_TEAM_ID": "T01234567"}},
        )
        assert env == {"SLACK_BOT_TOKEN": "xoxb-token", "SLACK_TEAM_ID": "T01234567"}

    def test_api_token_stdio_ignores_non_dict_env(self) -> None:
        config = _config(
            auth_mode=MCPAuthMode.API_TOKEN,
            transport=MCPTransport.STDIO,
            required_env=["BRAVE_API_KEY"],
        )
        env, headers = build_auth_env_and_headers(
            config, {"apiToken": "secret", "env": "not-a-dict"}
        )
        assert env == {"BRAVE_API_KEY": "secret"}
        assert headers == {}

    def test_api_token_stdio_without_api_token_or_env_returns_empty(self) -> None:
        config = _config(
            auth_mode=MCPAuthMode.API_TOKEN,
            transport=MCPTransport.STDIO,
            required_env=["BRAVE_API_KEY"],
        )
        env, headers = build_auth_env_and_headers(config, {"env": {}})
        assert env == {}
        assert headers == {}

    def test_api_token_http_uses_bearer_header(self) -> None:
        config = _config(auth_mode=MCPAuthMode.API_TOKEN, transport=MCPTransport.STREAMABLE_HTTP)
        env, headers = build_auth_env_and_headers(config, {"apiToken": "secret"})
        assert env == {}
        assert headers == {"Authorization": "Bearer secret"}

    def test_api_token_missing_returns_empty(self) -> None:
        config = _config(auth_mode=MCPAuthMode.API_TOKEN, transport=MCPTransport.STREAMABLE_HTTP)
        env, headers = build_auth_env_and_headers(config, {})
        assert env == {}
        assert headers == {}

    def test_headers_mode_uses_credential_header_name(self) -> None:
        config = _config(auth_mode=MCPAuthMode.HEADERS, transport=MCPTransport.SSE, header_name="X-Default")
        _, headers = build_auth_env_and_headers(
            config, {"headerName": "X-Api-Key", "headerValue": "abc123"}
        )
        assert headers == {"X-Api-Key": "abc123"}

    def test_headers_mode_falls_back_to_instance_header_name(self) -> None:
        config = _config(auth_mode=MCPAuthMode.HEADERS, transport=MCPTransport.SSE, header_name="X-Default")
        _, headers = build_auth_env_and_headers(config, {"headerValue": "abc123"})
        assert headers == {"X-Default": "abc123"}

    def test_headers_mode_missing_value_returns_empty(self) -> None:
        config = _config(auth_mode=MCPAuthMode.HEADERS, transport=MCPTransport.SSE)
        _, headers = build_auth_env_and_headers(config, {"headerName": "X-Api-Key"})
        assert headers == {}

    def test_oauth_mode_uses_bearer_access_token(self) -> None:
        config = _config(auth_mode=MCPAuthMode.OAUTH, transport=MCPTransport.SSE)
        _, headers = build_auth_env_and_headers(config, {"accessToken": "tok"})
        assert headers == {"Authorization": "Bearer tok"}

    def test_oauth_mode_missing_token_returns_empty(self) -> None:
        config = _config(auth_mode=MCPAuthMode.OAUTH, transport=MCPTransport.SSE)
        _, headers = build_auth_env_and_headers(config, {})
        assert headers == {}

    def test_unknown_auth_mode_returns_empty(self) -> None:
        config = _config(auth_mode=MCPAuthMode.OAUTH, transport=MCPTransport.SSE)
        object.__setattr__(config, "auth_mode", "unknown_mode")
        env, headers = build_auth_env_and_headers(config, {"accessToken": "tok", "apiToken": "x"})
        assert env == {}
        assert headers == {}


class _FakeTool:
    def __init__(self, name: str, description: str | None = None, input_schema: dict | None = None) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {}


class TestDiscoverTools:
    @pytest.mark.asyncio
    async def test_discovers_and_namespaces_tools(self) -> None:
        config = _config(type_id="brave_search")
        fake_tools = [_FakeTool("web_search", "Search the web", {"type": "object"})]

        mock_manager = MagicMock()
        mock_manager.list_tools = AsyncMock(return_value=fake_tools)

        with patch("app.agents.mcp.discovery.MCPClientManager", return_value=mock_manager):
            tools = await discover_tools(config, {})

        assert len(tools) == 1
        assert tools[0].name == "web_search"
        assert tools[0].namespaced_name == "mcp_brave_search_web_search"
        assert tools[0].description == "Search the web"
        assert tools[0].input_schema == {"type": "object"}

    @pytest.mark.asyncio
    async def test_falls_back_to_instance_name_when_no_type_id(self) -> None:
        config = _config(type_id=None, name="My Custom Server")
        fake_tools = [_FakeTool("do_thing")]

        mock_manager = MagicMock()
        mock_manager.list_tools = AsyncMock(return_value=fake_tools)

        with patch("app.agents.mcp.discovery.MCPClientManager", return_value=mock_manager):
            tools = await discover_tools(config, {})

        assert tools[0].namespaced_name == "mcp_my_custom_server_do_thing"

    @pytest.mark.asyncio
    async def test_skips_tools_without_a_name(self) -> None:
        config = _config()
        fake_tools = [{"description": "no name here"}, _FakeTool("valid_tool")]

        mock_manager = MagicMock()
        mock_manager.list_tools = AsyncMock(return_value=fake_tools)

        with patch("app.agents.mcp.discovery.MCPClientManager", return_value=mock_manager):
            tools = await discover_tools(config, {})

        assert len(tools) == 1
        assert tools[0].name == "valid_tool"

    @pytest.mark.asyncio
    async def test_dict_shaped_tools_supported(self) -> None:
        config = _config()
        fake_tools = [{"name": "dict_tool", "description": "desc", "inputSchema": {"a": 1}}]

        mock_manager = MagicMock()
        mock_manager.list_tools = AsyncMock(return_value=fake_tools)

        with patch("app.agents.mcp.discovery.MCPClientManager", return_value=mock_manager):
            tools = await discover_tools(config, {})

        assert tools[0].name == "dict_tool"
        assert tools[0].input_schema == {"a": 1}

    @pytest.mark.asyncio
    async def test_timeout_raises_connection_error(self) -> None:
        config = _config()

        async def _hang(*args, **kwargs):
            import asyncio
            await asyncio.sleep(10)

        mock_manager = MagicMock()
        mock_manager.list_tools = _hang

        with patch("app.agents.mcp.discovery.MCPClientManager", return_value=mock_manager):
            with pytest.raises(MCPConnectionError, match="Timed out"):
                await discover_tools(config, {}, timeout_seconds=0.01)

    @pytest.mark.asyncio
    async def test_connection_error_propagates(self) -> None:
        config = _config()
        mock_manager = MagicMock()
        mock_manager.list_tools = AsyncMock(side_effect=MCPConnectionError("unreachable"))

        with patch("app.agents.mcp.discovery.MCPClientManager", return_value=mock_manager):
            with pytest.raises(MCPConnectionError, match="unreachable"):
                await discover_tools(config, {})
