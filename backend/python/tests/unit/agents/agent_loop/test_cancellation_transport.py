"""`CancellationAwareTransport` (`app/agents/agent_loop/cancellation_transport.py`)
-- wraps a fake `LLMTransport` and proves the same race-and-close contract
`LangChainTransport.stream()` already has, on a transport that knows
nothing about `CancellationToken` itself (the `PIPESHUB_AGENT_TRANSPORT=
direct` SDK transports this decorator targets)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import AssistantMessage, TextPart, UserMessage
from app.agent_loop_lib.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
)
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agents.agent_loop.cancellation_transport import (
    CancellationAwareTransport,
    with_cancellation,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema


class _FakeTransport:
    """Minimal `LLMTransport`-shaped fake -- duck-typed rather than
    subclassing the ABC since `CancellationAwareTransport` only calls
    methods on `inner`, never isinstance-checks it.

    `stall` makes `stream()` yield `events` and then hang on an
    `asyncio.Event` that never fires, simulating a provider that stops
    sending chunks mid-response without ending the stream -- the same gap
    `_StallingModel` in `test_langchain_transport.py` covers for the
    LangChain arm."""

    def __init__(
        self,
        events: "list[StreamEvent] | None" = None,
        stall: bool = False,
    ) -> None:
        self._events = events or []
        self._stall = stall
        self.aclose_called = False
        self.complete_called_with: tuple[Any, ...] | None = None
        self.complete_structured_called_with: tuple[Any, ...] | None = None

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: "list[str] | None" = None,
    ) -> ModelResponse:
        self.complete_called_with = (messages, tools, system, model)
        return ModelResponse(message=AssistantMessage(content=[TextPart(text="done")]))

    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        self.complete_structured_called_with = (messages, output_schema, system, model)
        return StructuredResponse(data={"ok": True})

    async def stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: "list[str] | None" = None,
    ) -> "AsyncIterator[StreamEvent]":
        try:
            for event in self._events:
                yield event
            if self._stall:
                await asyncio.Event().wait()  # never set -- indefinite stall
        finally:
            # A real provider stream's teardown runs here on GeneratorExit
            # (delivered by our `.aclose()`) -- this is how we assert that
            # actually happened rather than the generator being abandoned.
            self.aclose_called = True


class TestStreamCancellationDuringProviderStall:
    """The gap a check made only "between chunks" cannot cover: nothing
    else makes the check run again if the provider never sends another
    chunk."""

    async def test_cancel_during_a_stall_stops_the_stream_without_another_chunk(self) -> None:
        token = CancellationToken()
        inner = _FakeTransport([TextDeltaEvent(delta="partial")], stall=True)
        transport = CancellationAwareTransport(inner, token)

        stream = transport.stream([UserMessage(content="hi")])
        first = await anext(stream)  # the one real chunk, before the stall
        token.cancel()  # fired while stream() is stuck on the never-set Event
        final = await anext(stream)

        with pytest.raises(StopAsyncIteration):
            await anext(stream)

        assert isinstance(first, TextDeltaEvent)
        assert isinstance(final, StreamCompleteEvent)
        assert final.response.stop_reason == StopReason.CANCELLED
        assert final.response.message.text == "partial"
        # Truncated tool call under construction (if any) is dropped, not
        # partially reported -- see the decorator's own comment for why.
        assert final.response.message.tool_calls is None

    async def test_cancel_during_a_stall_closes_the_inner_generator(self) -> None:
        """Teardown side of the same scenario: `.aclose()` must actually
        run (not be left to GC) so the provider's underlying connection is
        released promptly."""
        token = CancellationToken()
        inner = _FakeTransport([TextDeltaEvent(delta="partial")], stall=True)
        transport = CancellationAwareTransport(inner, token)

        stream = transport.stream([UserMessage(content="hi")])
        await anext(stream)
        token.cancel()
        async for _ in stream:
            pass

        assert inner.aclose_called is True


class TestStreamWithoutCancellation:
    async def test_normal_stream_is_unaffected_when_token_is_never_cancelled(self) -> None:
        token = CancellationToken()
        complete = StreamCompleteEvent(
            response=ModelResponse(message=AssistantMessage(content=[TextPart(text="hello")])),
        )
        inner = _FakeTransport([TextDeltaEvent(delta="hello"), complete])
        transport = CancellationAwareTransport(inner, token)

        events = [event async for event in transport.stream([UserMessage(content="hi")])]

        assert events == [TextDeltaEvent(delta="hello"), complete]
        # Natural completion still tears the generator down via `.aclose()`
        # in the `finally` -- a no-op since it is already exhausted, but
        # exercised here to prove it does not raise.
        assert inner.aclose_called is True

    async def test_exactly_one_completion_event_when_cancel_races_after_inner_complete(self) -> None:
        """Regression: if cancel() fires after the inner transport's own
        StreamCompleteEvent is yielded, the decorator must emit exactly
        one StreamCompleteEvent total — never a second one from the
        cancellation path."""
        token = CancellationToken()
        complete = StreamCompleteEvent(
            response=ModelResponse(message=AssistantMessage(content=[TextPart(text="done")])),
        )
        inner = _FakeTransport([TextDeltaEvent(delta="done"), complete])
        transport = CancellationAwareTransport(inner, token)

        stream = transport.stream([UserMessage(content="hi")])
        await anext(stream)  # TextDeltaEvent
        token.cancel()  # races with the next iteration
        second = await anext(stream)
        assert isinstance(second, StreamCompleteEvent)

        # Must be exactly one StreamCompleteEvent — no double-emit.
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    async def test_cancel_before_next_iteration_skips_provider_read(self) -> None:
        """cancel() between yields must not start another __anext__()."""
        token = CancellationToken()
        inner = _FakeTransport([TextDeltaEvent(delta="a")], stall=True)
        transport = CancellationAwareTransport(inner, token)

        stream = transport.stream([UserMessage(content="hi")])
        await anext(stream)  # TextDeltaEvent("a")
        token.cancel()
        final = await anext(stream)

        assert isinstance(final, StreamCompleteEvent)
        assert final.response.stop_reason == StopReason.CANCELLED
        assert final.response.message.text == "a"


class TestCompleteAndCompleteStructuredPassThrough:
    """Single unchunked provider calls have no intermediate point to check
    a token mid-call, so both are delegated unchanged -- same reason
    `LangChainTransport` only wires its check into `stream()`."""

    async def test_complete_passes_through_unchanged(self) -> None:
        token = CancellationToken()
        inner = _FakeTransport()
        transport = CancellationAwareTransport(inner, token)

        response = await transport.complete([UserMessage(content="hi")])

        assert response.message.text == "done"
        assert inner.complete_called_with is not None

    async def test_complete_structured_passes_through_unchanged(self) -> None:
        token = CancellationToken()
        inner = _FakeTransport()
        transport = CancellationAwareTransport(inner, token)

        response = await transport.complete_structured(
            [UserMessage(content="hi")], {"type": "object"},
        )

        assert response.data == {"ok": True}
        assert inner.complete_structured_called_with is not None

    async def test_provider_and_model_name_delegate_to_inner(self) -> None:
        token = CancellationToken()
        inner = _FakeTransport()
        transport = CancellationAwareTransport(inner, token)

        assert transport.provider == "fake"
        assert transport.model_name == "fake-model"


class TestWithCancellation:
    def test_returns_transport_unchanged_when_token_is_none(self) -> None:
        inner = _FakeTransport()

        assert with_cancellation(inner, None) is inner

    def test_wraps_transport_when_token_is_provided(self) -> None:
        inner = _FakeTransport()
        token = CancellationToken()

        wrapped = with_cancellation(inner, token)

        assert isinstance(wrapped, CancellationAwareTransport)
