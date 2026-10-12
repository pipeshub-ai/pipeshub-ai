"""`MCPToolAdapter` (`app/agents/agent_loop/mcp_tool_adapter.py`) — identity/
parameters derived from `MCPToolInfo`, and `execute()` result normalization."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import mcp.types as mt
import pytest

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import ImagePart, ImageSource, TextPart
from app.agent_loop_lib.core.types import ToolCall, ToolResult
from app.agent_loop_lib.tools.errors import ToolValidationError
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError
from tests.unit.agents.adapter.conftest import make_context


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


def _call_result(
    *, is_error: bool = False, structured: dict[str, object] | None = None, texts: tuple[str, ...] = (),
) -> mt.CallToolResult:
    return mt.CallToolResult(
        content=[mt.TextContent(type="text", text=t) for t in texts],
        structuredContent=structured,
        isError=is_error,
    )


class _FakeSessionManager:
    def __init__(self, result: Any = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[tuple[Any, str, dict]] = []
        self.progress_handlers: list[Any] = []

    async def call(self, server: ResolvedMCPServer, tool_name: str, arguments: dict, *, on_progress: Any = None) -> Any:  # noqa: ANN401
        self.calls.append((server, tool_name, arguments))
        self.progress_handlers.append(on_progress)
        if self._exc is not None:
            raise self._exc
        return self._result


def _make_adapter(session_manager: _FakeSessionManager) -> MCPToolAdapter:
    return MCPToolAdapter(_server(), _tool_info(), session_manager)


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


class TestAMissingPermissionOffersTheSignInCard:
    REFUSED = "The Jira MCP server needs more permission for this (scopes: write). Ask the user to reconnect it."

    async def test_the_model_hears_of_the_button_and_the_card_lists_the_server(self) -> None:
        from app.agents.agent_loop.mcp_sign_in import BUTTON_HINT
        from app.agents.mcp.errors import MCPInsufficientScopeError

        context = make_context(client_name="pipeshub-ai", conversation_id="conv-1", protocol="agui")
        manager = _FakeSessionManager(exc=MCPInsufficientScopeError(self.REFUSED, scopes=["write"]))
        adapter = MCPToolAdapter(_server(), _tool_info(), manager, context=context)

        out = await adapter.execute(query="x")

        assert out.success is False
        assert out.error == f"{self.REFUSED} {BUTTON_HINT}"
        assert context.mcp_sign_in_needed == [
            {"instanceId": adapter.server.instance_id, "serverName": adapter.server.display_name, "scopes": ["write"]},
        ]

    async def test_where_no_card_shows_the_message_is_unchanged(self) -> None:
        from app.agents.mcp.errors import MCPInsufficientScopeError

        context = make_context()
        manager = _FakeSessionManager(exc=MCPInsufficientScopeError(self.REFUSED, scopes=["write"]))

        out = await MCPToolAdapter(_server(), _tool_info(), manager, context=context).execute(query="x")

        assert out.error == self.REFUSED
        assert context.mcp_sign_in_needed == []

    async def test_without_a_context_too(self) -> None:
        from app.agents.mcp.errors import MCPInsufficientScopeError

        out = await _make_adapter(_FakeSessionManager(exc=MCPInsufficientScopeError(self.REFUSED, scopes=["write"]))).execute(query="x")
        assert out.error == self.REFUSED


class TestTheServersProgressIsShown:
    @staticmethod
    def _adapter(sink: _Sink | None) -> tuple[MCPToolAdapter, _FakeSessionManager]:
        manager = _FakeSessionManager(result=_call_result(texts=("ok",)))
        context = make_context(event_sink=sink, protocol="agui", run_id="run-1")
        return MCPToolAdapter(_server(), _tool_info(), manager, context=context), manager

    async def test_reports_reach_the_activity_row_at_most_twice_a_second(self) -> None:
        sink = _Sink()
        adapter, manager = self._adapter(sink)
        await adapter.execute(query="x")
        (report,) = manager.progress_handlers

        with patch("app.agents.agent_loop.mcp_tool_adapter.time.monotonic", side_effect=[100.0, 100.2, 100.6]):
            await report(1.0, 4.0, "Reading\n  page 1")
            await report(2.0, 4.0, None)
            await report(3.0, 4.0, None)

        snapshots = [e["data"]["snapshot"] for e in sink.events]
        assert snapshots == [
            {"status": "running_tool", "current_tool": adapter.name, "progress": 1.0, "total": 4.0, "progress_message": "Reading page 1"},
            {"status": "running_tool", "current_tool": adapter.name, "progress": 3.0, "total": 4.0},
        ]
        assert all(e["event"] == "STATE_SNAPSHOT" and e["data"]["runId"] == "run-1" for e in sink.events)

    async def test_a_long_message_is_cut(self) -> None:
        sink = _Sink()
        adapter, manager = self._adapter(sink)
        await adapter.execute(query="x")

        await manager.progress_handlers[0](1.0, None, "x" * 1000)

        assert len(sink.events[0]["data"]["snapshot"]["progress_message"]) == 200
        assert "total" not in sink.events[0]["data"]["snapshot"]

    async def test_without_a_chat_to_show_it_nothing_listens(self) -> None:
        adapter, manager = self._adapter(None)
        await adapter.execute(query="x")
        assert manager.progress_handlers == [None]

    async def test_a_call_past_the_limit_says_so(self) -> None:
        from app.agents.mcp.client import MCPCallTooLongError

        adapter = _make_adapter(_FakeSessionManager(exc=MCPCallTooLongError("too long", limit_seconds=1800)))
        out = await adapter.execute(query="x")

        assert out.success is False
        assert "still working after 1800 seconds, the limit for one call" in out.error


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

    def test_raw_input_schema_is_a_well_formed_object_when_tool_has_no_schema(self) -> None:
        """`MCPToolInfo.input_schema` defaults to `{}` (never `None`) and
        `discovery.py` writes `input_schema or {}`, so a server that omits
        `inputSchema` for a zero-argument tool lands here. It must NOT stay
        `{}`: `AnthropicTransport._format_tools` forwards `input_schema`
        verbatim and the API rejects an empty schema, which fails every tool
        in the request rather than just this one."""
        adapter = MCPToolAdapter(_server(), _tool_info(input_schema={}), _FakeSessionManager())
        assert adapter.raw_input_schema == {"type": "object", "properties": {}}


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

    def test_integral_float_is_coerced_for_an_integer_parameter(self) -> None:
        """JSON has one number type, so a model emitting `5.0` for an integer
        parameter means 5 — `isinstance(5.0, int)` being False is a Python
        detail, not a schema violation."""
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": [],
            }),
            _FakeSessionManager(),
        )
        kwargs = {"limit": 5.0}
        adapter.validate(kwargs)
        assert kwargs["limit"] == 5
        assert isinstance(kwargs["limit"], int)

    def test_fractional_float_is_still_rejected_for_an_integer_parameter(self) -> None:
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
            adapter.validate({"limit": 2.5})

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


class TestValidateSkipsNonAuthoritativeTypes:
    """`parameters` is a lossy view of the schema: `_tool_parameter_from_json_schema`
    reports STRING for a property with no declared `type`, and `_unwrap_any_of`
    collapses a union to its first non-null arm. Type-checking against that
    view rejects calls the real schema permits, and since `ToolExecutor`'s
    `_usage_hint` is built from the same `parameters`, the correction handed
    back repeats the wrong type — the model can't recover, and three such
    turns let `ToolErrorTracker` block the tool for the rest of the request.
    So the type check only runs where the schema declares exactly one
    concrete type."""

    def test_untyped_property_accepts_a_non_string_value(self) -> None:
        """A property with no `type` accepts any JSON value. `parameters`
        reports it as STRING, so an object here used to be a hard reject."""
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"payload": {"description": "anything"}},
                "required": [],
            }),
            _FakeSessionManager(),
        )
        adapter.validate({"payload": {"nested": True}})  # must not raise

    def test_multi_arm_union_accepts_either_arm(self) -> None:
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {
                    "fields": {"anyOf": [{"type": "string"}, {"type": "array"}]},
                },
                "required": [],
            }),
            _FakeSessionManager(),
        )
        adapter.validate({"fields": ["summary", "status"]})  # must not raise
        adapter.validate({"fields": "summary"})  # must not raise

    def test_one_of_union_accepts_either_arm(self) -> None:
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"mode": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": [],
            }),
            _FakeSessionManager(),
        )
        adapter.validate({"mode": 3})  # must not raise

    def test_nullable_single_type_is_still_enforced(self) -> None:
        """`["string", "null"]` normalizes to a nullable string
        (`_normalized_schema_keywords`), which IS one concrete type — so the
        check still applies, and an explicit null is still accepted."""
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"assignee": {"type": ["string", "null"]}},
                "required": [],
            }),
            _FakeSessionManager(),
        )
        adapter.validate({"assignee": None})  # must not raise
        with pytest.raises(ToolValidationError, match="expected type 'string'"):
            adapter.validate({"assignee": {"id": 1}})

    def test_enum_is_enforced_even_on_an_untyped_property(self) -> None:
        """Skipping the TYPE check must not skip the enum check — an `enum`
        is authoritative regardless of whether `type` is declared."""
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"status": {"enum": ["open", "closed"]}},
                "required": ["status"],
            }),
            _FakeSessionManager(),
        )
        with pytest.raises(ToolValidationError, match=r"must be one of .*open.*closed"):
            adapter.validate({"status": "archived"})

    def test_required_is_enforced_even_on_an_untyped_property(self) -> None:
        adapter = MCPToolAdapter(
            _server(),
            _tool_info(input_schema={
                "type": "object",
                "properties": {"payload": {"description": "anything"}},
                "required": ["payload"],
            }),
            _FakeSessionManager(),
        )
        with pytest.raises(ToolValidationError, match="missing required argument 'payload'"):
            adapter.validate({})


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
    async def test_returns_structured_content_verbatim(self) -> None:
        result = _call_result(structured={"issues": ["PA-1"], "_links": {"self": "x"}})
        adapter = _make_adapter(_FakeSessionManager(result=result))

        output = await adapter.execute(query="PA")

        assert output.success is True
        assert output.data == {"issues": ["PA-1"], "_links": {"self": "x"}}

    async def test_falls_back_to_content_text_blocks_without_structured_content(self) -> None:
        result = _call_result(texts=("hello", "world"))
        adapter = _make_adapter(_FakeSessionManager(result=result))

        output = await adapter.execute(query="PA")

        assert output.success is True
        assert output.data == "hello\nworld"

    async def test_passes_kwargs_through_to_session_manager(self) -> None:
        session_manager = _FakeSessionManager(result=_call_result(texts=("ok",)))
        adapter = _make_adapter(session_manager)

        await adapter.execute(query="PA")

        assert session_manager.calls == [(_server(), "search", {"query": "PA"})]

    async def test_image_results_use_the_adapter_context(self) -> None:
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        result = mt.CallToolResult(content=[mt.ImageContent(type="image", data=png, mimeType="image/png")])
        context = SimpleNamespace(is_multimodal_llm=True, tool_state={})
        adapter = MCPToolAdapter(_server(), _tool_info(), _FakeSessionManager(result=result), context=context)

        output = await adapter.execute(query="PA")

        assert [type(part) for part in output.data] == [ImagePart]


class TestAFailedCallIsLoggedSafely:
    """SEC-11: an expected failure logs one line, URLs without their query; a bug keeps its stack."""

    async def _failure_record(self, exc: Exception, caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
        adapter = _make_adapter(_FakeSessionManager(exc=exc))
        with caplog.at_level(logging.WARNING, logger="app.agents.agent_loop.mcp_tool_adapter"):
            await adapter.execute(query="PA")
        (record,) = [r for r in caplog.records if "MCP tool" in r.getMessage()]
        return record

    async def test_an_http_error_logs_no_stack_and_no_api_key(self, caplog: pytest.LogCaptureFixture) -> None:
        request = httpx.Request("POST", "https://mcp.example.com/mcp?api_key=secret")
        error = httpx.HTTPStatusError(
            "Server error for url 'https://mcp.example.com/mcp?api_key=secret'",
            request=request, response=httpx.Response(500, request=request),
        )

        record = await self._failure_record(error, caplog)

        assert not record.exc_info
        assert "secret" not in record.getMessage()
        assert "https://mcp.example.com/mcp" in record.getMessage()
        assert "(unreachable)" in record.getMessage()

    async def test_a_timeout_logs_no_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        record = await self._failure_record(asyncio.TimeoutError(), caplog)

        assert not record.exc_info
        assert "(timeout)" in record.getMessage()

    async def test_an_expired_sign_in_logs_no_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        record = await self._failure_record(MCPTokenRefreshError("no refresh token"), caplog)

        assert not record.exc_info

    async def test_an_unexpected_error_keeps_its_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        record = await self._failure_record(KeyError("structuredContent"), caplog)

        assert record.exc_info
        assert "(error)" in record.getMessage()


class TestExecuteFailure:
    async def test_is_error_result_returns_failed_output(self) -> None:
        result = _call_result(is_error=True, texts=("tool exploded",))
        adapter = _make_adapter(_FakeSessionManager(result=result))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert output.error == "tool exploded"

    async def test_connection_error_returns_failed_output(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=MCPConnectionError("connection refused")))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "connection refused" in output.error

    async def test_token_refresh_error_asks_to_reconnect(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=MCPTokenRefreshError("no refresh token at /services/mcp/credentials/x")))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "Reconnect it in Workspace" in output.error
        assert "/services/mcp" not in output.error

    async def test_oauth_error_never_shows_the_token_endpoint_body(self) -> None:
        adapter = _make_adapter(
            _FakeSessionManager(exc=MCPOAuthError('OAuth token request failed (400): {"error":"invalid_client","secret":"s3"}')),
        )

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "invalid_client" not in output.error
        assert "Jira MCP" in output.error

    async def test_http_error_hides_the_request_url(self) -> None:
        request = httpx.Request("POST", "https://mcp.example.com/mcp?api_key=secret")
        error = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request))
        adapter = _make_adapter(_FakeSessionManager(exc=error))

        output = await adapter.execute(query="PA")

        assert output.error == "The Jira MCP MCP server returned HTTP 500."

    async def test_http_error_inside_a_task_group_reads_as_its_status(self) -> None:
        request = httpx.Request("POST", "https://mcp.example.com/mcp")
        error = httpx.HTTPStatusError("busy", request=request, response=httpx.Response(503, request=request))
        adapter = _make_adapter(_FakeSessionManager(exc=ExceptionGroup("unhandled errors in a TaskGroup", [error])))

        output = await adapter.execute(query="PA")

        assert output.error == "The Jira MCP MCP server returned HTTP 503."

    async def test_http_401_says_the_credentials_were_rejected(self) -> None:
        request = httpx.Request("POST", "https://mcp.example.com/mcp")
        error = httpx.HTTPStatusError("no", request=request, response=httpx.Response(401, request=request))
        adapter = _make_adapter(_FakeSessionManager(exc=error))

        output = await adapter.execute(query="PA")

        assert "rejected the stored credentials" in output.error

    async def test_timeout_has_a_readable_message(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=asyncio.TimeoutError()))

        output = await adapter.execute(query="PA")

        assert "did not respond within 60 seconds" in output.error

    async def test_unexpected_error_text_has_urls_redacted(self) -> None:
        adapter = _make_adapter(
            _FakeSessionManager(exc=RuntimeError("fetch https://user:pw@api.example.com/v1?token=abc failed\ntrace")),
        )

        output = await adapter.execute(query="PA")

        assert output.error.startswith("The MCP tool call failed (RuntimeError): fetch https://api.example.com/v1")
        assert "token=abc" not in output.error
        assert "user:pw" not in output.error
        assert "trace" not in output.error

    async def test_unexpected_exception_returns_failed_output(self) -> None:
        adapter = _make_adapter(_FakeSessionManager(exc=ValueError("totally unexpected")))

        output = await adapter.execute(query="PA")

        assert output.success is False
        assert "totally unexpected" in output.error


def _tool_result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="c1", name="mcp_jira_mcp_search", content=content, is_error=is_error)


class TestDisplay:
    def test_display_name_names_the_server_and_the_raw_tool(self) -> None:
        adapter = _make_adapter(_FakeSessionManager())

        assert adapter.display_name == "Jira MCP · search"

    def test_summarize_args_lists_the_arguments(self) -> None:
        adapter = _make_adapter(_FakeSessionManager())

        assert adapter.summarize_args({"query": "PA", "limit": 5}) == 'query: "PA", limit: 5'

    def test_summarize_args_is_none_without_arguments(self) -> None:
        assert _make_adapter(_FakeSessionManager()).summarize_args({}) is None


class TestSummarizeResult:
    def _summary(self, content: object, *, is_error: bool = False) -> str | None:
        return _make_adapter(_FakeSessionManager()).summarize_result({}, _tool_result(content, is_error=is_error))

    def test_dict_with_a_list_counts_its_items(self) -> None:
        assert self._summary({"total": 2, "events": [{}, {}]}) == "Found 2 events"

    def test_single_item_is_singular(self) -> None:
        assert self._summary({"events": [{}]}) == "Found 1 event"

    def test_one_record_is_named(self) -> None:
        assert self._summary({"id": 1, "name": "x"}) == "x"
        assert self._summary({"id": 1, "size": 5}) == "Returned 2 fields"

    def test_list_counts_items(self) -> None:
        assert self._summary([1, 2, 3]) == "Found 3 results"

    def test_json_text_is_read_not_quoted(self) -> None:
        text = '{"issues": [{"key": "PA-1", "fields": {"summary": "Login fails"}}], "isLast": true}'
        assert self._summary(text) == "Found 1 issue"

    def test_text_uses_its_first_non_empty_line(self) -> None:
        assert self._summary("\n  First line\nsecond") == "First line"

    def test_empty_output(self) -> None:
        assert self._summary("") == "No output"

    def test_error_uses_the_first_line_only(self) -> None:
        assert self._summary("rate limited\ntrace...", is_error=True) == "Failed: rate limited"

    def test_image_parts_are_counted(self) -> None:
        content = [TextPart(text="Chart of Q3"), ImagePart(source=ImageSource(type="base64", data="AAAA"))]

        assert self._summary(content) == "Returned 1 image: Chart of Q3"


class TestResultView:
    def test_a_search_is_a_table_and_is_parsed_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.agent_loop import mcp_tool_adapter

        calls: list[object] = []
        real = mcp_tool_adapter.describe
        monkeypatch.setattr(mcp_tool_adapter, "describe", lambda content: calls.append(content) or real(content))
        adapter = _make_adapter(_FakeSessionManager())
        result = _tool_result('{"issues": [{"key": "PA-1", "fields": {"summary": "Login fails", "status": {"name": "Done"}}}]}')

        assert adapter.summarize_result({}, result) == "Found 1 issue"
        view = adapter.result_view({}, result)
        assert view is not None and view["columns"] == ["Key", "Summary", "Status"]
        assert len(calls) == 1

    @pytest.mark.parametrize("content,is_error", [
        ("plain words", False),
        ('{"issues": []}', True),
        ([TextPart(text="a"), ImagePart(source=ImageSource(type="base64", data="AAAA"))], False),
    ])
    def test_no_view_for_text_errors_or_images(self, content: object, is_error: bool) -> None:
        adapter = _make_adapter(_FakeSessionManager())
        assert adapter.result_view({}, _tool_result(content, is_error=is_error)) is None


class _HangingSessionManager:
    """A server that never answers; records whether the call was abandoned."""

    def __init__(self) -> None:
        self.cancelled = False
        self.started = asyncio.Event()

    async def call(self, server: ResolvedMCPServer, tool_name: str, arguments: dict, **_kwargs: Any) -> Any:  # noqa: ANN401
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _context_with_token() -> tuple[Any, CancellationToken]:
    token = CancellationToken()
    return make_context(cancellation_token=token), token


class TestStopInterruptsACall:
    async def test_stop_ends_a_call_that_is_still_waiting(self) -> None:
        session_manager = _HangingSessionManager()
        context, token = _context_with_token()
        adapter = MCPToolAdapter(_server(), _tool_info(), session_manager, context=context)

        execution = asyncio.create_task(adapter.execute(query="x"))
        await session_manager.started.wait()
        token.cancel()
        output = await asyncio.wait_for(execution, timeout=2)

        assert output.success is False
        assert "Stopped before the Jira MCP MCP server answered" in output.error
        assert session_manager.cancelled is True

    async def test_without_a_stop_the_result_comes_back(self) -> None:
        context, _ = _context_with_token()
        adapter = MCPToolAdapter(_server(), _tool_info(), _FakeSessionManager(result=_call_result(texts=("found 3",))), context=context)

        output = await adapter.execute(query="x")

        assert output.success is True

    async def test_a_failure_still_reads_as_a_failure(self) -> None:
        context, _ = _context_with_token()
        adapter = MCPToolAdapter(_server(), _tool_info(), _FakeSessionManager(exc=MCPConnectionError("Connection refused")), context=context)

        output = await adapter.execute(query="x")

        assert output.success is False
        assert "Connection refused" in output.error

    async def test_cancelling_the_turn_cancels_the_call(self) -> None:
        session_manager = _HangingSessionManager()
        context, _ = _context_with_token()
        adapter = MCPToolAdapter(_server(), _tool_info(), session_manager, context=context)

        execution = asyncio.create_task(adapter.execute(query="x"))
        await session_manager.started.wait()
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        await asyncio.sleep(0)

        assert session_manager.cancelled is True
