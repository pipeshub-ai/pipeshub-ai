"""End-to-end Opik tracing across one full `Agent.run()` turn (plan section
4f): a single conversation should produce ONE root trace with the turn's
LLM-call spans AND its tool-call spans nested underneath, all through
agent_loop_lib's own choke points (`OpikTracingTransport`, `ToolExecutor.
call_tool()`, `Agent.run()`'s `maybe_start_run_trace`) — no PipesHub
adapter layer involved, so this pins the underlying library behavior the
adapter wiring (`test_factory_opik.py`) builds on top of."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.opik_tracing import wrap_if_enabled
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _FakeSpan(SimpleNamespace):
    pass


@contextlib.contextmanager
def _fake_cm(**_kwargs: Any):
    yield _FakeSpan()


class _SearchTool(Tool):
    @property
    def name(self) -> str:
        return "search"

    @property
    def short_description(self) -> str:
        return "Searches for something"

    @property
    def description(self) -> str:
        return "Searches for something and returns a result"

    @property
    def path(self) -> str:
        return "/toolsets/test/search"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="query", type=ParameterType.STRING, description="search query")]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=f"found results for {kwargs['query']}")


def _build_runtime(transport: ScriptedTransport, *, opik_enabled: bool) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register_tool(_SearchTool())
    registry.register_tool(TaskCompleteTool())

    transport_registry = TransportRegistry()
    transport_registry.register(
        "scripted", lambda: wrap_if_enabled(transport, enabled=opik_enabled, project_name="proj")
    )

    return AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        opik_enabled=opik_enabled,
        opik_project_name="proj" if opik_enabled else None,
    )


def _build_agent(runtime: AgentRuntime) -> Agent:
    spec = AgentSpec(
        name="assistant",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=10,
    )
    return Agent(spec, runtime)


def _scripted_two_turn_run() -> ScriptedTransport:
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="1", name="search", arguments={"query": "cats"}))
    transport.add_tool_call(ToolCall(id="2", name="task_complete", arguments={"output": "Cats are great."}))
    return transport


class TestFullTurnOpikTracing:
    async def test_produces_one_trace_with_llm_and_tool_spans(self) -> None:
        transport = _scripted_two_turn_run()
        runtime = _build_runtime(transport, opik_enabled=True)
        agent = _build_agent(runtime)

        with (
            patch("opik.start_as_current_trace", side_effect=lambda **kw: _fake_cm(**kw)) as mock_trace,
            patch("opik.start_as_current_span", side_effect=lambda **kw: _fake_cm(**kw)) as mock_span,
        ):
            result = await agent.run(Goal(description="Tell me about cats"))

        assert result.success is True
        assert result.output == "Cats are great."

        # Exactly one root trace for the whole conversation.
        mock_trace.assert_called_once()
        trace_kwargs = mock_trace.call_args.kwargs
        assert trace_kwargs["name"] == "agent.assistant"
        assert trace_kwargs["project_name"] == "proj"

        # Two LLM-call spans (one per turn) + two tool-call spans (search, task_complete).
        span_names = [call.kwargs["name"] for call in mock_span.call_args_list]
        assert span_names.count("scripted.complete") == 2
        assert "tool.search" in span_names
        assert "tool.task_complete" in span_names
        assert len(span_names) == 4

    async def test_tool_span_nests_correctly_by_call_order(self) -> None:
        """Each tool span opens/closes fully between its owning turn's LLM
        span and the next turn's LLM span — pins the interleaving order the
        trace tree should show, not just that all spans exist somewhere."""
        transport = _scripted_two_turn_run()
        runtime = _build_runtime(transport, opik_enabled=True)
        agent = _build_agent(runtime)

        with (
            patch("opik.start_as_current_trace", side_effect=lambda **kw: _fake_cm(**kw)),
            patch("opik.start_as_current_span", side_effect=lambda **kw: _fake_cm(**kw)) as mock_span,
        ):
            await agent.run(Goal(description="Tell me about cats"))

        span_names = [call.kwargs["name"] for call in mock_span.call_args_list]
        assert span_names == ["scripted.complete", "tool.search", "scripted.complete", "tool.task_complete"]

    async def test_no_opik_calls_when_disabled(self) -> None:
        transport = _scripted_two_turn_run()
        runtime = _build_runtime(transport, opik_enabled=False)
        agent = _build_agent(runtime)

        with (
            patch("opik.start_as_current_trace") as mock_trace,
            patch("opik.start_as_current_span") as mock_span,
        ):
            result = await agent.run(Goal(description="Tell me about cats"))

        assert result.success is True
        mock_trace.assert_not_called()
        mock_span.assert_not_called()

    async def test_opik_failures_never_break_the_run(self) -> None:
        """Tracing is best-effort end to end — a totally broken Opik client
        must not prevent the agent from completing its task."""
        transport = _scripted_two_turn_run()
        runtime = _build_runtime(transport, opik_enabled=True)
        agent = _build_agent(runtime)

        with (
            patch("opik.start_as_current_trace", side_effect=RuntimeError("opik down")),
            patch("opik.start_as_current_span", side_effect=RuntimeError("opik down")),
        ):
            result = await agent.run(Goal(description="Tell me about cats"))

        assert result.success is True
        assert result.output == "Cats are great."

    async def test_llm_span_records_tool_call_and_usage(self) -> None:
        from app.agent_loop_lib.core.responses import TokenUsage

        transport = ScriptedTransport()
        transport.add_tool_call(
            ToolCall(id="1", name="search", arguments={"query": "cats"}),
            usage=TokenUsage(input_tokens=20, output_tokens=8),
        )
        transport.add_tool_call(ToolCall(id="2", name="task_complete", arguments={"output": "done"}))
        runtime = _build_runtime(transport, opik_enabled=True)
        agent = _build_agent(runtime)

        captured_spans: list[_FakeSpan] = []

        @contextlib.contextmanager
        def _capture(**_kwargs: Any):
            span = _FakeSpan()
            yield span
            captured_spans.append(span)

        with (
            patch("opik.start_as_current_trace", side_effect=lambda **kw: _fake_cm(**kw)),
            patch("opik.start_as_current_span", side_effect=lambda **kw: _capture(**kw)),
        ):
            await agent.run(Goal(description="Tell me about cats"))

        first_llm_span = captured_spans[0]
        assert first_llm_span.output["content"]["tool_calls"][0]["name"] == "search"
        assert first_llm_span.usage["prompt_tokens"] == 20
        assert first_llm_span.usage["completion_tokens"] == 8

        search_tool_span = captured_spans[1]
        assert search_tool_span.output["data"] == "found results for cats"
        assert search_tool_span.output["success"] is True
