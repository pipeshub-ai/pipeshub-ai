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
import logging
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.exceptions import TransportError
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
from app.utils.logger import log_record_fields
from tests.unit.agents.adapter.support.provider_stall import (
    ProviderStall,
    pending_since,
)
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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

    async def test_a_call_skipped_by_stop_still_has_its_end_line(self, caplog: pytest.LogCaptureFixture) -> None:
        token = CancellationToken()
        transport = ScriptedTransport().add_tool_call(ToolCall(id="n1", name="note", arguments={"text": "a"}))
        agent, _ = _build(transport, tool=_NoteTool(), cancellation_token=token)

        async def _cancel_after_model(ctx, next_fn) -> None:
            token.cancel()
            await next_fn()

        _use(agent, HookEvent.POST_MODEL, _cancel_after_model)
        with caplog.at_level(logging.DEBUG):
            await agent.run(_GOAL)

        (end,) = [r for r in caplog.records if r.getMessage() == "tool_call.end"]
        assert end.levelno == logging.INFO
        assert log_record_fields(end) == {
            "outcome": "skipped", "reason": "cancelled_before_execution", "error_class": "cancelled",
            "tool": "note", "tool_call_id": "n1",
        }


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


class _StallingTransport(ScriptedTransport):
    """A model call that never answers: its stream reads a provider that stalls until cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.stall = ProviderStall()

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:  # noqa: ANN401
        await self.stall.read()
        yield  # pragma: no cover - marks this as an async generator


async def _cancel_streaming_run_once_started(agent: Agent, *started: asyncio.Event) -> None:
    """Drive `agent.stream()` as the agent route does, and cancel it once every `started` event
    is set (a client disconnect)."""
    async def consume() -> None:
        async for _ in agent.stream(_GOAL):
            pass

    consumer = asyncio.create_task(consume())
    for event in started:
        await event.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer


class TestCancelledTurn:
    """Cancelling the task that runs a turn (a client disconnect) stops its model call: no task
    of the run keeps reading the provider after the turn is gone."""

    async def test_the_model_call_is_cancelled_with_the_turn(self) -> None:
        transport = _StallingTransport()
        agent, _ = _build(transport)
        before = asyncio.all_tasks()

        await _cancel_streaming_run_once_started(agent, transport.stall.started)
        await transport.stall.settle()

        assert (transport.stall.cancels, transport.stall.closed) == (1, True)
        assert await pending_since(before) == []

    async def test_a_running_input_guardrail_is_cancelled_too(self) -> None:
        transport = _StallingTransport()
        agent, _ = _build(transport)
        guard = ProviderStall()

        async def _slow_guard(ctx, next_fn) -> None:
            await guard.read()

        _use(agent, HookEvent.GUARDRAIL_INPUT, _slow_guard)
        before = asyncio.all_tasks()

        await _cancel_streaming_run_once_started(agent, transport.stall.started, guard.started)
        await guard.settle()
        await transport.stall.settle()

        assert guard.cancels == 1 and transport.stall.cancels == 1
        assert await pending_since(before) == []


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
                return "stable\n\nvolatile"

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
        assert transport.calls[0]["system"] == "stable\n\nvolatile"

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


_NARRATION = "I'll look this up in the weekly notes and then write the summary for you."
_LIB_LOGGER = "app.agent_loop_lib.agent"


def _failed_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "llm_call.failed"]


async def _deny_async(ctx, next_fn) -> None:
    ctx.deny("refused")


async def _block_async(ctx, next_fn) -> None:
    ctx.block("blocked")


def _tool_turn(narration: str) -> ScriptedTransport:
    return ScriptedTransport().add_text_and_tool_call(
        narration, ToolCall(id="n1", name="note", arguments={"text": "a"}),
    )


class TestEveryExitNamesItsEndEvent:
    """`AgentResult.end_event` is the lib's own name for the exit taken: the turn's log line maps it to a stop reason."""

    async def test_a_plain_answer(self) -> None:
        agent, _ = _build(ScriptedTransport().add_text("Nothing changed."))
        result = await agent.run(_GOAL)
        assert (result.success, result.end_event, result.error_class) == (True, "agent_complete", None)

    async def test_the_wrap_up_answer_after_max_turns(self) -> None:
        agent, _ = _build(_tool_turn(_NARRATION).add_text("Three notes changed."), max_turns=1)
        result = await agent.run(_GOAL)
        assert (result.success, result.end_event) == (True, "agent_complete_after_max_turns")

    async def test_the_partial_answer_after_max_turns(self) -> None:
        transport = _tool_turn(_NARRATION).add_error(TransportError("busy", status_code=503, retryable=True))
        agent, _ = _build(transport, max_turns=1)
        result = await agent.run(_GOAL)
        assert (result.success, result.end_event) == (True, "agent_complete_partial")

    async def test_max_turns_with_nothing_to_return(self) -> None:
        transport = _tool_turn("Checking.").add_error(TransportError("busy", status_code=503, retryable=True))
        agent, _ = _build(transport, max_turns=1)
        result = await agent.run(_GOAL)
        assert (result.success, result.end_event) == (False, "agent_failed")

    async def test_a_model_failure(self) -> None:
        agent, _ = _build(ScriptedTransport().add_error(RuntimeError("transport bug")))
        result = await agent.run(_GOAL)
        assert (result.end_event, result.error_class) == ("llm_call_failed", "internal")
        assert result.error == "LLM call failed: transport bug"

    @pytest.mark.parametrize(("event", "middleware", "end_event"), [
        (HookEvent.PRE_AGENT, _deny_async, "agent_blocked"),
        (HookEvent.PRE_TURN, _deny_async, "turn_blocked"),
        (HookEvent.GUARDRAIL_INPUT, _block_async, "guardrail_blocked"),
        (HookEvent.GUARDRAIL_OUTPUT, _block_async, "guardrail_blocked"),
    ])
    async def test_a_hook_refusal(self, event: HookEvent, middleware: Any, end_event: str) -> None:  # noqa: ANN401
        agent, _ = _build(ScriptedTransport().add_text("answer"))
        _use(agent, event, middleware)
        result = await agent.run(_GOAL)
        assert (result.success, result.end_event) == (False, end_event)

    async def test_stop_pressed(self) -> None:
        token = CancellationToken()
        token.cancel()
        agent, _ = _build(ScriptedTransport(), cancellation_token=token)
        result = await agent.run(_GOAL)
        assert (result.cancelled, result.end_event) == (True, "agent_cancelled")


class TestModelFailureIsLoggedOnce:
    async def test_our_bug_is_one_error_with_its_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        agent, _ = _build(ScriptedTransport().add_error(RuntimeError("transport bug")))
        with caplog.at_level(logging.DEBUG, logger=_LIB_LOGGER):
            await agent.run(_GOAL)

        [record] = _failed_lines(caplog)
        assert record.levelno == logging.ERROR
        assert isinstance(record.exc_info[1], RuntimeError)
        fields = log_record_fields(record)
        assert (fields["error_class"], fields["error_type"], fields["turn"]) == ("internal", "RuntimeError", 0)

    async def test_a_rate_limit_is_a_warning_without_a_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        agent, _ = _build(ScriptedTransport().add_error(TransportError("slow down", status_code=429, retryable=True)))
        with caplog.at_level(logging.DEBUG, logger=_LIB_LOGGER):
            result = await agent.run(_GOAL)

        [record] = _failed_lines(caplog)
        assert (record.levelno, record.exc_info) == (logging.WARNING, None)
        assert log_record_fields(record)["error_class"] == "rate_limited" == result.error_class

    async def test_a_refused_provider_key_is_misconfigured(self, caplog: pytest.LogCaptureFixture) -> None:
        agent, _ = _build(ScriptedTransport().add_error(TransportError("invalid api key", status_code=401)))
        with caplog.at_level(logging.DEBUG, logger=_LIB_LOGGER):
            result = await agent.run(_GOAL)

        [record] = _failed_lines(caplog)
        assert record.levelno == logging.ERROR
        assert log_record_fields(record)["error_class"] == "misconfigured" == result.error_class

    async def test_a_failed_wrap_up_turn_is_logged_and_the_partial_answer_kept(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        transport = _tool_turn(_NARRATION).add_error(TransportError("busy", status_code=503, retryable=True))
        agent, _ = _build(transport, max_turns=1)
        with caplog.at_level(logging.DEBUG, logger=_LIB_LOGGER):
            result = await agent.run(_GOAL)

        [record] = _failed_lines(caplog)
        assert record.levelno == logging.WARNING
        assert log_record_fields(record)["turn"] == 1
        assert result.success is True and result.output == _NARRATION

    async def test_a_guardrail_block_is_not_a_model_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        agent, _ = _build(ScriptedTransport().add_text("answer"))
        _use(agent, HookEvent.GUARDRAIL_INPUT, _block_async)
        with caplog.at_level(logging.DEBUG, logger=_LIB_LOGGER):
            await agent.run(_GOAL)

        assert _failed_lines(caplog) == []
