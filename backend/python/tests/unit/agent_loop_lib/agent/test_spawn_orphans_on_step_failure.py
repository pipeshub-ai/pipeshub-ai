"""`Agent.step()`'s tool-dispatch phase (`agent/__init__.py`): a pre-launched
`spawn_agent` task (via `spawn_scheduler.schedule_spawn_batch`) must be
cancelled if anything LATER in that same phase raises before the turn loop
ever reaches the point where it awaits that task.

Without the `try/finally` wrapping that phase, a pre-launched child keeps
running — mutating shared runtime/hook/tool_state — even after `Agent.
step()` itself has already failed out from under it. See `spawn_scheduler.
cancel_pending_spawn_tasks`, invoked unconditionally from that `finally`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spawn_scheduler import cancel_pending_spawn_tasks
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _spec_factory(role_name, **overrides):
    return AgentSpec(
        name=f"child-{role_name}",
        system_prompt=f"You are the '{role_name}' sub-agent.",
        tool_names=list(overrides.get("tool_names") or []),
        model=ModelSpec(provider="scripted", model=overrides.get("model") or "scripted-model"),
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
        transport_registry=transport_registry, tool_registry=registry, spec_factory=_spec_factory,
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


class TestPendingSpawnTaskCancelledOnStepFailure:
    async def test_pre_launched_spawn_task_cancelled_when_dispatch_phase_raises(self) -> None:
        """`schedule_spawn_batch` launches the child's task before
        `execute_tool_call` ever reaches the point (deep inside
        `SpawnAgentTool.handle()`) where it would normally await it. Forcing
        `execute_tool_call`'s OWN first step (its "pre_tool" checkpoint) to
        raise reproduces the exact failure window the fix closes: a
        pre-launched task that the turn loop never gets a chance to await."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="c-spawn", name="spawn_agent", arguments={
            "role": "worker", "goal": "do independent research", "reasoning": "independent workstream",
        }))
        transport.add_text("child's own turn — should never actually run to completion observably")

        agent = _build_parent(transport)

        with patch(
            "app.agent_loop_lib.agent.tool_loop.obs.save_checkpoint",
            side_effect=RuntimeError("boom: checkpoint store down"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await agent.run(Goal(description="Research something"))

        assert "c-spawn" in agent._pending_spawn_tasks
        task = agent._pending_spawn_tasks["c-spawn"]
        # Cancelled by the `finally`, not merely "eventually settles" —
        # confirms the fix actively tears the orphan down rather than
        # relying on it to finish naturally after the parent already failed.
        assert task.cancelled()

    async def test_successful_turn_leaves_no_pending_task_running(self) -> None:
        """Sanity check: on the ordinary success path, `cancel_pending_
        spawn_tasks` running unconditionally from the `finally` is a no-op
        — the spawn task was already awaited and completed before
        `finally` runs, so the run must complete normally, with the
        child's actual output flowing through untouched."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="c-spawn", name="spawn_agent", arguments={
            "role": "worker", "goal": "do independent research", "reasoning": "independent workstream",
        }))
        transport.add_text("child result")
        transport.add_tool_call(ToolCall(id="c-done", name="task_complete", arguments={"output": "done"}))

        agent = _build_parent(transport)
        result = await agent.run(Goal(description="Research something"))

        assert result.success is True
        assert result.output == "done"
        spawn_turn = result.turns[0]
        assert spawn_turn.tool_results[0].is_error is False


class TestCancelPendingSpawnTasks:
    """Direct unit coverage of `cancel_pending_spawn_tasks` — no `Agent`."""

    async def test_cancels_and_awaits_running_tasks(self) -> None:
        started = asyncio.Event()
        cancelled_seen = False

        async def _never_finishes() -> None:
            nonlocal cancelled_seen
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_seen = True
                raise

        task = asyncio.create_task(_never_finishes())
        await started.wait()

        await cancel_pending_spawn_tasks({"c1": task})

        assert task.cancelled()
        assert cancelled_seen is True

    async def test_noop_for_already_completed_tasks(self) -> None:
        async def _immediate() -> AgentResult:
            return AgentResult(goal=Goal(description="x"), output="done", success=True)

        task = asyncio.create_task(_immediate())
        await task

        await cancel_pending_spawn_tasks({"c1": task})

        assert task.done()
        assert not task.cancelled()

    async def test_empty_batch_is_a_noop(self) -> None:
        await cancel_pending_spawn_tasks({})

    async def test_mixed_batch_only_cancels_still_running_tasks(self) -> None:
        async def _immediate() -> str:
            return "done"

        async def _slow() -> None:
            await asyncio.sleep(10)

        done_task = asyncio.create_task(_immediate())
        await done_task
        running_task = asyncio.create_task(_slow())
        await asyncio.sleep(0)  # let it actually start

        await cancel_pending_spawn_tasks({"done": done_task, "running": running_task})

        assert not done_task.cancelled()
        assert running_task.cancelled()
