"""End-to-end reproduction (and fix verification) of the "two spawned
sub-agents race / the dependent one can't see the prerequisite's output"
bug — e.g. a planner spawning one sub-agent to fetch and categorize Jira
tickets and a second to build a PDF FROM those tickets, in the same turn.

Drives a real top-level `Agent` (`ReActLoop` + `spawn_agent`) through a
single shared `ScriptedTransport` that also answers for every spawned
child (same pattern `test_orchestrator_loop.py` and `test_runtime_opik.py`
already use) — no LLM, but a real `Agent.step()` turn loop, a real
`AgentRuntime.run_child()`, and the real `spawn_scheduler` wiring.
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


def _spec_factory(role_name: str, **overrides: Any) -> AgentSpec:
    tool_names = overrides.get("tool_names") or []
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


class TestSpawnAgentDependencyOrdering:
    async def test_pdf_sub_agent_receives_jira_sub_agent_output(self) -> None:
        """The bug, reproduced and fixed: `task_pdf` depends_on `task_jira`
        — the PDF child's own LLM call must happen strictly after the Jira
        child's, and must see the Jira child's actual ticket data, not an
        isolated goal string."""
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="c-jira", name="spawn_agent", arguments={
                "role": "jira", "goal": "Fetch and categorize open Jira tickets assigned to the user",
                "reasoning": "isolated Jira data-fetch workstream", "task_id": "task_jira",
            }),
            ToolCall(id="c-pdf", name="spawn_agent", arguments={
                "role": "pdf", "goal": "Create a PDF report of the fetched tickets",
                "reasoning": "consumes task_jira's output", "task_id": "task_pdf",
                "depends_on": ["task_jira"],
            }),
        ])
        transport.add_text("Found 3 open tickets: JIRA-1 (bug), JIRA-2 (feature), JIRA-3 (bug).")  # Jira child
        transport.add_text("PDF report created from the 3 tickets above.")  # PDF child
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete",
            arguments={"output": "Done: fetched tickets and built the PDF report."},
        ))

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="Fetch my open Jira tickets and create a PDF report of them"))

        assert result.success is True
        assert result.output == "Done: fetched tickets and built the PDF report."

        # Call order proves the PDF child's LLM call happened strictly
        # after the Jira child's — never in parallel, never reversed.
        # calls[0] is the parent's dispatch turn; [1] is the Jira child's
        # only turn; [2] is the PDF child's only turn; [3] is the parent's
        # final turn.
        assert len(transport.calls) == 4
        pdf_child_call = transport.calls[2]
        pdf_child_messages = pdf_child_call["messages"]
        pdf_goal_text = " ".join(
            m.content if isinstance(m.content, str) else str(m.content) for m in pdf_child_messages
        )
        assert "Found 3 open tickets: JIRA-1 (bug), JIRA-2 (feature), JIRA-3 (bug)." in pdf_goal_text
        assert "Create a PDF report of the fetched tickets" in pdf_goal_text

        jira_child_call = transport.calls[1]
        jira_goal_text = " ".join(
            m.content if isinstance(m.content, str) else str(m.content) for m in jira_child_call["messages"]
        )
        assert "Fetch and categorize open Jira tickets" in jira_goal_text
        # The Jira child must NOT have seen the PDF task's goal — no
        # data flows "backwards" from a dependent to its prerequisite.
        assert "Create a PDF report" not in jira_goal_text

    async def test_independent_spawns_still_run_without_extra_turns(self) -> None:
        """Two spawn_agent calls with no depends_on between them keep
        today's behavior: both dispatch in the same turn, no artificial
        ordering, no goal mutation."""
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="c-a", name="spawn_agent", arguments={
                "role": "alpha", "goal": "Research topic A", "reasoning": "independent workstream A",
            }),
            ToolCall(id="c-b", name="spawn_agent", arguments={
                "role": "beta", "goal": "Research topic B", "reasoning": "independent workstream B",
            }),
        ])
        transport.add_text("Findings on topic A.")
        transport.add_text("Findings on topic B.")
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete", arguments={"output": "Combined findings on A and B."},
        ))

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="Research topics A and B"))

        assert result.success is True
        assert result.output == "Combined findings on A and B."
        assert len(transport.calls) == 4
        for call in (transport.calls[1], transport.calls[2]):
            messages = call["messages"]
            goal_text = " ".join(m.content if isinstance(m.content, str) else str(m.content) for m in messages)
            assert "## Results from prerequisite tasks" not in goal_text

    async def test_failed_prerequisite_reports_actionable_error_to_planner(self) -> None:
        """If the Jira fetch itself fails, the PDF task must never run
        against missing data — the planner sees a corrective error instead
        of a silently-empty/garbage PDF."""
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="c-jira", name="spawn_agent", arguments={
                "role": "jira", "goal": "Fetch Jira tickets", "reasoning": "fetch",
                "task_id": "task_jira",
            }),
            ToolCall(id="c-pdf", name="spawn_agent", arguments={
                "role": "pdf", "goal": "Build the PDF", "reasoning": "consumes jira output",
                "task_id": "task_pdf", "depends_on": ["task_jira"],
            }),
        ])
        transport.add_error(RuntimeError("Jira API unreachable"))  # Jira child's only turn fails
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete",
            arguments={"output": "Could not fetch Jira tickets, so no PDF was created."},
        ))

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="Fetch my open Jira tickets and create a PDF report of them"))

        assert result.success is True
        assert result.output == "Could not fetch Jira tickets, so no PDF was created."
        # Only the parent's dispatch turn + Jira's failing turn + the
        # parent's final turn happened — the PDF child was never launched.
        assert len(transport.calls) == 3
