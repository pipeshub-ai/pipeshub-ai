"""`run_spawned_child`'s direct-dispatch optimization (`tools/builtin/
coordination/spawn_agent.py`): a `spawn_agent` call whose resolved
`tool_names` is EXACTLY one composed `AgentTool` (e.g. a deep-mode plan
step delegated to `internal_exploration_agent`) skips the generic ReAct
wrapper `AgentRuntime.spec_for_role()` would otherwise build — that
wrapper's only possible move is to call the ONE tool it has, then
lossily re-narrate the result as its own final answer. Direct dispatch
runs the `AgentTool`'s OWN spec instead, applying its `result_note`
exactly as `AgentTool.handle()` would.

Drives a real top-level `Agent` (`ReActLoop` + `spawn_agent`) through one
shared `ScriptedTransport`, same pattern as `test_spawn_agent_dependencies.
py` — the call COUNT on that shared transport is the load-bearing
assertion: direct dispatch removes exactly the wrapper's own two turns
(the decision to call the domain tool, and the re-narration turn after).
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _spec_factory(role_name: str, **overrides: Any) -> AgentSpec:
    tool_names = overrides.get("tool_names") or []
    model = overrides.get("model") or "scripted-model"
    return AgentSpec(
        name=f"wrapper-{role_name}",
        system_prompt=f"You are the '{role_name}' sub-agent.",
        tool_names=list(tool_names),
        model=ModelSpec(provider="scripted", model=model),
        loop=ReActLoop(),
        max_turns=5,
    )


def _build_registry_and_runtime(
    transport: ScriptedTransport, *, explorer_tool_names: list[str], result_note: str | None = None,
    share_parent_results: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(SpawnAgentTool())
    registry.register_tool(TaskCompleteTool())

    explorer_spec = AgentSpec(
        name="internal_exploration_agent",
        system_prompt="You explore internal knowledge.",
        tool_names=explorer_tool_names,
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=5,
    )
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        spec_factory=_spec_factory,
    )
    registry.register_tool(
        AgentTool(
            explorer_spec, runtime, name="internal_exploration_agent",
            share_parent_results=share_parent_results, result_note=result_note,
        )
    )
    return runtime


def _build_parent(runtime: AgentRuntime, *, max_turns: int = 10) -> Agent:
    spec = AgentSpec(
        name="orchestrator",
        system_prompt="You dispatch sub-agents.",
        tool_names=["spawn_agent", "task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


class TestDirectAgentToolDispatch:
    async def test_single_agent_tool_skips_the_wrapper_hop(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-spawn", name="spawn_agent", arguments={
                "role": "explorer", "goal": "find the Q3 roadmap",
                "reasoning": "internal knowledge lookup",
                "tools": ["internal_exploration_agent"],
            },
        ))
        transport.add_text("Found the Q3 roadmap: ships in September.")  # explorer's OWN turn
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete",
            arguments={"output": "Done: found the Q3 roadmap."},
        ))

        runtime = _build_registry_and_runtime(
            transport, explorer_tool_names=[], result_note="[NOTE] cite sources.",
        )
        agent = _build_parent(runtime)
        result = await agent.run(Goal(description="Find the Q3 roadmap"))

        assert result.success is True
        # Exactly 3 model calls: the parent's spawn_agent turn, the
        # explorer's OWN turn, the parent's task_complete turn — NOT the
        # 5 a wrapper hop would need (spawn turn, wrapper-decides-to-call
        # turn, explorer turn, wrapper-re-narrates turn, task_complete turn).
        assert len(transport.calls) == 3

        spawn_result = agent.last_tool_result("spawn_agent")
        assert spawn_result is not None
        assert "Found the Q3 roadmap: ships in September." in spawn_result["output"]
        # result_note applied even though handle() was never called.
        assert "[NOTE] cite sources." in spawn_result["output"]

    async def test_share_parent_results_falls_back_to_wrapper(self) -> None:
        """An `AgentTool` needing the calling agent's own conversation
        can't be dispatched directly (no `ctx.messages` at this layer) —
        must keep going through the normal wrapper + `handle()` path."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-spawn", name="spawn_agent", arguments={
                "role": "explorer", "goal": "find the Q3 roadmap",
                "reasoning": "internal knowledge lookup",
                "tools": ["internal_exploration_agent"],
            },
        ))
        transport.add_tool_call(ToolCall(  # wrapper decides to call the domain tool
            id="c-call-explorer", name="internal_exploration_agent",
            arguments={"goal": "find the Q3 roadmap"},
        ))
        transport.add_text("Found the Q3 roadmap: ships in September.")  # explorer's OWN turn
        transport.add_text("The Q3 roadmap ships in September.")  # wrapper re-narrates
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete",
            arguments={"output": "Done."},
        ))

        runtime = _build_registry_and_runtime(
            transport, explorer_tool_names=[], share_parent_results=True,
        )
        agent = _build_parent(runtime)
        result = await agent.run(Goal(description="Find the Q3 roadmap"))

        assert result.success is True
        # All 5 scripted turns consumed — the wrapper hop still runs.
        assert len(transport.calls) == 5

    async def test_multiple_tools_falls_back_to_wrapper(self) -> None:
        """`tools=[...]` resolving to more than one tool is not the
        single-delegate shape — must keep the wrapper so the model can
        actually choose between them."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-spawn", name="spawn_agent", arguments={
                "role": "explorer", "goal": "find the Q3 roadmap",
                "reasoning": "internal knowledge lookup",
                "tools": ["internal_exploration_agent", "task_complete"],
            },
        ))
        transport.add_tool_call(ToolCall(
            id="c-call-explorer", name="internal_exploration_agent",
            arguments={"goal": "find the Q3 roadmap"},
        ))
        transport.add_text("Found the Q3 roadmap: ships in September.")
        transport.add_tool_call(ToolCall(
            id="c-wrapper-done", name="task_complete",
            arguments={"output": "The Q3 roadmap ships in September."},
        ))
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete", arguments={"output": "Done."},
        ))

        runtime = _build_registry_and_runtime(transport, explorer_tool_names=[])
        agent = _build_parent(runtime)
        result = await agent.run(Goal(description="Find the Q3 roadmap"))

        assert result.success is True
        assert len(transport.calls) == 5
