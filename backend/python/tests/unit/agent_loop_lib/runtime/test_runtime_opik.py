"""`AgentRuntime.run_child()` Opik sub-agent-span wiring
(`app/agent_loop_lib/runtime/runtime.py`).

Covers the plan's "1b. Sub-agent spans" item: every child agent launched via
`run_child()` (both `spawn_agent`/`best_of_n` dynamic fan-out and
`AgentTool` static composition) gets one Opik `"general"` span named
`agent.{role}` wrapping the child's entire `Agent.run()` call, with the
child's goal recorded as span input and its `AgentResult` as span output."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import MAX_SPAWN_DEPTH, AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _FakeSpan(SimpleNamespace):
    pass


@contextlib.contextmanager
def _fake_span_cm(**_kwargs: Any):
    yield _FakeSpan()


def _build_runtime(transport: ScriptedTransport, *, opik_enabled: bool = False) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())

    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    return AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        opik_enabled=opik_enabled,
        opik_project_name="proj" if opik_enabled else None,
    )


def _child_spec(*, max_turns: int = 5) -> AgentSpec:
    return AgentSpec(
        name="researcher",
        system_prompt="You are a researcher.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=max_turns,
    )


class TestRunChildOpikSpan:
    async def test_creates_agent_span_named_after_role(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="1", name="task_complete", arguments={"output": "done researching"}))
        runtime = _build_runtime(transport, opik_enabled=True)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        with patch("opik.start_as_current_span", side_effect=lambda **_kw: _fake_span_cm()) as mock_span:
            result = await runtime.run_child(_child_spec(), Goal(description="research X"), parent_ctx)

        assert result.success is True
        assert result.output == "done researching"

        _, kwargs = mock_span.call_args_list[0]
        assert kwargs["name"] == "agent.researcher"
        assert kwargs["type"] == "general"
        assert kwargs["input"]["description"] == "research X"
        assert kwargs["project_name"] == "proj"

    async def test_span_records_agent_result_on_success(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="1", name="task_complete", arguments={"output": "42"}))
        runtime = _build_runtime(transport, opik_enabled=True)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        captured: dict[str, Any] = {}

        @contextlib.contextmanager
        def _capture(**_kwargs: Any):
            span = _FakeSpan()
            yield span
            captured["span"] = span

        with patch("opik.start_as_current_span", side_effect=lambda **kw: _capture(**kw)):
            await runtime.run_child(_child_spec(), Goal(description="compute the answer"), parent_ctx)

        assert captured["span"].output["success"] is True
        assert captured["span"].output["output"] == "42"
        assert captured["span"].output["error"] is None

    async def test_no_span_when_opik_disabled(self) -> None:
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport, opik_enabled=False)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        with patch("opik.start_as_current_span") as mock_span:
            result = await runtime.run_child(_child_spec(), Goal(description="just finish"), parent_ctx)

        mock_span.assert_not_called()
        assert result.success is True

    async def test_child_run_context_inherits_trace_id_from_parent(self) -> None:
        """`run_child()`'s Opik span isn't the only thing establishing the
        parent/child relationship — `RunContext.child()` must also carry the
        same `trace_id` so the child's own turn-level LLM spans nest under
        the shared trace, not a fresh one."""
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport, opik_enabled=True)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        captured_child_ctx: dict[str, Any] = {}
        original_agent_run = Agent.run

        async def _spy_run(self: Agent, goal: Any, **kwargs: Any) -> Any:
            captured_child_ctx["run_ctx"] = self._run_ctx
            return await original_agent_run(self, goal, **kwargs)

        with patch("opik.start_as_current_span", side_effect=lambda **_kw: _fake_span_cm()):
            with patch.object(Agent, "run", _spy_run):
                await runtime.run_child(_child_spec(), Goal(description="x"), parent_ctx)

        child_ctx = captured_child_ctx["run_ctx"]
        assert child_ctx.trace_id == parent_ctx.trace_id
        assert child_ctx.parent_run_id == parent_ctx.run_id
        assert child_ctx.role_name == "researcher"

    async def test_opik_failure_does_not_break_the_child_run(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="1", name="task_complete", arguments={"output": "still fine"}))
        runtime = _build_runtime(transport, opik_enabled=True)
        parent_ctx = RunContext(role_name="assistant", model="scripted-model")

        with patch("opik.start_as_current_span", side_effect=RuntimeError("opik is down")):
            result = await runtime.run_child(_child_spec(), Goal(description="x"), parent_ctx)

        assert result.success is True
        assert result.output == "still fine"

    async def test_max_spawn_depth_is_enforced_before_opening_a_span(self) -> None:
        from app.agent_loop_lib.core.exceptions import AgentError

        transport = ScriptedTransport()
        runtime = _build_runtime(transport, opik_enabled=True)
        deep_parent_ctx = RunContext(
            role_name="assistant", model="scripted-model", spawn_depth=MAX_SPAWN_DEPTH,
        )

        with patch("opik.start_as_current_span") as mock_span:
            try:
                await runtime.run_child(_child_spec(), Goal(description="too deep"), deep_parent_ctx)
                raised = False
            except AgentError:
                raised = True

        assert raised is True
        mock_span.assert_not_called()

    async def test_no_parent_ctx_still_opens_span_but_no_run_context_inheritance(self) -> None:
        """`AgentTool` static composition passes `parent_run_ctx=None` —
        the span still opens (nesting under whatever is active via Opik's
        own contextvar storage), but no `RunContext.child()` linkage happens.

        With no parent, the child's own `_run_ctx.parent_run_id` stays
        `None`, so `Agent.run()`'s `maybe_start_run_trace` treats it as a
        ROOT run and opens a real trace too — patch that as well so this
        test doesn't attempt a real Opik network call."""
        transport = ScriptedTransport()
        transport.add_text("done")
        runtime = _build_runtime(transport, opik_enabled=True)

        with (
            patch("opik.start_as_current_span", side_effect=lambda **_kw: _fake_span_cm()) as mock_span,
            patch("opik.start_as_current_trace", side_effect=lambda **_kw: _fake_span_cm()),
        ):
            result = await runtime.run_child(_child_spec(), Goal(description="solo"), None)

        assert result.success is True
        assert mock_span.call_count >= 1
        first_name = mock_span.call_args_list[0].kwargs["name"]
        assert first_name == "agent.researcher"
