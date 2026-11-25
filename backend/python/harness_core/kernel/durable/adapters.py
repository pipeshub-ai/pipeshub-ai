"""Adapters from the kernel's existing abstractions to the durable ports."""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any, override

from harness_core import bindings
from harness_core.kernel.core.json_stream import AnswerArgumentStreams
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ThinkingPart
from harness_core.kernel.core.responses import ModelResponse
from harness_core.kernel.core.streaming import (
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.kernel.durable.ports import ModelFailed, SampledTurn
from harness_core.models.stream_watch import (
    TIMEOUT,
    CallClock,
    CallTiming,
    ModelStalled,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.kernel.durable.ports import DeltaKind, ModelCall
    from harness_core.models.base import Model


class TransportModelPort:
    """``DurableModelPort`` over a ``harness_core.models`` ``Model``.

    ``raw`` is the transport's full ``AssistantMessage`` (thinking parts included),
    so a resume on the same provider and transport sends it back verbatim. A
    transport that exposes richer native state (signatures, encrypted reasoning
    items) should subclass and override ``raw_state``/``restore``.
    """

    def __init__(
        self,
        model: Model,
        *,
        provider: str,
        transport: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self.provider = provider
        self.transport = transport
        self._clock = clock

    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        watch = CallClock(self._clock)
        try:
            response = await self._model.complete(
                call.messages, tools=call.tools, system=call.system, **layout(call, self._model)
            )
        except ModelStalled as exc:
            raise ModelFailed(TIMEOUT, str(exc)) from exc
        watch.event()
        if response.message.text:
            await on_delta("text_delta", response.message.text)
        return self._sampled(response, watch.timing())

    def _sampled(self, response: ModelResponse, timing: CallTiming | None = None) -> SampledTurn:
        return SampledTurn(
            message=response.message,
            usage=response.usage,
            stop_reason=response.stop_reason,
            provider=self.provider,
            transport=self.transport,
            model=response.model or self._model.model_name,
            raw=self.raw_state(response.message),
            timing=timing,
        )

    def raw_state(self, message: AssistantMessage) -> bytes:
        return message.model_dump_json().encode("utf-8")

    def restore(
        self, raw: bytes, *, provider: str, transport: str
    ) -> AssistantMessage | None:
        if (provider, transport) != (self.provider, self.transport):
            return None
        return AssistantMessage.model_validate_json(raw)


class StreamingModelPort(TransportModelPort):
    """``TransportModelPort`` that streams: token and reasoning deltas reach the
    live bus as the provider produces them, and so does the answer argument of
    a tool that delivers the answer (``final_answer``, through the run's
    ``streamed_answer_argument`` binding), as legacy streams it; the journaled
    message is the stream's assembled response, the same one ``complete()``
    would return."""

    @override
    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        try:
            return await self._streamed(call, on_delta)
        except ModelStalled as exc:
            # The kernel retries it as a new model attempt, whatever this one showed (P4.9).
            raise ModelFailed(TIMEOUT, str(exc)) from exc

    async def _streamed(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        watch = CallClock(self._clock)
        stream = self._model.stream(
            call.messages, tools=call.tools, system=call.system, **layout(call, self._model)
        )
        reasoning: list[str] = []
        answers = AnswerArgumentStreams(bindings.current().streamed_answer_argument)
        announced = False
        # Closed as soon as the answer is in, not when the stream is collected: what the call holds until its stream
        # ends (a provider slot, a reservation) goes back before the turn moves on (G-45).
        async with _closing(stream):
            async for event in stream:
                watch.event()
                if isinstance(event, TextDeltaEvent):
                    if event.delta:
                        await on_delta("text_delta", event.delta)
                elif isinstance(event, ToolCallDeltaEvent):
                    decoded = answers.feed(event)
                    if decoded:
                        await on_delta("text_delta", decoded)
                    elif decoded is None and not announced:
                        # Legacy re-asserts "calling the model" once, on the first
                        # argument delta of a call that is not the answer.
                        announced = True
                        await on_delta("tool_call", TOOL_ARGUMENTS_STARTED)
                elif isinstance(event, ThinkingDeltaEvent):
                    if event.delta:
                        reasoning.append(event.delta)
                        await on_delta("reasoning_delta", event.delta)
                else:
                    return self._sampled(_with_reasoning(event.response, "".join(reasoning)), watch.timing())
        raise RuntimeError("model stream ended without a complete event")


def layout(call: ModelCall, model: Model) -> dict[str, Any]:
    """The compiled request's band edges (P4.1), passed only when the compiler set them, so
    a request assembled as before reaches the model exactly as before. Cache marks go only
    to a model that takes them."""
    kwargs: dict[str, Any] = {}
    if call.system_blocks:
        kwargs["system_blocks"] = call.system_blocks
    if call.cache_marks is not None and getattr(model, "accepts_cache_marks", False):
        kwargs["cache_marks"] = call.cache_marks
    if call.tool_choice is not None:
        kwargs["tool_choice"] = call.tool_choice
    if call.max_output_tokens is not None:
        kwargs["max_output_tokens"] = call.max_output_tokens
    return kwargs


# The ``tool_call`` delta's text: its arrival is the signal, it carries no arguments.
TOOL_ARGUMENTS_STARTED = "arguments"


def _with_reasoning(response: ModelResponse, streamed: str) -> ModelResponse:
    """The response carries the reasoning it streamed, even when the transport
    leaves it out of the assembled message."""
    message = response.message
    content = message.content
    if not streamed or (
        not isinstance(content, str) and any(isinstance(p, ThinkingPart) for p in content)
    ):
        return response
    parts = [TextPart(text=content)] if isinstance(content, str) else list(content)
    return response.model_copy(
        update={"message": message.model_copy(update={"content": [ThinkingPart(thinking=streamed), *parts]})}
    )



@contextlib.asynccontextmanager
async def _closing(stream: object) -> AsyncIterator[None]:
    """Closes ``stream`` when the block ends, if it can be closed (a test double's iterator may not)."""
    try:
        yield
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()

__all__ = ["TOOL_ARGUMENTS_STARTED", "StreamingModelPort", "TransportModelPort", "layout"]
