"""`OutputCappedTransport`: stops a streamed turn that will not stop itself.

A provider normally ends a turn at its own output-token limit, but PipesHub
sets that limit for only a few providers, and OpenAI-compatible gateways and
local servers (Ollama, LM Studio, vLLM) may have none. A model stuck repeating
itself inside a tool call's arguments then streams until something else gives
out, with every fragment held in memory for the final message.

The cap counts the characters one turn has streamed -- text, reasoning and
tool-call arguments together -- and ends the stream once they pass it. What
happens next depends on what the model was writing:

* A tool call. The turn is reported exactly as a provider reports one it cut
  off itself (`truncated`, `StopReason.MAX_TOKENS`), so the agent loop's
  existing recovery applies: the call is not run and the model is told its
  reply was too long and to try again.
* Plain text or reasoning only. The loop's recovery for cut-off text is "carry
  on from where you stopped", which needs that text in the next prompt, and
  an overrun this size does not fit in one. There is nothing sound to hand
  back, so the turn fails the way any mid-stream provider failure does and
  the user gets the standard "something went wrong, please try again" answer.

A decorator for the same reason `CancellationAwareTransport` and
`CappedImagesTransport` are: the policy is PipesHub's, and it has to hold for
the LangChain transport and the direct SDK ones alike.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.core.responses import ModelResponse, StopReason, TokenUsage
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.transport.base import LLMTransport
from app.utils.env_utils import env_int

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

logger = logging.getLogger(__name__)

MAX_TURN_OUTPUT_CHARS_ENV_VAR = "PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS"

# The largest output limit any supported provider offers today is about 128k
# tokens, roughly 500k characters, so twice that never touches a turn a
# provider would have completed. Kept no higher because ending a stream costs
# more than its length suggests: LangChain merges everything it received when
# a stream finishes or is closed, re-copying a tool call's arguments once per
# fragment. Measured with five-character fragments, that holds the event loop
# for about 6 s at this size and about 90 s at twice it.
DEFAULT_MAX_TURN_OUTPUT_CHARS = 1_000_000


def max_turn_output_chars() -> int | None:
    """The configured cap in characters, or None when it is switched off (`0`)."""
    value = env_int(MAX_TURN_OUTPUT_CHARS_ENV_VAR, DEFAULT_MAX_TURN_OUTPUT_CHARS, lo=0)
    return value or None


class OutputCappedTransport(LLMTransport):
    """Decorates any `LLMTransport`, ending a `stream()` whose output passes
    `max_chars` and reporting that turn as cut off.

    `complete()`/`complete_structured()` are single provider calls with nothing
    to count until the whole reply has arrived, so both are delegated unchanged.
    """

    def __init__(self, inner: LLMTransport, max_chars: int) -> None:
        self._inner = inner
        self._max_chars = max_chars

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: "list[str] | None" = None,
    ) -> "ModelResponse":
        return await self._inner.complete(
            messages, tools, system, model, thinking_budget, effort, system_blocks,
        )

    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse":
        return await self._inner.complete_structured(
            messages, output_schema, system, model,
        )

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
        stream_iter = self._inner.stream(
            messages, tools, system, model, thinking_budget, effort, system_blocks,
        ).__aiter__()
        streamed = 0
        # Insertion-ordered by first appearance, which is the order the model
        # made the calls in.
        calls: dict[int, dict[str, str | None]] = {}
        try:
            async for event in stream_iter:
                if isinstance(event, StreamCompleteEvent):
                    yield event
                    return
                if isinstance(event, (TextDeltaEvent, ThinkingDeltaEvent)):
                    streamed += len(event.delta)
                elif isinstance(event, ToolCallDeltaEvent):
                    streamed += len(event.arguments_delta)
                    call = calls.setdefault(event.index, {"id": None, "name": None})
                    call["id"] = call["id"] or event.id
                    call["name"] = call["name"] or event.name
                yield event
                if streamed > self._max_chars:
                    yield StreamCompleteEvent(response=self._cut_off_response(calls, model))
                    return
        finally:
            # Explicit close, not left to GC: this is what stops the provider
            # generating (and billing for) output nobody will read.
            with contextlib.suppress(BaseException):
                await stream_iter.aclose()

    def _cut_off_response(
        self, calls: dict[int, dict[str, str | None]], model: str | None,
    ) -> ModelResponse:
        """The cut-off turn to hand the agent loop, or a `TransportError` when
        the turn held no tool call for the loop to answer."""
        model_name = model or self._inner.model_name
        logger.warning(
            "Model %s streamed more than %d characters in one turn without "
            "finishing, so the stream was stopped. Raise %s if replies this "
            "long are expected; 0 turns the limit off.",
            model_name or "?", self._max_chars, MAX_TURN_OUTPUT_CHARS_ENV_VAR,
        )
        tool_calls = [
            ToolCall(id=call["id"] or f"call_{index}", name=call["name"])
            for index, call in calls.items()
            if call["name"]
        ]
        if not tool_calls:
            # No number or model name in the text: the user-facing error is
            # chosen by matching this string, and "500" or "429" inside a
            # limit or a model name would pick the wrong one.
            raise TransportError(
                "The model kept writing past the per-turn output cap without "
                "finishing its reply.",
                retryable=False,
            )
        # Each call keeps its name and id so the loop can answer it with its
        # "not executed, your reply was cut off" note. Its arguments are
        # dropped: incomplete, never going to run, and most of what would make
        # this turn too large to send back to the model. Text that preceded the
        # call is dropped for the same reason; it was narration, already shown.
        return ModelResponse(
            message=AssistantMessage(tool_calls=tool_calls, truncated=True),
            usage=TokenUsage(),
            stop_reason=StopReason.MAX_TOKENS,
            model=model_name,
        )


def with_output_cap(transport: LLMTransport, max_chars: int | None) -> LLMTransport:
    """`transport` with the per-turn output cap applied, or unchanged when the
    cap is switched off."""
    if max_chars is None:
        return transport
    return OutputCappedTransport(transport, max_chars)


__all__ = [
    "DEFAULT_MAX_TURN_OUTPUT_CHARS",
    "MAX_TURN_OUTPUT_CHARS_ENV_VAR",
    "OutputCappedTransport",
    "max_turn_output_chars",
    "with_output_cap",
]
