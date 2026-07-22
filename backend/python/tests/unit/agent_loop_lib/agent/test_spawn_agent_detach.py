"""Regression test for the `spawn_agent(detach=true)` double-launch bug:
`Agent.step()` used to pre-launch EVERY `spawn_agent` call (detached ones
included) via `schedule_spawn_batch`, and `SpawnAgentTool.handle()` would
then launch a SECOND, entirely separate child for the same call without
ever awaiting (or even referencing) the first one — two children spawned,
one of them silently orphaned.

Drives a real top-level `Agent` (`ReActLoop` + `spawn_agent`) through a
single shared `ScriptedTransport`, same pattern as
`test_spawn_agent_dependencies.py` — no LLM, but a real `Agent.step()` turn
loop and a real `AgentRuntime.run_child()`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spawn_scheduler import SPAWN_RESULTS_SLOT
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
        system_prompt="You are a planner that can kick off background sub-agents.",
        tool_names=["spawn_agent", "task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


class TestSpawnAgentDetach:
    async def test_detached_spawn_launches_exactly_one_child(self) -> None:
        """The bug, reproduced and fixed: a `detach=true` call must launch
        its child exactly once — not once via the turn's pre-launch batch
        AND once again from `SpawnAgentTool.handle()`."""
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="c-spawn", name="spawn_agent", arguments={
                "role": "worker", "goal": "Do background work",
                "reasoning": "independent background workstream",
                "task_id": "bg_task", "detach": True,
            }),
            ToolCall(id="c-done", name="task_complete", arguments={
                "output": "Kicked off background work.",
            }),
        ])
        transport.add_text("Background work finished.")  # the detached child's only turn

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="Do some work and kick off a background task"))

        assert result.success is True
        assert result.output == "Kicked off background work."

        # Detach must never populate the pre-launch batch — the parent's
        # only turn made exactly one LLM call (spawn dispatch + task_complete
        # together), with no scheduled spawn task left dangling.
        assert len(transport.calls) == 1
        assert agent._pending_spawn_tasks == {}

        detached_tasks = list(agent._detached_tasks)
        assert len(detached_tasks) == 1, "exactly one detached task, never two"
        await asyncio.gather(*detached_tasks)

        # The detached child made exactly one additional LLM call — a
        # second, orphaned launch (the bug) would have made it two.
        assert len(transport.calls) == 2

        # Detached completions are still recorded into SPAWN_RESULTS_SLOT
        # so a `depends_on` from a LATER turn could reference this task_id.
        completed = agent.scope.get(SPAWN_RESULTS_SLOT)
        assert "bg_task" in completed
        assert completed["bg_task"].result.success is True
        assert completed["bg_task"].result.output == "Background work finished."

    async def test_detached_spawn_records_failure_for_later_dependents(self) -> None:
        """An infrastructure failure inside a detached run must still land
        in SPAWN_RESULTS_SLOT (as a failed result), not vanish silently."""
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="c-spawn", name="spawn_agent", arguments={
                "role": "worker", "goal": "Do background work",
                "reasoning": "independent background workstream",
                "task_id": "bg_task", "detach": True,
            }),
            ToolCall(id="c-done", name="task_complete", arguments={
                "output": "Kicked off background work.",
            }),
        ])
        transport.add_error(RuntimeError("boom"))  # the detached child's only turn fails

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="Do some work and kick off a background task"))

        assert result.success is True

        detached_tasks = list(agent._detached_tasks)
        assert len(detached_tasks) == 1
        await asyncio.gather(*detached_tasks)

        completed = agent.scope.get(SPAWN_RESULTS_SLOT)
        assert "bg_task" in completed
        assert completed["bg_task"].result.success is False
