"""`PipesHubToolAdapter`/`PipesHubStructuredToolAdapter`
(`app/agents/agent_loop/tool_adapter.py`) — registry tools and dynamic
LangChain `StructuredTool`s execute correctly through agent-loop's `Tool`
ABC, with success/failure verdicts matching the legacy extraction logic."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    PipesHubToolAdapter,
    _to_tool_output,
    split_original_tool_name,
)
from app.agents.tools.enums import ParameterType as RegistryParameterType
from app.agents.tools.models import Tool as RegistryTool
from app.agents.tools.models import ToolParameter


def _plain_function(**kwargs: Any) -> str:
    """A plain top-level function so RegistryToolWrapper treats it as a
    standalone function, not a class method (`__qualname__` has no dot)."""
    if kwargs.get("boom"):
        raise RuntimeError("kaboom")
    return f"searched for {kwargs.get('query', '')}"


class _SearchArgs(BaseModel):
    query: str


def _make_registry_tool(**overrides: Any) -> RegistryTool:
    defaults: dict[str, Any] = {
        "app_name": "search",
        "tool_name": "run_search",
        "description": "Search things",
        "function": _plain_function,
        "parameters": [
            ToolParameter(name="query", type=RegistryParameterType.STRING, description="query text", required=True),
        ],
        "args_schema": _SearchArgs,
        "llm_description": "Use this to search for things.",
        "when_to_use": ["when the user asks to find something"],
        "when_not_to_use": ["when the user wants to create something"],
    }
    defaults.update(overrides)
    return RegistryTool(**defaults)


def _make_context() -> Any:
    from unittest.mock import MagicMock

    from app.agents.agent_loop.context import AgentContext

    return AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="u@example.com",
        logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
    )


class TestPipesHubToolAdapter:
    def test_name_joins_app_and_tool(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        assert adapter.name == "search_run_search"
        assert adapter.path == "/connectors/search/run_search"

    def test_description_includes_when_to_use_guidance(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        assert "Use this to search for things." in adapter.description
        assert "When to use:" in adapter.description
        assert "When NOT to use:" in adapter.description

    def test_description_falls_back_to_short_description(self) -> None:
        context = _make_context()
        tool = _make_registry_tool(llm_description=None, when_to_use=[], when_not_to_use=[])
        adapter = PipesHubToolAdapter(tool, "search", "run_search", lambda: context)
        assert adapter.description == adapter.short_description

    def test_parameters_extracted_from_registry_tool(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        params = adapter.parameters
        assert any(p.name == "query" for p in params)

    async def test_execute_success(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        output = await adapter.execute(query="cats")
        assert output.success is True
        assert "cats" in output.data

    async def test_execute_failure_returns_error_output(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        output = await adapter.execute(boom=True)
        assert output.success is False
        assert output.error

    def test_validate_is_permissive(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        # Should not raise even with unexpected/loosely-typed kwargs.
        adapter.validate({"unexpected_key": 123, "query": 5})


class TestPipesHubToolAdapterToolUnavailableSSE:
    """`error_type: "toolset_auth"` (`RegistryToolWrapper._format_error`'s
    additive marker) makes `execute()` emit a `tool_unavailable` SSE event
    with `reason="not_authenticated"` through `context.event_sink`, on top
    of the normal failed `ToolOutput` — see `_is_toolset_auth_error`/
    `_emit_tool_unavailable` in `tool_adapter.py`."""

    async def test_toolset_auth_error_marker_emits_sse_event(self, monkeypatch) -> None:
        import json

        from app.agents.agent_loop import tool_adapter as tool_adapter_module

        context = _make_context()
        sink_calls: list[dict] = []

        class _RecordingEventSink:
            async def write(self, event: dict) -> None:
                sink_calls.append(event)

        context.event_sink = _RecordingEventSink()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "jira", "create_issue", lambda: context)

        async def _fake_arun(self, kwargs: dict) -> str:
            return json.dumps({
                "status": "error",
                "message": "Error executing tool jira.create_issue: reconnect required",
                "tool": "jira.create_issue",
                "args": kwargs,
                "error_type": "toolset_auth",
                "error_title": "Jira reconnect required",
            })

        monkeypatch.setattr(tool_adapter_module.RegistryToolWrapper, "arun", _fake_arun)

        output = await adapter.execute(query="x")

        assert output.success is False
        assert len(sink_calls) == 1
        assert sink_calls[0] == {
            "event": "tool_unavailable",
            "data": {
                "tool": "jira_create_issue", "toolset": "jira",
                "reason": "not_authenticated", "message": output.error,
            },
        }

    async def test_generic_failure_does_not_emit_sse_event(self, monkeypatch) -> None:
        from app.agents.agent_loop import tool_adapter as tool_adapter_module

        context = _make_context()
        sink_calls: list[dict] = []

        class _RecordingEventSink:
            async def write(self, event: dict) -> None:
                sink_calls.append(event)

        context.event_sink = _RecordingEventSink()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)

        async def _fake_arun(self, kwargs: dict) -> str:
            return '{"status": "error", "message": "boom", "tool": "search.run_search", "args": {}}'

        monkeypatch.setattr(tool_adapter_module.RegistryToolWrapper, "arun", _fake_arun)

        output = await adapter.execute(query="x")

        assert output.success is False
        assert sink_calls == []

    async def test_no_event_sink_is_a_no_op(self, monkeypatch) -> None:
        """`context.event_sink is None` (background/test runs) must not
        raise — see `_emit_tool_unavailable`'s early return."""
        from app.agents.agent_loop import tool_adapter as tool_adapter_module

        context = _make_context()
        assert context.event_sink is None
        adapter = PipesHubToolAdapter(_make_registry_tool(), "jira", "create_issue", lambda: context)

        async def _fake_arun(self, kwargs: dict) -> str:
            return (
                '{"status": "error", "message": "reconnect", "tool": "jira.create_issue", '
                '"args": {}, "error_type": "toolset_auth"}'
            )

        monkeypatch.setattr(tool_adapter_module.RegistryToolWrapper, "arun", _fake_arun)

        output = await adapter.execute(query="x")  # must not raise
        assert output.success is False


class _EchoArgs(BaseModel):
    text: str = ""


async def _echo_coroutine(**kwargs: Any) -> str:
    if kwargs.get("fail"):
        raise ValueError("dynamic tool failure")
    return f"echo: {kwargs.get('text', '')}"


def _make_structured_tool() -> StructuredTool:
    return StructuredTool.from_function(
        name="web_search",
        description="Search the web",
        args_schema=_EchoArgs,
        coroutine=_echo_coroutine,
    )


class TestPipesHubStructuredToolAdapter:
    def test_name_and_path(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        assert adapter.name == "dynamic_web_search"
        assert adapter.path == "/dynamic/dynamic/web_search"

    async def test_execute_success_calls_coroutine(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        output = await adapter.execute(text="hello")
        assert output.success is True
        assert "hello" in output.data

    async def test_execute_failure_wraps_exception(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        output = await adapter.execute(fail=True)
        assert output.success is False
        assert "dynamic tool failure" in output.error


class TestToTooOutputRetrievalFastPath:
    """`_to_tool_output()`'s `<record>` fast path — retrieved document
    content routinely contains words like "error"/"failed"/"traceback"
    (bug reports, incident postmortems, ...), which the generic
    `ToolResultExtractor` substring heuristic would otherwise
    false-positive on, misclassifying a successful search as a failure."""

    def test_record_content_with_error_like_words_is_success(self) -> None:
        payload = (
            "Retrieved 3 knowledge blocks from 2 documents.\n\n<record>\n"
            "Record ID : abc-123\n\nRecord blocks (sorted):\n\n"
            "* Block Content: The deployment failed with a traceback showing "
            "an error in the retry handler.\n"
        )
        output = _to_tool_output(payload)
        assert output.success is True
        assert output.data == payload
        assert output.error is None

    def test_record_content_without_error_words_is_still_success(self) -> None:
        payload = "Retrieved 1 knowledge blocks from 1 documents.\n\n<record>\nAll good here.\n"
        output = _to_tool_output(payload)
        assert output.success is True
        assert output.data == payload

    def test_non_record_string_with_error_words_still_fails(self) -> None:
        """No `<record>` marker — falls through to the normal heuristic, so
        genuine tool errors are still classified as failures."""
        output = _to_tool_output("Error: something failed with a traceback")
        assert output.success is False

    def test_plain_success_string_without_record_marker_unaffected(self) -> None:
        output = _to_tool_output("searched for cats")
        assert output.success is True
        assert output.data == "searched for cats"


class TestSplitOriginalToolName:
    def test_dotted_name_splits_into_app_and_tool(self) -> None:
        tool = _make_structured_tool()
        tool._original_name = "slack.fetch_slack_thread"
        app_name, tool_name = split_original_tool_name(tool)
        assert app_name == "slack"
        assert tool_name == "fetch_slack_thread"

    def test_no_dot_falls_back_to_dynamic_bucket(self) -> None:
        tool = _make_structured_tool()
        app_name, tool_name = split_original_tool_name(tool)
        assert app_name == "dynamic"
        assert tool_name == "web_search"
