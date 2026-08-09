"""End-to-end verification of sub-agent `needs_input` escalation to the
root agent (task engine plan Part D1/D2: "sub-agent needs_input escalation
to root -- untested end-to-end" / "test needs_input escalation before DAG
work depends on it").

Drives a real top-level `Agent` (`ReActLoop` + `spawn_agent`) through a
single shared `ScriptedTransport` that also answers for the spawned child
(same pattern as `test_spawn_agent_dependencies.py`) — no LLM, but a real
`Agent.step()` turn loop, a real `AgentRuntime.run_child()`, and the real
`spawn_agent`/`child_result_content` wiring. The child calls
`task_complete(needs_input=...)` instead of completing normally; this
verifies the escalation actually reaches the PARENT's tool_result content
(`child_result_content`) through a live spawn, not just the unit-level
`child_result_content()` coverage in `test_child_result_content.py`.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _spec_factory(role_name: str, **overrides: Any) -> AgentSpec:  # noqa: ANN401 -- matches AgentRuntime.spec_factory's own Callable[..., AgentSpec] shape
    tool_names = overrides.get("tool_names") or ["task_complete"]
    model = overrides.get("model") or "scripted-model"
    return AgentSpec(
        name=f"child-{role_name}",
        system_prompt=f"You are the '{role_name}' sub-agent.",
        tool_names=list(tool_names),
        model=ModelSpec(provider="scripted", model=model),
        loop=ReActLoop(),
        max_turns=5,
    )


def _build_parent(transport: ScriptedTransport, *, max_turns: int = 10) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(SpawnAgentTool())
    registry.register_tool(TaskCompleteTool())

    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        spec_factory=_spec_factory,
    )
    spec = AgentSpec(
        name="planner",
        system_prompt="You are a planner that spawns sub-agents.",
        tool_names=["spawn_agent", "task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


class TestChildNeedsInputEscalatesToParent:
    async def test_childs_needs_input_reaches_parents_tool_result(self) -> None:
        """The child calls `task_complete(needs_input=...)` instead of
        finishing normally — the PARENT's `spawn_agent` tool result must
        surface that escalation (via `child_result_content`), not silently
        treat the child as having completed successfully."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-jira", name="spawn_agent", arguments={
                "role": "jira", "goal": "File a ticket in the current sprint",
                "reasoning": "isolated Jira workstream", "task_id": "task_jira",
            },
        ))
        # Child's only turn: escalates instead of completing cleanly.
        transport.add_tool_call(ToolCall(
            id="c-child-1", name="task_complete", arguments={
                "output": "Drafted the ticket but could not identify the sprint",
                "needs_input": "the target sprint name",
            },
        ))
        # Parent's final turn, after seeing the escalation in its own context.
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete", arguments={
                "output": "Need the sprint name from the user before filing the ticket.",
                "needs_input": "the target sprint name",
            },
        ))

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="File a Jira ticket in the current sprint"))

        # The child's escalation must have reached the PARENT's own
        # tool-result content for the spawn_agent call, one turn before
        # the parent's own final turn ran (so the parent model actually
        # saw it before deciding how to respond).
        parent_dispatch_turn = result.turns[0]
        spawn_result = next(tr for tr in parent_dispatch_turn.tool_results if tr.name == "spawn_agent")
        assert spawn_result.is_error is False
        assert "needs_input" in spawn_result.content
        assert "the target sprint name" in spawn_result.content["needs_input"]
        assert "could not fully complete" in spawn_result.content["needs_input"]

        # The parent itself also escalates upward in this scenario (it
        # decided it needs the same missing info) — proving the chain is
        # not just "child sets needs_input", but that it is genuinely
        # usable by whatever consumes the PARENT's own `AgentResult`
        # (e.g. `TaskExecutor`, which keys off exactly this field).
        assert result.success is True
        assert result.needs_input == "the target sprint name"

    async def test_childs_successful_completion_does_not_set_needs_input(self) -> None:
        """Control case: a child that completes normally must NOT leave
        any needs_input residue on the parent's tool result or the run."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-jira", name="spawn_agent", arguments={
                "role": "jira", "goal": "File a ticket in Sprint 42",
                "reasoning": "isolated Jira workstream", "task_id": "task_jira",
            },
        ))
        transport.add_tool_call(ToolCall(
            id="c-child-1", name="task_complete", arguments={"output": "Filed ticket JIRA-99 in Sprint 42"},
        ))
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete", arguments={"output": "Filed ticket JIRA-99 in Sprint 42"},
        ))

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="File a Jira ticket in Sprint 42"))

        parent_dispatch_turn = result.turns[0]
        spawn_result = next(tr for tr in parent_dispatch_turn.tool_results if tr.name == "spawn_agent")
        assert "needs_input" not in spawn_result.content
        assert result.needs_input is None
