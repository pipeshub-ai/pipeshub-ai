"""`compute_extra_question_flags()` (`agent/tool_loop.py`, task engine plan
Part D2 "reject second terminal `ask_user_question` per turn"): a model
that calls a `TAG_LIFECYCLE_TERMINAL` + `TAG_UI_ONLY` tool (today, only
`ask_user_question`) twice in the SAME turn must only have the first call
actually execute — the second is rejected with a corrective tool result
instead of showing a second, unanswerable question card.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import compute_extra_question_flags
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.builtin.planning.task_complete import (
    TaskCompleteTool,
    TaskCompletionOutcome,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL, TAG_UI_ONLY
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _CountingAskUserQuestionTool(Tool):
    """A double for `ask_user_question`: `TAG_LIFECYCLE_TERMINAL` +
    `TAG_UI_ONLY`, the exact tag pair `compute_extra_question_flags()`
    dispatches on. Implements `extract_outcome` (satisfying the
    `TerminalTool` protocol) the same way the real `ask_user_question`'s
    `outcome_extractor` does, so a successful call actually stops the run."""

    def __init__(self) -> None:
        self.execution_count = 0

    @property
    def name(self) -> str:
        return "ask_user_question"

    @property
    def short_description(self) -> str:
        return "Ask the user a question"

    @property
    def description(self) -> str:
        return "Ask the user a question"

    @property
    def path(self) -> str:
        return "/toolsets/builtin/ask_user_question"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_LIFECYCLE_TERMINAL, TAG_UI_ONLY]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="question", type=ParameterType.STRING, description="question")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        self.execution_count += 1
        return ToolOutput(success=True, data={"question": kwargs.get("question")})

    def extract_outcome(
        self, tr: CoreToolResult, call: ToolCall, fallback_text: str
    ) -> TaskCompletionOutcome:
        return TaskCompletionOutcome(task_done=True, final_output=tr.content, needs_input="pending question")


def _build_agent(transport: ScriptedTransport, tool: Tool) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(tool)
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=5,
    )
    return Agent(spec, runtime)


class TestSecondAskUserQuestionInSameTurnIsRejected:
    async def test_only_first_call_actually_executes(self) -> None:
        tool = _CountingAskUserQuestionTool()
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="call-1", name="ask_user_question", arguments={"question": "Which channel?"}),
            ToolCall(id="call-2", name="ask_user_question", arguments={"question": "Which sprint?"}),
        ])

        agent = _build_agent(transport, tool)
        result = await agent.run(Goal(description="ask two things"))

        assert tool.execution_count == 1

        first_turn = result.turns[0]
        assert len(first_turn.tool_results) == 2
        rejected = [tr for tr in first_turn.tool_results if "only one question" in str(tr.content).lower()]
        executed = [tr for tr in first_turn.tool_results if tr.tool_call_id == "call-1"]
        assert len(rejected) == 1
        assert rejected[0].tool_call_id == "call-2"
        assert len(executed) == 1

    async def test_the_executed_calls_own_outcome_still_stops_the_run(self) -> None:
        """The turn must still end via the first (accepted) call's
        `needs_input`/terminal outcome — rejecting the second call must not
        also suppress the first one's own effect on the run."""
        tool = _CountingAskUserQuestionTool()
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="call-1", name="ask_user_question", arguments={"question": "Which channel?"}),
            ToolCall(id="call-2", name="ask_user_question", arguments={"question": "Which sprint?"}),
        ])

        agent = _build_agent(transport, tool)
        result = await agent.run(Goal(description="ask two things"))

        assert result.success is True


class TestComputeExtraQuestionFlagsPrePass:
    """Unit-level coverage of the synchronous pre-pass itself."""

    def _registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(_CountingAskUserQuestionTool())
        registry.register_tool(TaskCompleteTool())
        return registry

    def test_second_interactive_terminal_call_flagged(self) -> None:
        calls = [
            ToolCall(id="a", name="ask_user_question", arguments={"question": "x"}),
            ToolCall(id="b", name="ask_user_question", arguments={"question": "y"}),
        ]
        flags = compute_extra_question_flags(calls, self._registry())
        assert flags == {"a": False, "b": True}

    def test_third_and_later_calls_also_flagged(self) -> None:
        calls = [
            ToolCall(id="a", name="ask_user_question", arguments={"question": "x"}),
            ToolCall(id="b", name="ask_user_question", arguments={"question": "y"}),
            ToolCall(id="c", name="ask_user_question", arguments={"question": "z"}),
        ]
        flags = compute_extra_question_flags(calls, self._registry())
        assert flags == {"a": False, "b": True, "c": True}

    def test_task_complete_is_not_subject_to_the_guard(self) -> None:
        """`task_complete` is `TAG_LIFECYCLE_TERMINAL` but NOT `TAG_UI_ONLY`
        — the guard must dispatch on the tag PAIR, not just terminality."""
        calls = [
            ToolCall(id="a", name="ask_user_question", arguments={"question": "x"}),
            ToolCall(id="b", name="task_complete", arguments={"output": "done"}),
        ]
        flags = compute_extra_question_flags(calls, self._registry())
        assert flags == {"a": False, "b": False}

    def test_single_question_call_never_flagged(self) -> None:
        calls = [ToolCall(id="a", name="ask_user_question", arguments={"question": "x"})]
        flags = compute_extra_question_flags(calls, self._registry())
        assert flags == {"a": False}
