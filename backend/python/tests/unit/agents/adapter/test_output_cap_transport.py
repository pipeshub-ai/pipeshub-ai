"""`OutputCappedTransport` (`app/agents/agent_loop/output_cap_transport.py`):
a streamed turn that never ends is stopped at the per-turn output cap. A
runaway tool call is handled as a reply the provider cut off, so the model
tries again; runaway text ends the answer with the standard error. A normal
long answer passes through untouched."""

from __future__ import annotations

import inspect
import itertools
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessageChunk

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import ModelResponse, StopReason, TokenUsage
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop import factory
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.output_cap_transport import (
    DEFAULT_MAX_TURN_OUTPUT_CHARS,
    MAX_TURN_OUTPUT_CHARS_ENV_VAR,
    OutputCappedTransport,
    max_turn_output_chars,
    with_output_cap,
)
from tests.unit.agent_loop_lib.agent.test_agent_step_outcomes import _NoteTool
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator

    from app.agent_loop_lib.core.tool_schema import ToolSchema

_FRAGMENT = "word "
_MESSAGES: list[Message] = [UserMessage(content="hi")]


def _endless_arguments(index: int = 0) -> "Iterator[StreamEvent]":
    return (ToolCallDeltaEvent(index=index, arguments_delta=_FRAGMENT) for _ in itertools.count())


def _complete(text: str = "done") -> StreamCompleteEvent:
    return StreamCompleteEvent(response=ModelResponse(
        message=AssistantMessage(content=text), usage=TokenUsage(input_tokens=3, output_tokens=5),
        stop_reason=StopReason.END_TURN, model="scripted-model",
    ))


class _Streams(ScriptedTransport):
    """Replays one (possibly endless) run of stream events per model call, and
    records how much of each the caller pulled and whether it closed it."""

    def __init__(self, *turns: "Iterable[StreamEvent]") -> None:
        super().__init__()
        self._turns = list(turns)
        self.pulled = 0
        self.closed = 0

    async def stream(
        self,
        messages: list[Message],
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> "AsyncIterator[StreamEvent]":
        self.calls.append({"messages": list(messages)})
        try:
            for event in self._turns.pop(0):
                self.pulled += 1
                yield event
        finally:
            self.closed += 1


async def _events(transport: OutputCappedTransport | Any) -> list[StreamEvent]:  # noqa: ANN401
    return [event async for event in transport.stream(_MESSAGES)]


class TestNormalRepliesAreUntouched:
    async def test_a_long_answer_under_the_cap_passes_through_unchanged(self) -> None:
        """200,000 characters is several times a long real answer, and a fifth
        of the default cap."""
        script = [TextDeltaEvent(delta=_FRAGMENT) for _ in range(40_000)]
        script.append(_complete(_FRAGMENT * 40_000))
        inner = _Streams(script)

        events = await _events(with_output_cap(inner, DEFAULT_MAX_TURN_OUTPUT_CHARS))

        assert len(events) == len(script)
        assert all(got is sent for got, sent in zip(events, script, strict=True))
        assert events[-1].response.message.truncated is False
        assert events[-1].response.usage.output_tokens == 5

    async def test_a_long_tool_call_under_the_cap_passes_through_unchanged(self) -> None:
        script: list[StreamEvent] = [
            ToolCallDeltaEvent(index=0, id="call_1", name="final_answer", arguments_delta=""),
            *itertools.islice(_endless_arguments(), 40_000),
            _complete(),
        ]

        events = await _events(with_output_cap(_Streams(script), DEFAULT_MAX_TURN_OUTPUT_CHARS))

        assert all(got is sent for got, sent in zip(events, script, strict=True))

    async def test_a_reply_exactly_at_the_cap_is_not_cut(self) -> None:
        script = [TextDeltaEvent(delta="x" * 100), _complete("x" * 100)]

        events = await _events(with_output_cap(_Streams(script), 100))

        assert events[-1] is script[-1]

    async def test_identity_and_single_shot_calls_are_the_wrapped_transports(self) -> None:
        inner = ScriptedTransport().add_text("whole reply")
        capped = with_output_cap(inner, 10)

        response = await capped.complete(_MESSAGES)
        await capped.complete_structured(_MESSAGES, {"type": "object"})

        assert (capped.provider, capped.model_name) == ("scripted", "scripted-model")
        assert response.message.text == "whole reply"
        assert len(inner.calls) == 1


class TestRunawayToolCallIsStopped:
    async def test_stream_is_stopped_closed_and_reported_as_cut_off(self) -> None:
        inner = _Streams(itertools.chain(
            [ToolCallDeltaEvent(index=0, id="call_1", name="final_answer", arguments_delta="")],
            _endless_arguments(),
        ))

        events = await _events(with_output_cap(inner, 1_000))

        final = events[-1]
        assert isinstance(final, StreamCompleteEvent)
        assert final.response.stop_reason == StopReason.MAX_TOKENS
        assert final.response.message.truncated is True
        assert final.response.message.tool_calls == [ToolCall(id="call_1", name="final_answer")]
        assert final.response.usage == TokenUsage()
        assert final.response.model == "scripted-model"
        # One fragment past the cap, then nothing more is read from the provider.
        assert inner.pulled == 1 + 1_000 // len(_FRAGMENT) + 1
        assert len(events) == inner.pulled + 1
        assert inner.closed == 1

    async def test_nothing_of_the_overrun_is_sent_back_to_the_model(self) -> None:
        """Neither the unfinished arguments nor the text before the call: at
        the default cap either one is larger than a prompt can hold."""
        inner = _Streams(itertools.chain(
            [
                TextDeltaEvent(delta="Here is "),
                TextDeltaEvent(delta="the answer."),
                ToolCallDeltaEvent(index=0, id="call_1", name="final_answer", arguments_delta='{"a": "'),
            ],
            _endless_arguments(),
        ))

        final = (await _events(with_output_cap(inner, 500)))[-1]

        assert final.response.message.content == []
        assert final.response.message.tool_calls == [ToolCall(id="call_1", name="final_answer")]

    async def test_every_call_in_the_turn_is_kept_in_the_order_it_was_made(self) -> None:
        inner = _Streams(itertools.chain(
            [
                ToolCallDeltaEvent(index=0, id="call_a", name="search", arguments_delta='{"query": "cats"}'),
                ToolCallDeltaEvent(index=1, id=None, name="note", arguments_delta=""),
                ToolCallDeltaEvent(index=2, id="call_c", name=None, arguments_delta=""),
            ],
            _endless_arguments(index=1),
        ))

        final = (await _events(with_output_cap(inner, 300)))[-1]

        # A call that never got an id is given a positional one; a call that
        # never got a name cannot be answered and is left out.
        assert final.response.message.tool_calls == [
            ToolCall(id="call_a", name="search"), ToolCall(id="call_1", name="note"),
        ]


class TestRunawayTextEndsTheAnswer:
    """Cut-off text is recovered by asking the model to carry on from it, which
    needs that text in the next prompt. An overrun does not fit in one, so
    there is no tool call to answer and nothing to continue."""

    @pytest.mark.parametrize("event_type", [TextDeltaEvent, ThinkingDeltaEvent])
    async def test_stream_is_closed_and_the_turn_fails_without_a_retry(
        self, event_type: type[TextDeltaEvent] | type[ThinkingDeltaEvent],
    ) -> None:
        inner = _Streams(event_type(delta=_FRAGMENT) for _ in itertools.count())
        capped = with_output_cap(inner, 100)
        events: list[StreamEvent] = []

        with pytest.raises(TransportError) as raised:
            async for event in capped.stream(_MESSAGES):
                events.append(event)  # noqa: PERF401

        assert raised.value.retryable is False
        assert len(events) == inner.pulled == 21
        assert not any(isinstance(event, StreamCompleteEvent) for event in events)
        assert inner.closed == 1

    @pytest.mark.parametrize("cap", [100, 429, 500, 500_000, 1_000_000])
    async def test_user_sees_the_standard_plain_language_error(self, cap: int) -> None:
        """Whatever the cap is set to: a number in the error text could match
        the "500"/"429" hints and tell the user the provider is down."""
        inner = _Streams(TextDeltaEvent(delta="x" * (cap + 1)) for _ in itertools.count())

        with pytest.raises(TransportError) as raised:
            await _events(with_output_cap(inner, cap))

        code, message = classify_error(f"LLM call failed: {raised.value}")
        assert code == "unknown"
        assert message.startswith("Something went wrong while answering. Please try again")
        assert str(raised.value) not in message

    async def test_a_call_that_never_got_a_name_cannot_be_retried_either(self) -> None:
        inner = _Streams(itertools.chain(
            [ToolCallDeltaEvent(index=0, id="call_1", name=None, arguments_delta="")],
            _endless_arguments(),
        ))

        with pytest.raises(TransportError):
            await _events(with_output_cap(inner, 100))

        assert inner.closed == 1


class TestConfiguration:
    def test_default_is_one_million_characters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(MAX_TURN_OUTPUT_CHARS_ENV_VAR, raising=False)

        assert max_turn_output_chars() == 1_000_000

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("250000", 250_000), ("0", None), ("-5", None), ("", 1_000_000), ("lots", 1_000_000)],
    )
    def test_env_override(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int | None,
    ) -> None:
        monkeypatch.setenv(MAX_TURN_OUTPUT_CHARS_ENV_VAR, raw)

        assert max_turn_output_chars() == expected

    def test_switched_off_leaves_the_transport_unwrapped(self) -> None:
        inner = ScriptedTransport()

        assert with_output_cap(inner, None) is inner
        assert isinstance(with_output_cap(inner, 10), OutputCappedTransport)

    def test_every_transport_the_factory_registers_is_capped(self) -> None:
        """Both registrations, and both arms of the direct one: a provider with
        no direct transport falls back to LangChain and must not lose the cap."""
        source = inspect.getsource(factory.PipesHubAgentFactory.create)
        langchain_arm = source[source.index('"langchain",'):source.index("def _direct_or_langchain")]
        direct_arm = source[source.index("def _direct_or_langchain"):]
        direct_arm = direct_arm[: direct_arm.index("transport_registry.register(")]

        assert "output_cap = max_turn_output_chars()" in source
        assert langchain_arm.count("with_output_cap(") == 1
        assert "with_output_cap(with_image_cap(direct, image_cap), output_cap)" in direct_arm
        assert direct_arm.count("with_output_cap(") == 2


class _EndlessModel:
    """A LangChain chat model whose tool call never closes."""

    def __init__(self) -> None:
        self.closed = False

    def bind_tools(self, tools: list[Any]) -> "_EndlessModel":
        return self

    async def astream(self, messages: list, config: dict | None = None) -> "AsyncIterator[AIMessageChunk]":
        def piece(args: str, name: str | None = None, call_id: str | None = None) -> AIMessageChunk:
            return AIMessageChunk(
                content="", tool_call_chunks=[{"name": name, "args": args, "id": call_id, "index": 0}],
            )

        try:
            yield piece("", name="final_answer", call_id="call_1")
            yield piece('{"answer_markdown": "')
            while True:
                yield piece(_FRAGMENT)
        finally:
            self.closed = True


class TestOverTheLangChainTransport:
    async def test_runaway_tool_call_is_cut_off_and_the_provider_stream_closed(self) -> None:
        model = _EndlessModel()
        capped = with_output_cap(LangChainTransport(model, model_name="model-x"), 2_000)

        events = await _events(capped)

        final = events[-1]
        assert isinstance(final, StreamCompleteEvent)
        assert final.response.message.truncated is True
        assert final.response.stop_reason == StopReason.MAX_TOKENS
        assert final.response.message.tool_calls == [ToolCall(id="call_1", name="final_answer")]
        assert final.response.model == "model-x"
        assert model.closed is True


class TestAgentRecoversFromACappedTurn:
    async def test_the_call_is_not_run_and_the_model_gets_to_try_again(self) -> None:
        tool = _NoteTool()
        inner = _Streams(
            itertools.chain(
                [ToolCallDeltaEvent(index=0, id="n1", name="note", arguments_delta='{"text": "')],
                _endless_arguments(),
            ),
            [TextDeltaEvent(delta="Short answer."), _complete("Short answer.")],
        )
        registry = ToolRegistry()
        registry.register_tool(tool)
        transports = TransportRegistry()
        transports.register("scripted", lambda: with_output_cap(inner, 1_000))
        agent = Agent(
            AgentSpec(
                name="capped-agent",
                system_prompt="You are a helpful assistant.",
                model=ModelSpec(provider="scripted", model="scripted-model"),
                loop=ReActLoop(),
                max_turns=4,
            ),
            AgentRuntime(transport_registry=transports, tool_registry=registry),
        )

        _ = [event async for event in agent.stream(Goal(description="Take a note"))]
        result = agent.last_stream_result

        assert result.success is True
        assert result.output == "Short answer."
        assert result.error is None
        assert tool.executed == []
        assert len(inner.calls) == 2
        retry_messages = inner.calls[1]["messages"]
        [capped_turn] = [m for m in retry_messages if isinstance(m, AssistantMessage)]
        assert capped_turn.tool_calls == [ToolCall(id="n1", name="note")]
        [note] = [m for m in retry_messages if isinstance(m, ToolMessage)]
        assert note.tool_call_id == "n1"
        assert "Tool call not executed" in note.text

    async def test_runaway_text_ends_the_run_without_reaching_the_prompt_or_the_answer(self) -> None:
        inner = _Streams(
            (TextDeltaEvent(delta=_FRAGMENT) for _ in itertools.count()),
            [TextDeltaEvent(delta="never asked for"), _complete("never asked for")],
        )
        transports = TransportRegistry()
        transports.register("scripted", lambda: with_output_cap(inner, 1_000))
        agent = Agent(
            AgentSpec(
                name="capped-agent",
                system_prompt="You are a helpful assistant.",
                model=ModelSpec(provider="scripted", model="scripted-model"),
                loop=ReActLoop(),
                max_turns=4,
            ),
            AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry()),
        )

        _ = [event async for event in agent.stream(Goal(description="Summarise the quarter"))]
        result = agent.last_stream_result

        assert result.success is False
        assert len(inner.calls) == 1
        assert _FRAGMENT not in (result.output or "")
        assert classify_error(result.error)[0] == "unknown"
        assert _FRAGMENT not in classify_error(result.error)[1]
