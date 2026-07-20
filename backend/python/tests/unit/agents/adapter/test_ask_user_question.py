"""`ask_user_question_sse` (`app/agents/agent_loop/hooks/ask_user_question.py`)
— Phase 3/5's "ask_user_question needs SSE emission" special case: the
`internaltools.ask_user_question` tool's structured question payload must
reach the frontend as `{"event": "ask_user_question", "data": {status,
toolData}}` the moment the tool result is ready, gated on `has_ui_client`."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.ask_user_question import ask_user_question_sse
from tests.unit.agents.adapter.support.hook_helpers import run_post_tool

_TOOL_PATH = "/internal/internaltools/ask_user_question"


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


def _make_context(**overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    return context


class TestAskUserQuestionSSE:
    async def test_emits_sse_event_with_parsed_json_payload(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = ask_user_question_sse(context)
        payload = '{"question": "Which one?", "options": ["a", "b"]}'
        await run_post_tool(
            middleware, ToolOutput(success=True, data=payload), tool_path=_TOOL_PATH
        )

        assert len(sink.events) == 1
        assert sink.events[0] == {
            "event": "ask_user_question",
            "data": {
                "status": "success",
                "toolData": {"question": "Which one?", "options": ["a", "b"]},
            },
        }
        assert context.tool_state["ask_user_question_emitted"] is True

    async def test_non_json_payload_passed_through_as_raw_string(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = ask_user_question_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data="not json"), tool_path=_TOOL_PATH
        )

        assert sink.events[0]["data"]["toolData"] == "not json"

    async def test_error_output_still_emits_with_error_status(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = ask_user_question_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=False, error="boom"), tool_path=_TOOL_PATH
        )

        assert sink.events[0]["data"]["status"] == "error"
        assert sink.events[0]["data"]["toolData"] == "boom"

    async def test_no_emission_when_no_ui_client(self) -> None:
        context = _make_context(event_sink=_RecordingSink(), has_ui_client=False)

        middleware = ask_user_question_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data="{}"), tool_path=_TOOL_PATH
        )
        assert "ask_user_question_emitted" not in context.tool_state

    async def test_no_emission_for_unrelated_tool(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = ask_user_question_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data="ok"), tool_path="/connectors/jira/search"
        )
        assert sink.events == []

    async def test_no_event_sink_is_a_safe_noop(self) -> None:
        context = _make_context(event_sink=None, has_ui_client=True)

        middleware = ask_user_question_sse(context)
        # Should not raise even though there's nowhere to write the event.
        await run_post_tool(
            middleware, ToolOutput(success=True, data="{}"), tool_path=_TOOL_PATH
        )

    async def test_resolved_via_registry_when_scope_present(self) -> None:
        """When a real `ToolRegistry` is available (normal runtime, unlike
        the isolated-hook-helper default of `scope=None`), the tool name is
        resolved through it rather than falling back to path-splitting —
        exercising the other branch of `resolve_tool_name`."""
        from types import SimpleNamespace

        from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolParameter
        from app.agent_loop_lib.tools.registry import ToolRegistry

        class _AskUserQuestionTool(Tool):
            name = "internaltools.ask_user_question"
            short_description = "Ask the user a clarifying question"
            description = "Ask the user a clarifying question"
            path = _TOOL_PATH

            @property
            def parameters(self) -> list[ToolParameter]:
                return [ToolParameter(name="question", type=ParameterType.STRING, description="q", required=True)]

            async def execute(self, **kwargs):
                raise NotImplementedError

        registry = ToolRegistry()
        registry.register_tool(_AskUserQuestionTool())

        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)
        middleware = ask_user_question_sse(context)

        scope = SimpleNamespace(turn=SimpleNamespace(run=SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry))))
        await run_post_tool(
            middleware, ToolOutput(success=True, data="{}"), tool_path=_TOOL_PATH, scope=scope
        )
        assert len(sink.events) == 1
