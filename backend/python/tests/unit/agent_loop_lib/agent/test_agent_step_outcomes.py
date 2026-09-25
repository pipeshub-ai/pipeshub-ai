"""How one `Agent.step()` ends, for every way a chat turn can go wrong or be
steered: a hook refusing the run or a turn, the user pressing Stop, a
guardrail blocking input or output, the model erroring or being cut off at
its output limit, and a hook asking for another turn instead of accepting a
text answer.

Each of these decides what the user finally sees (an answer, an apology,
"stopped"), so each test drives a real `Agent` + `AgentRuntime` with the
production turn guards; only the LLM is scripted.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.responses import ModelResponse, StopReason, TokenUsage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventEmitter, EventType
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.modules.providers.budget.tracker import BudgetTracker
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

_GOAL = Goal(description="What changed this week?")


class _Recorder(EventEmitter):
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def types(self) -> list[EventType]:
        return [e.event_type for e in self.events]


class _RecordingTransport(ScriptedTransport):
    """Also records the per-call options `ScriptedTransport` drops."""

    def __init__(self, script: list[ScriptedStep] | None = None) -> None:
        super().__init__(script)
        self.options: list[dict[str, Any]] = []

    async def complete(self, messages, tools=None, system=None, model=None,
                       thinking_budget=None, effort=None, system_blocks=None) -> ModelResponse:
        self.options.append({
            "thinking_budget": thinking_budget, "effort": effort, "system_blocks": system_blocks,
        })
        return await super().complete(messages, tools=tools, system=system, model=model)


class _NoteTool(Tool):
    """Records every execution, so a test can tell whether it really ran."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    @property
    def name(self) -> str:
        return "note"

    @property
    def short_description(self) -> str:
        return "Takes a note"

    @property
    def description(self) -> str:
        return "Takes a note"

    @property
    def path(self) -> str:
        return "/toolsets/test/note"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="text", type=ParameterType.STRING, description="note")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        self.executed.append(str(kwargs["text"]))
        return ToolOutput(success=True, data=f"noted {kwargs['text']}")


def _build(
    transport: ScriptedTransport,
    *,
    tool: Tool | None = None,
    max_turns: int = 5,
    **runtime_kwargs: object,
) -> tuple[Agent, _Recorder]:
    registry = ToolRegistry()
    registry.register_tool(tool or _NoteTool())
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    recorder = _Recorder()
    runtime = AgentRuntime(
        transport_registry=transports, tool_registry=registry, event_emitter=recorder, **runtime_kwargs,
    )
    spec = AgentSpec(
        name="step-outcome-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime), recorder


def _use(agent: Agent, event: HookEvent, middleware: Any) -> None:  # noqa: ANN401
    agent.runtime.hooks.on(event).use(middleware)


class TestRunRefusedBeforeItStarts:
    async def test_pre_agent_denial_fails_without_calling_the_model(self) -> None:
        transport = ScriptedTransport().add_text("should never be asked")
        agent, _ = _build(transport)

        async def _deny(ctx, next_fn) -> None:
            ctx.deny("workspace is read-only")

        _use(agent, HookEvent.PRE_AGENT, _deny)
        seen: list[Any] = []

        async def _post(ctx, next_fn) -> None:
            seen.append(ctx.result)
            await next_fn()

        _use(agent, HookEvent.POST_AGENT, _post)

        result = await agent.run(_GOAL)

        assert result.success is False
        assert result.error == "Blocked before start: workspace is read-only"
        assert transport.calls == []
        assert seen == [result]

    async def test_pre_turn_denial_fails_with_the_turn_number(self) -> None:
        transport = ScriptedTransport().add_text("should never be asked")
        agent, recorder = _build(transport)

        async def _deny(ctx, next_fn) -> None:
            ctx.deny("rate limited")

        _use(agent, HookEvent.PRE_TURN, _deny)

        result = await agent.run(_GOAL)

        assert result.success is False
        assert result.cancelled is False
        assert result.error == "Blocked before turn 0: rate limited"
        assert transport.calls == []
        assert EventType.ERROR in recorder.types()
        assert EventType.RUN_ERROR in recorder.types()


class TestStopGeneration:
    async def test_stop_pressed_before_a_turn_reports_cancelled_not_failed(self) -> None:
        token = CancellationToken()
        token.cancel()
        transport = ScriptedTransport().add_text("should never be asked")
        agent, recorder = _build(transport, cancellation_token=token)

        result = await agent.run(_GOAL)

        assert result.success is False
        assert result.cancelled is True
        assert transport.calls == []
        assert EventType.CANCELLATION in recorder.types()
        assert EventType.ERROR not in recorder.types()

    async def test_model_stopping_mid_response_reports_cancelled(self) -> None:
        transport = ScriptedTransport([ScriptedStep(
            message=AssistantMessage(content="The first half of"), stop_reason=StopReason.CANCELLED,
        )])
        agent, _ = _build(transport)

        result = await agent.run(_GOAL)

        assert result.cancelled is True
        assert result.error == "Cancelled"
        history = await agent.context.messages()
        assert history[-1].text == "The first half of"

    async def test_stop_pressed_while_tools_are_queued_skips_them(self) -> None:
        token = CancellationToken()
        tool = _NoteTool()
        transport = ScriptedTransport().add_tool_call(ToolCall(id="n1", name="note", arguments={"text": "a"}))
        agent, _ = _build(transport, tool=tool, cancellation_token=token)

        async def _cancel_after_model(ctx, next_fn) -> None:
            token.cancel()
            await next_fn()

        _use(agent, HookEvent.POST_MODEL, _cancel_after_model)

        result = await agent.run(_GOAL)

        assert tool.executed == []
        first_turn = agent.scope.turns[0]
        assert first_turn.tool_results[0].content == "Cancelled before execution"
        assert first_turn.tool_results[0].is_error is True
        assert result.cancelled is True


class TestGuardrails:
    async def test_input_guardrail_block_wins_over_a_slow_model_call(self) -> None:
        transport = ScriptedTransport([ScriptedStep(message=AssistantMessage(content="late"), delay=5.0)])
        agent, _ = _build(transport)

        async def _block(ctx, next_fn) -> None:
            ctx.block("contains a password")

        _use(agent, HookEvent.GUARDRAIL_INPUT, _block)

        result = await asyncio.wait_for(agent.run(_GOAL), timeout=2.0)

        assert result.success is False
        assert result.error == "Guardrail blocked: contains a password"

    async def test_input_guardrail_block_after_a_fast_model_call_still_blocks(self) -> None:
        transport = ScriptedTransport().add_text("fast answer")
        agent, _ = _build(transport)

        async def _slow_block(ctx, next_fn) -> None:
            await asyncio.sleep(0.05)
            ctx.block("policy check failed")

        _use(agent, HookEvent.GUARDRAIL_INPUT, _slow_block)

        result = await agent.run(_GOAL)

        assert result.success is False
        assert result.error == "Guardrail blocked: policy check failed"

    async def test_output_guardrail_block_replaces_the_answer_with_a_failure(self) -> None:
        transport = ScriptedTransport().add_text("here is the secret")
        agent, _ = _build(transport)

        async def _block(ctx, next_fn) -> None:
            if "secret" in (ctx.output or ""):
                ctx.block("answer leaks a secret")
                return
            await next_fn()

        _use(agent, HookEvent.GUARDRAIL_OUTPUT, _block)

        result = await agent.run(_GOAL)

        assert result.success is False
        assert result.output is None
        assert result.error == "Output guardrail blocked: answer leaks a secret"


class TestModelFailures:
    async def test_model_error_becomes_a_failed_result_with_the_reason(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("provider returned 503"))
        agent, _ = _build(transport)

        result = await agent.run(_GOAL)

        assert result.success is False
        assert result.error == "LLM call failed: provider returned 503"

    async def test_model_error_while_input_guardrail_is_still_running(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("bad request"))
        agent, _ = _build(transport)
        guard_cancelled = asyncio.Event()

        async def _slow_guard(ctx, next_fn) -> None:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                guard_cancelled.set()
                raise
            await next_fn()

        _use(agent, HookEvent.GUARDRAIL_INPUT, _slow_guard)

        result = await asyncio.wait_for(agent.run(_GOAL), timeout=2.0)

        assert result.error == "LLM call failed: bad request"
        assert guard_cancelled.is_set()


class TestOutputLimitRecovery:
    async def test_cut_off_text_answer_is_asked_to_continue(self) -> None:
        transport = ScriptedTransport()
        transport.add_truncated("Part one of a long answer")
        transport.add_text("and part two.")
        agent, _ = _build(transport)

        result = await agent.run(_GOAL)

        assert result.success is True
        assert len(transport.calls) == 2
        second_call_users = [m for m in transport.calls[1]["messages"] if isinstance(m, UserMessage)]
        assert "cut off at the maximum output-token limit" in second_call_users[-1].content

    async def test_cut_off_tool_call_is_not_executed(self) -> None:
        tool = _NoteTool()
        transport = ScriptedTransport([ScriptedStep(
            message=AssistantMessage(
                tool_calls=[ToolCall(id="n1", name="note", arguments={"text": "half"})], truncated=True,
            ),
            stop_reason=StopReason.MAX_TOKENS,
        )])
        transport.add_text("Done after retrying.")
        agent, _ = _build(transport, tool=tool)

        result = await agent.run(_GOAL)

        assert result.success is True
        assert tool.executed == []
        truncated_turn = agent.scope.turns[0]
        assert truncated_turn.tool_results[0].is_error is True
        assert "Tool call not executed" in truncated_turn.tool_results[0].content


class TestHooksCanAskForAnotherTurn:
    async def test_post_model_recovery_message_vetoes_a_text_only_finish(self) -> None:
        transport = ScriptedTransport().add_text("").add_text("A real answer.")
        agent, _ = _build(transport)

        async def _nudge_empty(ctx, next_fn) -> None:
            if not ctx.tool_calls and not ctx.response.text.strip():
                ctx.recovery_message = UserMessage(content="Please answer the question.", injected=True)
            await next_fn()

        _use(agent, HookEvent.POST_MODEL, _nudge_empty)

        result = await agent.run(_GOAL)

        assert result.success is True
        assert result.output == "A real answer."
        assert len(agent.scope.turns) == 1


class TestPerCallModelOptions:
    async def test_thinking_budget_effort_and_split_system_prompt_reach_the_model(self) -> None:
        transport = _RecordingTransport().add_text("ok")

        class _SplitPrompt:
            def build(self, spec, runtime, goal, todos, extra_sections) -> str:
                return "stable\nvolatile"

            def build_blocks(self, spec, runtime, goal, todos, extra_sections):  # noqa: ANN202
                return "stable", "volatile"

        registry = ToolRegistry()
        transports = TransportRegistry()
        transports.register("scripted", lambda: transport)
        spec = AgentSpec(
            name="options-agent",
            system_prompt=_SplitPrompt(),
            model=ModelSpec(provider="scripted", model="scripted-model", thinking_budget=2048, effort="high"),
            loop=ReActLoop(),
        )
        agent = Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=registry))

        await agent.run(_GOAL)

        assert transport.options == [{"thinking_budget": 2048, "effort": "high", "system_blocks": ["stable", "volatile"]}]
        assert transport.calls[0]["system"] == "stable\nvolatile"

    async def test_token_usage_is_recorded_against_the_budget(self) -> None:
        budget = BudgetTracker(max_input_tokens=10_000)
        usage = TokenUsage(input_tokens=120, output_tokens=30)
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="n1", name="note", arguments={"text": "x"}), usage=usage,
        ).add_text("done", usage=usage)
        agent, _ = _build(transport, budget=budget)

        result = await agent.run(_GOAL)

        snapshot = await budget.snapshot()
        assert result.usage.input_tokens == 240
        assert result.usage.requests == 2
        assert snapshot.input_tokens == 240
        assert snapshot.output_tokens == 60


class TestToolResultFooter:
    async def test_every_turn_of_empty_or_failed_results_raises_the_stale_count(self) -> None:
        class _EmptyTool(_NoteTool):
            async def execute(self, **kwargs: object) -> ToolOutput:
                return ToolOutput(success=True, data="")

        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="a", name="note", arguments={"text": "1"}))
        transport.add_tool_call(ToolCall(id="b", name="note", arguments={"text": "2"}))
        transport.add_text("nothing found")
        agent, _ = _build(transport, tool=_EmptyTool(), max_turns=5)

        await agent.run(_GOAL)

        footers = [
            m.step_footer for m in await agent.context.messages() if getattr(m, "step_footer", None)
        ]
        assert footers == [
            "\n\n[loop: step 1/5, stale_rounds=1]",
            "\n\n[loop: step 2/5, stale_rounds=2]",
        ]

    async def test_a_useful_result_resets_the_stale_count(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="a", name="note", arguments={"text": "found"}))
        transport.add_text("answer")
        agent, _ = _build(transport, max_turns=3)

        await agent.run(_GOAL)

        footers = [m.step_footer for m in await agent.context.messages() if getattr(m, "step_footer", None)]
        assert footers == ["\n\n[loop: step 1/3, stale_rounds=0]"]
