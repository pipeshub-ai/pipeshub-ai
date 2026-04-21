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
