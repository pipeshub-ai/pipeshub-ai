"""Unit tests for app.agents.mcp.wrapper.MCPToolWrapper."""

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

if "fastmcp" not in sys.modules:
    _fastmcp = types.ModuleType("fastmcp")
    _fastmcp.Client = object
    sys.modules["fastmcp"] = _fastmcp

if "langchain_core.tools" not in sys.modules:
    _langchain_core = types.ModuleType("langchain_core")
    _langchain_core_tools = types.ModuleType("langchain_core.tools")

    class _StubBaseTool:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    _langchain_core_tools.BaseTool = _StubBaseTool
    sys.modules["langchain_core"] = _langchain_core
    sys.modules["langchain_core.tools"] = _langchain_core_tools


@pytest.fixture
def tool_info():
    from app.agents.mcp.models import MCPToolInfo

    return MCPToolInfo(
        name="grep",
        namespaced_name="mcp_srv_grep",
        description="Search files",
        input_schema={
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
        },
        server_name="MyServer",
        instance_id="instance-uuid-1",
    )


@pytest.fixture
def base_state(tool_info):
    return {
        "mcp_server_configs": {
            tool_info.instance_id: {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "mcp"],
            }
        }
    }


class TestMCPToolWrapperMetadata:
    def test_tool_name_and_description(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(tool_info, state={}, manager=MagicMock())
        assert w.name == tool_info.namespaced_name
        assert w.description == tool_info.description

    def test_args_property(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(tool_info, state={}, manager=MagicMock())
        assert w.args == {"pattern": {"type": "string"}}


class TestMCPToolWrapperRun:
    def test_run_raises(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(tool_info, state={}, manager=MagicMock())
        with pytest.raises(NotImplementedError, match="async"):
            w._run()


class TestMCPToolWrapperArun:
    async def test_arun_calls_mcp_tool(self, tool_info, base_state):
        from app.agents.mcp.wrapper import MCPToolWrapper

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value="done")
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=mock_client)

        w = MCPToolWrapper(tool_info, state=base_state, manager=mgr)
        out = await w._arun(pattern="foo")

        assert out == "done"
        mgr.connect.assert_awaited()
        mock_client.call_tool.assert_awaited_once_with("grep", {"pattern": "foo"})

    async def test_arun_no_config_returns_error(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(
            tool_info,
            state={"mcp_server_configs": {}},
            manager=MagicMock(),
        )
        out = await w._arun()
        data = json.loads(out)
        assert "error" in data
        assert "instance-uuid-1" in data["error"]

    async def test_arun_handles_exception(self, tool_info, base_state):
        from app.agents.mcp.wrapper import MCPToolWrapper

        mgr = MagicMock()
        mgr.connect = AsyncMock(side_effect=ConnectionError("refused"))

        w = MCPToolWrapper(tool_info, state=base_state, manager=mgr)
        out = await w._arun(x=1)
        data = json.loads(out)
        assert "error" in data
        assert "MCP tool execution failed" in data["error"]


class TestMCPToolWrapperExtractResult:
    def test_extract_result_none(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        assert MCPToolWrapper._extract_result(None) == ""

    def test_extract_result_string(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        assert MCPToolWrapper._extract_result("hello") == "hello"

    def test_extract_result_with_content(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        part1 = SimpleNamespace(text="a")
        part2 = SimpleNamespace(text="b")
        result = SimpleNamespace(content=[part1, part2])
        assert MCPToolWrapper._extract_result(result) == "a\nb"


class TestMCPToolWrapperResolveConfig:
    def test_resolve_config_stdio_with_api_token(self, tool_info):
        from app.agents.mcp.models import MCPTransport
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                tool_info.instance_id: {
                    "transport": "stdio",
                    "command": "mcp",
                    "args": [],
                    "requiredEnv": ["MY_TOKEN"],
                    "auth": {"apiToken": "secret-val"},
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state, manager=MagicMock())
        cfg = w._resolve_config()
        assert cfg is not None
        assert cfg.transport == MCPTransport.STDIO
        assert cfg.env == {"MY_TOKEN": "secret-val"}

    def test_resolve_config_http_with_token(self, tool_info):
        from app.agents.mcp.models import MCPTransport
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                tool_info.instance_id: {
                    "transport": "sse",
                    "url": "https://api.example/mcp",
                    "auth": {"apiToken": "tok-9"},
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state, manager=MagicMock())
        cfg = w._resolve_config()
        assert cfg is not None
        assert cfg.transport == MCPTransport.SSE
        assert cfg.headers.get("Authorization") == "Bearer tok-9"


# ---------------------------------------------------------------------------
# New test classes — added to cover gaps in the original test suite
# ---------------------------------------------------------------------------


class TestIsTransientError:
    """Tests for the module-level _is_transient_error() helper."""

    def test_timeout_is_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("connection timed out") is True

    def test_service_unavailable_is_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("503 service unavailable") is True

    def test_connection_refused_is_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("connection refused") is True

    def test_502_bad_gateway_is_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("502 bad gateway") is True

    def test_try_again_is_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("please try again") is True

    def test_unauthorized_is_not_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("401 unauthorized") is False

    def test_forbidden_is_not_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("403 forbidden") is False

    def test_not_found_is_not_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("404 not found") is False

    def test_invalid_is_not_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("invalid request") is False

    def test_authentication_failure_is_not_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("authentication failed") is False

    def test_non_transient_wins_over_transient_keyword(self):
        # "unauthorized" overrides "timed out"
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("unauthorized: request timed out") is False

    def test_unknown_error_text_is_not_transient(self):
        from app.agents.mcp.wrapper import _is_transient_error

        assert _is_transient_error("something went completely wrong") is False


class TestStripFixedParams:
    """Direct tests for MCPToolWrapper._strip_fixed_params."""

    def test_removes_key_from_properties_and_required(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        schema = {
            "type": "object",
            "properties": {
                "cloudId": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["cloudId", "query"],
        }
        result = MCPToolWrapper._strip_fixed_params(schema, {"cloudId": "abc-123"})

        assert "cloudId" not in result["properties"]
        assert "query" in result["properties"]
        assert "cloudId" not in result["required"]
        assert "query" in result["required"]

    def test_empty_fixed_params_returns_schema_unchanged(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
        result = MCPToolWrapper._strip_fixed_params(schema, {})

        assert result == schema

    def test_none_fixed_params_returns_schema_unchanged(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        result = MCPToolWrapper._strip_fixed_params(schema, None)

        assert result == schema

    def test_none_schema_returned_as_is(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        result = MCPToolWrapper._strip_fixed_params(None, {"k": "v"})

        assert result is None

    def test_schema_without_required_key(self):
        from app.agents.mcp.wrapper import MCPToolWrapper

        schema = {
            "type": "object",
            "properties": {"cloudId": {"type": "string"}, "q": {"type": "string"}},
        }
        result = MCPToolWrapper._strip_fixed_params(schema, {"cloudId": "id"})

        assert "cloudId" not in result["properties"]
        assert "q" in result["properties"]
        assert "required" not in result  # untouched since it was absent


class TestMCPToolWrapperSetManager:
    def test_set_manager_injects(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(tool_info, state={})
        assert w._manager is None

        mgr = MagicMock()
        w.set_manager(mgr)
        assert w._manager is mgr

    def test_set_manager_to_none_clears(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        mgr = MagicMock()
        w = MCPToolWrapper(tool_info, state={}, manager=mgr)
        w.set_manager(None)
        assert w._manager is None


class TestMCPToolWrapperExtractResultExtended:
    def test_extract_result_dict_serialised_to_json(self):
        import json

        from app.agents.mcp.wrapper import MCPToolWrapper

        data = {"key": "value", "count": 42}
        result = MCPToolWrapper._extract_result(data)
        parsed = json.loads(result)

        assert parsed == data

    def test_extract_result_content_item_without_text_attr(self):
        from types import SimpleNamespace

        from app.agents.mcp.wrapper import MCPToolWrapper

        item_with_text = SimpleNamespace(text="hello")
        item_without_text = SimpleNamespace(value=123)  # no 'text' attribute
        result_obj = SimpleNamespace(content=[item_with_text, item_without_text])

        output = MCPToolWrapper._extract_result(result_obj)

        assert "hello" in output
        # item_without_text should fall back to str()
        assert str(item_without_text) in output

    def test_extract_result_list_serialised_to_json(self):
        import json

        from app.agents.mcp.wrapper import MCPToolWrapper

        result = MCPToolWrapper._extract_result([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]


class TestMCPToolWrapperArunRetry:
    """Retry logic and ephemeral manager cleanup inside _arun."""

    async def test_retries_on_transient_error_then_succeeds(self, tool_info, base_state):
        from unittest.mock import patch

        from app.agents.mcp.wrapper import MCPToolWrapper

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(
            side_effect=[Exception("connection timed out"), "result_on_retry"]
        )
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.wrapper.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            w = MCPToolWrapper(tool_info, state=base_state, manager=mgr)
            out = await w._arun(pattern="test")

        assert out == "result_on_retry"
        assert mock_client.call_tool.call_count == 2
        assert mock_sleep.call_count == 1  # slept once between attempts

    async def test_exhausts_retries_and_returns_error_json(self, tool_info, base_state):
        import json
        from unittest.mock import patch

        from app.agents.mcp.wrapper import MCP_MAX_RETRIES, MCPToolWrapper

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(side_effect=Exception("service unavailable"))
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.wrapper.asyncio.sleep", new_callable=AsyncMock):
            w = MCPToolWrapper(tool_info, state=base_state, manager=mgr)
            out = await w._arun()

        data = json.loads(out)
        assert "error" in data
        assert mock_client.call_tool.call_count == MCP_MAX_RETRIES

    async def test_non_transient_error_does_not_retry(self, tool_info, base_state):
        import json
        from unittest.mock import patch

        from app.agents.mcp.wrapper import MCPToolWrapper

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(side_effect=Exception("401 unauthorized"))
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.wrapper.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            w = MCPToolWrapper(tool_info, state=base_state, manager=mgr)
            out = await w._arun()

        data = json.loads(out)
        assert "error" in data
        assert mock_client.call_tool.call_count == 1
        mock_sleep.assert_not_called()

    async def test_ephemeral_manager_disconnect_called_on_success(self, tool_info, base_state):
        from unittest.mock import patch

        from app.agents.mcp.wrapper import MCPToolWrapper

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value="done")
        mock_manager = MagicMock()
        mock_manager.connect = AsyncMock(return_value=mock_client)
        mock_manager.disconnect_all = AsyncMock()

        with patch("app.agents.mcp.wrapper.MCPClientManager", return_value=mock_manager):
            w = MCPToolWrapper(tool_info, state=base_state, manager=None)
            await w._arun()

        mock_manager.disconnect_all.assert_awaited_once()

    async def test_ephemeral_manager_disconnect_called_on_error(self, tool_info, base_state):
        import json
        from unittest.mock import patch

        from app.agents.mcp.wrapper import MCPToolWrapper

        mock_manager = MagicMock()
        mock_manager.connect = AsyncMock(side_effect=ConnectionError("refused"))
        mock_manager.disconnect_all = AsyncMock()

        with patch("app.agents.mcp.wrapper.MCPClientManager", return_value=mock_manager):
            w = MCPToolWrapper(tool_info, state=base_state, manager=None)
            out = await w._arun()

        data = json.loads(out)
        assert "error" in data
        mock_manager.disconnect_all.assert_awaited_once()

    async def test_injects_fixed_params_at_call_time(self, base_state):
        from app.agents.mcp.models import MCPToolInfo
        from app.agents.mcp.wrapper import MCPToolWrapper

        tool_with_fixed = MCPToolInfo(
            name="create_issue",
            namespaced_name="mcp_jira_create_issue",
            description="Create issue",
            input_schema={
                "type": "object",
                "properties": {
                    "cloudId": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
            server_name="jira",
            instance_id="instance-uuid-1",  # must match base_state key
            fixed_params={"cloudId": "cloud-abc"},
        )

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value="created")
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=mock_client)

        w = MCPToolWrapper(tool_with_fixed, state=base_state, manager=mgr)
        await w._arun(summary="Fix bug")

        positional_kwargs = mock_client.call_tool.call_args[0][1]
        assert positional_kwargs["cloudId"] == "cloud-abc"
        assert positional_kwargs["summary"] == "Fix bug"

    async def test_fixed_params_not_visible_in_schema(self, tool_info):
        from app.agents.mcp.models import MCPToolInfo
        from app.agents.mcp.wrapper import MCPToolWrapper

        tool_with_fixed = MCPToolInfo(
            name="t",
            namespaced_name="mcp_s_t",
            input_schema={
                "type": "object",
                "properties": {
                    "cloudId": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["cloudId", "query"],
            },
            server_name="s",
            instance_id="i",
            fixed_params={"cloudId": "c123"},
        )

        w = MCPToolWrapper(tool_with_fixed, state={})

        assert "cloudId" not in w.args
        assert "query" in w.args


class TestMCPToolWrapperResolveConfigExtended:
    """Additional _resolve_config scenarios: OAUTH, HEADERS, invalid enums."""

    def test_resolve_config_oauth_uses_credentials_access_token(self, tool_info):
        from app.agents.mcp.models import MCPAuthMode, MCPTransport
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                "instance-uuid-1": {
                    "transport": "sse",
                    "url": "https://api.example/mcp",
                    "authMode": "oauth",
                    "credentials": {"access_token": "oauth-access-token"},
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state)
        cfg = w._resolve_config()

        assert cfg is not None
        assert cfg.transport == MCPTransport.SSE
        assert cfg.auth_mode == MCPAuthMode.OAUTH
        assert cfg.headers.get("Authorization") == "Bearer oauth-access-token"

    def test_resolve_config_oauth_falls_back_to_auth_token(self, tool_info):
        from app.agents.mcp.models import MCPAuthMode
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                "instance-uuid-1": {
                    "transport": "streamable_http",
                    "url": "https://api.example/mcp",
                    "authMode": "oauth",
                    "auth": {"apiToken": "fallback-token"},
                    "credentials": {},
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state)
        cfg = w._resolve_config()

        assert cfg is not None
        assert cfg.auth_mode == MCPAuthMode.OAUTH
        assert cfg.headers.get("Authorization") == "Bearer fallback-token"

    def test_resolve_config_headers_mode(self, tool_info):
        from app.agents.mcp.models import MCPAuthMode, MCPTransport
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                "instance-uuid-1": {
                    "transport": "streamable_http",
                    "url": "https://api.example/mcp",
                    "authMode": "headers",
                    "auth": {
                        "headerName": "X-API-Key",
                        "headerValue": "custom-key-789",
                    },
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state)
        cfg = w._resolve_config()

        assert cfg is not None
        assert cfg.transport == MCPTransport.STREAMABLE_HTTP
        assert cfg.auth_mode == MCPAuthMode.HEADERS
        assert cfg.headers.get("X-API-Key") == "custom-key-789"

    def test_resolve_config_headers_mode_default_header_name(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                "instance-uuid-1": {
                    "transport": "sse",
                    "url": "https://api.example/mcp",
                    "authMode": "headers",
                    "auth": {"headerValue": "my-api-key"},
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state)
        cfg = w._resolve_config()

        assert cfg is not None
        # Default header name is "Authorization"
        assert cfg.headers.get("Authorization") == "my-api-key"

    def test_resolve_config_invalid_transport_falls_back_to_stdio(self, tool_info):
        from app.agents.mcp.models import MCPTransport
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                "instance-uuid-1": {
                    "transport": "completely_invalid",
                    "authMode": "none",
                    "command": "cmd",
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state)
        cfg = w._resolve_config()

        assert cfg is not None
        assert cfg.transport == MCPTransport.STDIO  # fallback

    def test_resolve_config_invalid_auth_mode_falls_back_to_none(self, tool_info):
        from app.agents.mcp.models import MCPAuthMode
        from app.agents.mcp.wrapper import MCPToolWrapper

        state = {
            "mcp_server_configs": {
                "instance-uuid-1": {
                    "transport": "stdio",
                    "authMode": "not_a_real_mode",
                    "command": "cmd",
                }
            }
        }
        w = MCPToolWrapper(tool_info, state=state)
        cfg = w._resolve_config()

        assert cfg is not None
        assert cfg.auth_mode == MCPAuthMode.NONE  # fallback

    def test_resolve_config_missing_instance_returns_none(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(tool_info, state={"mcp_server_configs": {}})
        assert w._resolve_config() is None

    def test_resolve_config_missing_key_in_state_returns_none(self, tool_info):
        from app.agents.mcp.wrapper import MCPToolWrapper

        w = MCPToolWrapper(tool_info, state={})
        assert w._resolve_config() is None
