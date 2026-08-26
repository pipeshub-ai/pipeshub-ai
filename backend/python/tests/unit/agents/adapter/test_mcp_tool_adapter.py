"""`MCPToolAdapter` (`app/agents/agent_loop/mcp_tool_adapter.py`) — identity/
parameters derived from `MCPToolInfo`, and `execute()` result normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.tools.errors import ToolValidationError
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError


def _server() -> ResolvedMCPServer:
    return ResolvedMCPServer(
        instance_id="inst-1", name="JiraMCP", display_name="Jira MCP",
        instance={"authMode": "none"}, auth={}, owner_id="user-1", attached_tools=None,
    )


def _tool_info(**overrides: Any) -> MCPToolInfo:
    defaults: dict[str, Any] = {
        "name": "search", "namespaced_name": "mcp_jira_mcp_search",
        "description": "Search Jira issues",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "search text"}},
            "required": ["query"],
        },
    }
    defaults.update(overrides)
    return MCPToolInfo(**defaults)


@dataclass
class _FakeCallResult:
    is_error: bool = False
    data: Any = None
    content: list[Any] = field(default_factory=list)


@dataclass
class _TextBlock:
    text: str


class _FakeSessionManager:
    def __init__(self, result: Any = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[tuple[Any, str, dict]] = []

    async def call(self, server: ResolvedMCPServer, tool_name: str, arguments: dict) -> Any:
        self.calls.append((server, tool_name, arguments))
        if self._exc is not None:
            raise self._exc
        return self._result


def _make_adapter(session_manager: _FakeSessionManager) -> MCPToolAdapter:
    return MCPToolAdapter(_server(), _tool_info(), session_manager)


class TestIdentity:
    def test_name_is_the_namespaced_tool_name(self) -> None:
        assert _make_adapter(_FakeSessionManager()).name == "mcp_jira_mcp_search"

    def test_path_includes_instance_id_and_raw_tool_name(self) -> None:
        assert _make_adapter(_FakeSessionManager()).path == "/mcp/inst-1/search"

    def test_short_description_falls_back_to_tool_name_without_description(self) -> None:
        adapter = MCPToolAdapter(_server(), _tool_info(description=None), _FakeSessionManager())
        assert adapter.short_description == "search"

    def test_description_falls_back_to_display_name_and_tool_name(self) -> None:
        adapter = MCPToolAdapter(_server(), _tool_info(description=None), _FakeSessionManager())
        assert adapter.description == "Jira MCP: search"

    def test_parameters_extracted_from_input_schema(self) -> None:
        params = _make_adapter(_FakeSessionManager()).parameters
        assert any(p.name == "query" and p.required for p in params)

    def test_raw_input_schema_returns_the_mcp_schema_verbatim(self) -> None:
        adapter = _make_adapter(_FakeSessionManager())
        assert adapter.raw_input_schema == {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "search text"}},
            "required": ["query"],
        }

    def test_raw_input_schema_is_empty_object_when_tool_has_no_schema(self) -> None:
        """`MCPToolInfo.input_schema` defaults to `{}` (never `None`), which
        is what the schema-less path (`input_schema={}` narrowed at the
        frontend/API boundary, see `mcp_tool_loader.py`'s module docstring)
        actually produces."""
        adapter = MCPToolAdapter(_server(), _tool_info(input_schema={}), _FakeSessionManager())
        assert adapter.raw_input_schema == {}


class TestValidate:
    """Real validation against the MCP server's own schema (`raw_input_schema`
    via `parameters`), replacing the old `_PermissiveValidationMixin` no-op —
    see `MCPToolAdapter.validate`'s docstring for why it's still shallower
    than `Tool.validate()`'s default (unknown keys are allowed)."""

    def test_missing_required_argument_raises(self) -> None:
        adapter = _make_adapter(_FakeSessionManager())
        with pytest.raises(ToolValidationError, match="missing required argument 'query'"):
            adapter.validate({})

    def test_out_of_enum_value_raises_naming_allowed_values(self) -> None:
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
                "required": ["status"],
            }),
            _FakeSessionManager(),
        )
        with pytest.raises(ToolValidationError, match=r"must be one of .*open.*closed"):
            adapter.validate({"status": "archived"})

    def test_stringified_integer_is_coerced_not_rejected(self) -> None:
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": [],
            }),
            _FakeSessionManager(),
        )
        kwargs = {"limit": "5"}
        adapter.validate(kwargs)
        assert kwargs["limit"] == 5

    def test_boolean_does_not_leak_into_a_numeric_field(self) -> None:
        """`isinstance(True, int)` is `True` in Python, so a naive numeric
        check would silently accept a boolean where an integer/float was
        expected — must be rejected instead."""
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": [],
            }),
            _FakeSessionManager(),
        )
        with pytest.raises(ToolValidationError, match="expected type 'integer'"):
            adapter.validate({"limit": True})

    def test_unknown_extra_keys_are_allowed(self) -> None:
        """Deliberately more permissive than `Tool.validate()`'s default: an
        MCP `additionalProperties` schema can legitimately allow keys
        `parameters` doesn't know about, and a false local rejection would
        block a call that would have succeeded server-side."""
        adapter = _make_adapter(_FakeSessionManager())
        adapter.validate({"query": "x", "unexpected": 1})  # must not raise

    def test_none_value_for_optional_argument_is_allowed(self) -> None:
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            }),
            _FakeSessionManager(),
        )
        adapter.validate({"query": "x", "limit": None})  # must not raise


class TestValidationBlocksExecutionViaToolExecutor:
    """The point of real validation: a bad call must fail locally, in one
    turn, WITHOUT a network round trip to the MCP server — proven end to
    end through `ToolExecutor.call_tool`, the only path production code
    uses to reach `Tool.execute()`."""

    async def test_missing_required_argument_never_reaches_session_manager(self) -> None:
        session_manager = _FakeSessionManager()
        adapter = _make_adapter(session_manager)
        registry = ToolRegistry()
        registry.register_tool(adapter)
        executor = ToolExecutor(registry)

        result = await executor.call_tool(ToolCall(id="c1", name=adapter.name, arguments={}))

        assert result.is_error is True
        assert "missing required argument 'query'" in result.content
        assert session_manager.calls == []

    async def test_out_of_enum_value_never_reaches_session_manager(self) -> None:
        session_manager = _FakeSessionManager()
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
                "required": ["status"],
            }),
            session_manager,
        )
        registry = ToolRegistry()
        registry.register_tool(adapter)
        executor = ToolExecutor(registry)

        result = await executor.call_tool(
            ToolCall(id="c1", name=adapter.name, arguments={"status": "archived"})
        )

        assert result.is_error is True
        assert "must be one of" in result.content
        assert session_manager.calls == []


class TestExecuteSuccess:
    async def test_uses_data_field_when_present(self) -> None:
        result = _FakeCallResult(is_error=False, data={"issues": ["PA-1"]})
        adapter = _make_adapter(_FakeSessionManager(result=result))

        output = await adapter.execute(query="PA")

        assert output.success is True
        assert output.data == {"issues": ["PA-1"]}

    async def test_falls_back_to_content_text_blocks_when_no_data(self) -> None:
        result = _FakeCallResult(is_error=False, data=None, content=[_TextBlock("hello"), _TextBlock("world")])
        adapter = _make_adapter(_FakeSessionManager(result=result))

        output = await adapter.execute(query="PA")

        assert output.success is True
        assert output.data == "hello\nworld"

    async def test_passes_kwargs_through_to_session_manager(self) -> None:
        session_manager = _FakeSessionManager(result=_FakeCallResult(data="ok"))
        adapter = _make_adapter(session_manager)

        await adapter.execute(query="PA")

        assert session_manager.calls == [(_server(), "search", {"query": "PA"})]


class TestExecuteFailure:
    async def test_is_error_result_returns_failed_output(self) -> None:
        result = _FakeCallResult(is_error=True, data="tool exploded")
        adapter = _make_adapter(_FakeSessionManager(result=result))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert output.error == "tool exploded"

    async def test_connection_error_returns_failed_output(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=MCPConnectionError("connection refused")))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "connection refused" in output.error

    async def test_token_refresh_error_returns_failed_output(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=MCPTokenRefreshError("no refresh token")))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "no refresh token" in output.error

    async def test_oauth_error_returns_failed_output(self) -> None:
        adapter = _make_adapter(
            _FakeSessionManager(exc=MCPOAuthError("token endpoint rejected refresh")),
        )

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "token endpoint rejected refresh" in output.error

    async def test_unexpected_exception_returns_failed_output(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=ValueError("totally unexpected")))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "totally unexpected" in output.error
