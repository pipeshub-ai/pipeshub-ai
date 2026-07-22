"""`AgentRuntime.run_child()` streaming flag ("Parts-Based Agent Message
Transcript" plan, `py-child-stream`): every child agent must run with
`_streaming = True` so its own turns emit `TEXT_MESSAGE_*`/
`REASONING_MESSAGE_*` through the shared `event_emitter` — otherwise
`step()` takes the non-streaming `_model.complete()` branch and a
sub-agent's activity never reaches `TranscriptCollector`/`AGUIEventEmitter`.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _build_runtime(transport: ScriptedTransport) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    return AgentRuntime(transport_registry=transport_registry, tool_registry=registry)


def _child_spec() -> AgentSpec:
    return AgentSpec(
        name="researcher",
        system_prompt="You are a researcher.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=5,
    )


class TestRunChildStreamingFlag:
    async def test_child_agent_is_flipped_to_streaming_before_run(self) -> None:
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        observed: dict[str, Any] = {}
        original_run = Agent.run

        async def _spy_run(self: Agent, goal: Any, **kwargs: Any) -> Any:
            observed["streaming"] = self._streaming
            return await original_run(self, goal, **kwargs)

        from unittest.mock import patch

        with patch.object(Agent, "run", _spy_run):
            result = await runtime.run_child(_child_spec(), Goal(description="research X"), parent_ctx)

        assert result.success is True
        assert observed["streaming"] is True

    async def test_child_agent_still_streaming_with_no_parent_ctx(self) -> None:
        """`AgentTool` static composition passes `parent_run_ctx=None` —
        the streaming flag must still be set regardless of that path."""
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport)

        observed: dict[str, Any] = {}
        original_run = Agent.run

        async def _spy_run(self: Agent, goal: Any, **kwargs: Any) -> Any:
            observed["streaming"] = self._streaming
            return await original_run(self, goal, **kwargs)

        from unittest.mock import patch

        with patch.object(Agent, "run", _spy_run):
            result = await runtime.run_child(_child_spec(), Goal(description="solo"), None)

        assert result.success is True
        assert observed["streaming"] is True

    async def test_mirror_events_false_opts_child_out_of_streaming(self) -> None:
        """A caller that wants a silent sub-agent (no token-level narration
        on the parent's stream) passes `mirror_events=False` — the child
        must still run to completion, just without `streaming` flipped on."""
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        observed: dict[str, Any] = {}
        original_run = Agent.run

        async def _spy_run(self: Agent, goal: Any, **kwargs: Any) -> Any:
            observed["streaming"] = self.streaming
            return await original_run(self, goal, **kwargs)

        from unittest.mock import patch

        with patch.object(Agent, "run", _spy_run):
            result = await runtime.run_child(
                _child_spec(), Goal(description="silent judge"), parent_ctx, mirror_events=False,
            )

        assert result.success is True
        assert observed["streaming"] is False

    async def test_run_child_seeds_context_via_public_seam(self) -> None:
        """`run_child()` must give every child a fresh `ContextManager`
        through `Agent.seed_context()` rather than poking `_context`
        directly — asserted via the public `agent.context` getter."""
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport)

        observed: dict[str, Any] = {}
        original_run = Agent.run

        async def _spy_run(self: Agent, goal: Any, **kwargs: Any) -> Any:
            observed["context"] = self.context
            return await original_run(self, goal, **kwargs)

        from unittest.mock import patch

        with patch.object(Agent, "run", _spy_run):
            await runtime.run_child(_child_spec(), Goal(description="research X"), None)

        assert observed["context"] is not None
