"""`MCPToolAdapter` (`app/agents/agent_loop/mcp_tool_adapter.py`) — identity/
parameters derived from `MCPToolInfo`, and `execute()` result normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

    def test_validate_is_permissive(self) -> None:
        _make_adapter(_FakeSessionManager()).validate({"unexpected": 1})  # must not raise


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
