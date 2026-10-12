"""A call PreToolUse leaves for a person to approve on a later turn (`ToolCallContext.ask_later`):
it isn't run, the turn ends with the pending message, and the call is reported as awaiting
approval rather than blocked."""
from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventType, ToolCallStatus
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PendingApproval, PreDecision
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _CreateIssue(Tool):
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "create_issue"

    @property
    def short_description(self) -> str:
        return "Creates an issue"

    @property
    def description(self) -> str:
        return "Creates an issue"

    @property
    def path(self) -> str:
        return "/mcp/inst-1/create_issue"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="title", type=ParameterType.STRING, description="title")]

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        self.runs.append(kwargs)
        return ToolOutput(success=True, data="created")


def _ask_later_kernel(*, end_turn: bool = True, deny_first: bool = False) -> HookRegistry:
    kernel = HookRegistry()

    async def _gate(ctx: ToolCallContext, next_fn: Any) -> None:  # noqa: ANN401
        if ctx.tool_path.endswith("/create_issue"):
            if deny_first:
                ctx.deny("blocked by policy")
            ctx.ask_later(PendingApproval(
                message="Waiting for your approval to run create_issue.", end_turn=end_turn,
                details={"approvalId": "ap-1"},
            ))
        await next_fn()

    kernel.on(HookEvent.PRE_TOOL_USE).use(_gate)
    return kernel


class _Events:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


def _agent(transport: ScriptedTransport, tool: _CreateIssue, kernel: HookRegistry, events: _Events) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    registry.register_tool(tool)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry, hooks=kernel, event_emitter=events)  # type: ignore[arg-type]
    spec = AgentSpec(
        name="agent-under-test", system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"), max_turns=5,
    )
    return Agent(spec, runtime)


class TestTheExecutor:
    async def test_the_call_isnt_run_and_on_pending_gets_it(self) -> None:
        tool = _CreateIssue()
        registry = ToolRegistry()
        registry.register_tool(tool)
        seen: list[tuple[str, PendingApproval]] = []

        async def _on_pending(call: ToolCall, pending: PendingApproval) -> None:
            seen.append((call.name, pending))

        result = await ToolExecutor(registry, _ask_later_kernel()).call_tool(
            ToolCall(id="c1", name="create_issue", arguments={"title": "x"}), on_pending=_on_pending,
        )

        assert tool.runs == []
        assert result.is_error is True
        assert result.content == "Waiting for your approval to run create_issue."
        assert [name for name, _ in seen] == ["create_issue"]

    async def test_without_on_pending_it_is_an_ordinary_ask(self) -> None:
        tool = _CreateIssue()
        registry = ToolRegistry()
        registry.register_tool(tool)
        asked: list[str] = []

        async def _on_ask(call: ToolCall, reason: str) -> bool:
            asked.append(reason)
            return True

        result = await ToolExecutor(registry, _ask_later_kernel()).call_tool(
            ToolCall(id="c1", name="create_issue", arguments={"title": "x"}), on_ask=_on_ask,
        )

        assert asked == ["Waiting for your approval to run create_issue."]
        assert result.is_error is False
        assert tool.runs == [{"title": "x"}]

    def test_a_denied_call_doesnt_become_pending(self) -> None:
        ctx = ToolCallContext(tool_path="/mcp/inst-1/create_issue", tool_input={})
        ctx.deny("blocked by policy")
        ctx.ask_later(PendingApproval(message="ask"))

        assert ctx.decision == PreDecision.DENY
        assert ctx.pending_approval is None


class TestTheTurn:
    async def test_the_turn_ends_with_the_pending_message_and_the_call_awaits_approval(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="call-1", name="create_issue", arguments={"title": "Bug"}))
        transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "never reached"}))
        tool, events = _CreateIssue(), _Events()

        result = await _agent(transport, tool, _ask_later_kernel(), events).run(Goal(description="file a bug"))

        assert tool.runs == []
        assert len(transport.calls) == 1, "the turn ended; the model wasn't asked again"
        assert result.output == "Waiting for your approval to run create_issue."
        blocked = [e.payload for e in events.events if e.event_type == EventType.TOOL_BLOCKED]
        assert blocked == [{
            "tool": "create_issue", "reason": "Waiting for your approval to run create_issue.",
            "tool_call_id": "call-1", "status": ToolCallStatus.AWAITING_APPROVAL, "approval": {"approvalId": "ap-1"},
        }]

    async def test_a_pending_call_that_doesnt_end_the_turn_lets_the_model_go_on(self) -> None:
        """A child run's call: the card is shown, the child carries on without it."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="call-1", name="create_issue", arguments={"title": "Bug"}))
        transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "told the user"}))
        tool, events = _CreateIssue(), _Events()

        result = await _agent(transport, tool, _ask_later_kernel(end_turn=False), events).run(Goal(description="file a bug"))

        assert tool.runs == []
        assert len(transport.calls) == 2
        assert result.output == "told the user"
        assert result.turns[0].tool_results[0].content == "Waiting for your approval to run create_issue."


class TestASubAgentAsking:
    async def test_the_parent_stops_too_and_answers_with_the_pending_message(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="spawn-1", name="spawn_agent", arguments={
            "role": "filer", "goal": "file the bug", "reasoning": "delegate", "tools": ["create_issue"],
        }))
        transport.add_tool_call(ToolCall(id="call-1", name="create_issue", arguments={"title": "Bug"}))  # the child's turn
        transport.add_tool_call(ToolCall(id="done", name="task_complete", arguments={"output": "never reached"}))

        def _spec_factory(role_name: str, **overrides: Any) -> AgentSpec:  # noqa: ANN401
            return AgentSpec(
                name=f"wrapper-{role_name}", system_prompt="sub-agent", tool_names=list(overrides.get("tool_names") or []),
                model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=5,
            )

        tool = _CreateIssue()
        registry = ToolRegistry()
        for registered in (SpawnAgentTool(), TaskCompleteTool(), tool):
            registry.register_tool(registered)
        transports = TransportRegistry()
        transports.register("scripted", lambda: transport)
        runtime = AgentRuntime(
            transport_registry=transports, tool_registry=registry, hooks=_ask_later_kernel(), spec_factory=_spec_factory,
        )
        parent = Agent(AgentSpec(
            name="orchestrator", system_prompt="dispatch", tool_names=["spawn_agent", "task_complete"],
            model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=10,
        ), runtime)

        result = await parent.run(Goal(description="file a bug"))

        assert tool.runs == []
        assert len(transport.calls) == 2, "the parent wasn't asked again after its sub-agent asked"
        assert result.output == "Waiting for your approval to run create_issue."
