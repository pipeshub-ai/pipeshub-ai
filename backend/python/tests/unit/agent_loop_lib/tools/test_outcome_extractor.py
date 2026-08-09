"""`@tool(outcome_extractor=...)` (task engine plan Part D, Phase 5) --
lets a `@tool`-defined `TAG_LIFECYCLE_TERMINAL` tool other than the
library's own `TaskCompleteTool` (which implements `extract_outcome`
directly) customize its `TaskCompletionOutcome`, e.g. to populate
`needs_input`. Covers both `FunctionTool` (standalone function) and
`BoundMethodTool` (class method via `ToolsetBuilder`) modes."""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolCall, ToolResult
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.builtin.planning.task_complete import (
    TaskCompletionOutcome,
)
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.toolset import ToolsetBuilder


def _custom_extractor(tr: ToolResult, call: ToolCall, fallback_text: str) -> TaskCompletionOutcome:
    return TaskCompletionOutcome(task_done=True, final_output=fallback_text, needs_input="custom needs_input")


class TestFunctionToolOutcomeExtractor:
    def test_no_extractor_uses_default(self) -> None:
        @tool(path="/toolsets/util/echo", short_description="Echo", description="Echo", parameters=[])
        async def echo(text: str) -> ToolOutput:
            return ToolOutput(success=True, data=text)

        outcome = echo.extract_outcome(
            ToolResult(tool_call_id="c1", name="echo", content="ok"),
            ToolCall(id="c1", name="echo", arguments={}),
            "fallback",
        )
        assert outcome.needs_input is None
        assert outcome.final_output == "fallback"

    def test_custom_extractor_is_used(self) -> None:
        @tool(
            path="/toolsets/util/ask",
            short_description="Ask",
            description="Ask",
            parameters=[],
            outcome_extractor=_custom_extractor,
        )
        async def ask(text: str) -> ToolOutput:
            return ToolOutput(success=True, data=text)

        outcome = ask.extract_outcome(
            ToolResult(tool_call_id="c1", name="ask", content="ok"),
            ToolCall(id="c1", name="ask", arguments={}),
            "fallback",
        )
        assert outcome.needs_input == "custom needs_input"


class _WithCustomOutcome:
    @tool(
        path="/tools/customish/ask",
        short_description="Ask",
        description="Ask",
        parameters=[],
        outcome_extractor=_custom_extractor,
    )
    async def ask(self, text: str = "") -> tuple[bool, str]:
        return True, "ok"


class TestBoundMethodToolOutcomeExtractor:
    def test_custom_extractor_is_used(self) -> None:
        toolset = ToolsetBuilder(
            _WithCustomOutcome(), name="customish",
            description="Fake toolset for tests", path_prefix="/tools/customish",
        )
        ask_tool = next(t for t in toolset.tools if t.name.endswith("ask"))

        outcome = ask_tool.extract_outcome(
            ToolResult(tool_call_id="c1", name=ask_tool.name, content="ok"),
            ToolCall(id="c1", name=ask_tool.name, arguments={}),
            "fallback",
        )
        assert outcome.needs_input == "custom needs_input"
