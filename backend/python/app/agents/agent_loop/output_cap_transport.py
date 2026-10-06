"""`OutputCappedTransport`: stops a streamed turn that will not stop itself.

A provider normally ends a turn at its own output-token limit, but PipesHub
sets that limit for only a few providers, and OpenAI-compatible gateways and
local servers (Ollama, LM Studio, vLLM) may have none. A model stuck repeating
itself inside a tool call's arguments then streams until something else gives
out, with every fragment held in memory for the final message.

The cap counts the characters one turn has streamed -- text, reasoning and
tool-call arguments together -- and ends the stream once they pass it. Where
it can, the turn is then reported exactly as a provider reports one it cut off
itself (`truncated`, `StopReason.MAX_TOKENS`), so the agent loop's existing
recovery applies:

* A tool call was in progress: the call is not run and the model is told its
  reply was too long and to try again. The call keeps its name and id only.
* Only text, short enough to send back: the model is asked to carry on from
  where it stopped. This is the case when an operator sets a small cap.

That recovery puts the cut-off text into the next prompt and joins it onto
the final answer, so it cannot be used for text too long to fit in a prompt --
which, at the default cap, is every text overrun. That text is dropped and the
turn's reply becomes `OUTPUT_CAP_NOTICE`, a short plain-language answer that
says what happened and what to do. It is an ordinary finished reply, not a
cut-off one, so nothing is continued from it or joined to it.

A decorator for the same reason `CancellationAwareTransport` and
`CappedImagesTransport` are: the policy is PipesHub's, and it has to hold for
the LangChain transport and the direct SDK ones alike.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, TextPart, ToolCall
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


# Shown as the answer, so it follows the rules for anything a user reads: what
# happened, in their terms, and what to do next.
OUTPUT_CAP_NOTICE = (
    "My answer grew far too long and I had to stop before finishing it. "
    "Please ask again, or ask for a shorter answer."
)


def max_turn_output_chars() -> int | None:
    """The configured cap in characters, or None when it is switched off (`0`)."""
    value = env_int(MAX_TURN_OUTPUT_CHARS_ENV_VAR, DEFAULT_MAX_TURN_OUTPUT_CHARS, lo=0)
    return value or None


def _fits_in_next_prompt(text: str, model: str | None) -> bool:
    """Whether cut-off `text` is small enough to go back to `model` with the
    conversation that produced it: a quarter of the prompt budget, at about
    four characters a token."""
    return len(text) <= ContextBudget.for_model(model).effective_max_tokens


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
        text_parts: list[str] = []
        # Insertion-ordered by first appearance, which is the order the model
        # made the calls in.
        calls: dict[int, dict[str, str | None]] = {}
        try:
            async for event in stream_iter:
                if isinstance(event, StreamCompleteEvent):
                    yield event
                    return
                if isinstance(event, TextDeltaEvent):
                    streamed += len(event.delta)
                    text_parts.append(event.delta)
                elif isinstance(event, ThinkingDeltaEvent):
                    streamed += len(event.delta)
                elif isinstance(event, ToolCallDeltaEvent):
                    streamed += len(event.arguments_delta)
                    call = calls.setdefault(event.index, {"id": None, "name": None})
                    call["id"] = call["id"] or event.id
                    call["name"] = call["name"] or event.name
                yield event
                if streamed > self._max_chars:
                    yield StreamCompleteEvent(
                        response=self._stopped_response("".join(text_parts), calls, model),
                    )
                    return
        finally:
            # Explicit close, not left to GC: this is what stops the provider
            # generating (and billing for) output nobody will read.
            with contextlib.suppress(BaseException):
                await stream_iter.aclose()

    def _stopped_response(
        self, text: str, calls: dict[int, dict[str, str | None]], model: str | None,
    ) -> ModelResponse:
        model_name = model or self._inner.model_name
        logger.warning(
            "Model %s streamed more than %d characters in one turn without "
            "finishing, so the stream was stopped. Raise %s if replies this "
            "long are expected; 0 turns the limit off.",
            model_name or "?", self._max_chars, MAX_TURN_OUTPUT_CHARS_ENV_VAR,
        )
        # Each call keeps its name and id so the loop can answer it with its
        # "not executed, your reply was cut off" note. Its arguments are
        # dropped: incomplete, never going to run, and most of what would make
        # this turn too large to send back to the model.
        tool_calls = [
            ToolCall(id=call["id"] or f"call_{index}", name=call["name"])
            for index, call in calls.items()
            if call["name"]
        ]
        kept_text = text if _fits_in_next_prompt(text, model_name) else ""
        if tool_calls or kept_text.strip():
            message = AssistantMessage(
                content=[TextPart(text=kept_text)] if kept_text else [],
                tool_calls=tool_calls or None,
                truncated=True,
            )
            stop_reason = StopReason.MAX_TOKENS
        else:
            message = AssistantMessage(content=OUTPUT_CAP_NOTICE)
            stop_reason = StopReason.END_TURN
        return ModelResponse(
            message=message, usage=TokenUsage(), stop_reason=stop_reason, model=model_name,
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
    "OUTPUT_CAP_NOTICE",
    "OutputCappedTransport",
    "max_turn_output_chars",
    "with_output_cap",
]
