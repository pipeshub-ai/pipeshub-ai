"""`CancellationAwareTransport`: races a `CancellationToken` against each
provider chunk for `LLMTransport` implementations that have no cancellation
wiring of their own.

`LangChainTransport.stream()` (`app/agents/agent_loop/langchain_transport.py`)
already does this inline for the `PIPESHUB_AGENT_TRANSPORT=langchain` arm.
This module gives the `PIPESHUB_AGENT_TRANSPORT=direct` SDK transports
(OpenAI, Azure OpenAI, Anthropic, Gemini -- `agent_loop_lib/transport/`) the
same behavior without teaching each of them PipesHub's `CancellationToken` --
mirroring how `image_guard.py::CappedImagesTransport` applies PipesHub's own
image policy to the same four transports from outside `agent_loop_lib`.

`complete()`/`complete_structured()` are single unchunked provider calls with
no intermediate point to check a token mid-call -- same reason
`LangChainTransport` only wires its check into `stream()` -- so both are
delegated unchanged here too.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import AssistantMessage, TextPart
from app.agent_loop_lib.core.responses import ModelResponse, StopReason, TokenUsage
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.transport.base import LLMTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.context import CancellationToken
    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

logger = logging.getLogger(__name__)

__all__ = ["CancellationAwareTransport", "with_cancellation"]


class CancellationAwareTransport(LLMTransport):
    """Decorates any `LLMTransport`, racing `token.wait()` against each
    `stream()` chunk so a cooperative Stop Generation cancel interrupts a
    provider mid-response instead of only at the next PRE_TURN/per-tool-call
    guard.
    """

    def __init__(self, inner: LLMTransport, token: "CancellationToken") -> None:
        self._inner = inner
        self._token = token

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
        # One task for the run of this call: `CancellationToken.wait()`
        # wraps an `asyncio.Event` that, once set, stays set -- reusing the
        # same task across iterations (rather than recreating it) is both
        # cheaper and correct, since a not-yet-done task is safe to pass to
        # `asyncio.wait()` again next iteration.
        cancel_task = asyncio.ensure_future(self._token.wait())
        text_parts: list[str] = []
        try:
            while True:
                # Fast exit before starting the next provider read: if
                # cancel() was called after the last yield, skip straight
                # to the post-loop cancelled-completion path without
                # issuing another __anext__().
                if self._token.is_cancelled:
                    break
                next_chunk_task = asyncio.ensure_future(stream_iter.__anext__())
                await asyncio.wait(
                    {next_chunk_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                if self._token.is_cancelled:
                    # `cancel_task` (racing `token.wait()` against the next
                    # chunk) is what makes this fire even when the provider
                    # stalls between chunks -- see `LangChainTransport.
                    # stream()` for the same rationale.
                    if not next_chunk_task.done():
                        next_chunk_task.cancel()
                    with contextlib.suppress(BaseException):
                        await next_chunk_task
                    break
                try:
                    event = next_chunk_task.result()
                except StopAsyncIteration:
                    return
                if isinstance(event, TextDeltaEvent):
                    text_parts.append(event.delta)
                yield event
                # `StreamCompleteEvent` is terminal — exactly one per
                # `stream()` call. Without this early return, a `cancel()`
                # racing the next iteration would enter the cancellation
                # branch and emit a second one.
                if isinstance(event, StreamCompleteEvent):
                    return

            # Reached only by `break` from the two cancellation paths
            # above. One `StreamCompleteEvent` with whatever text we
            # accumulated, `stop_reason=CANCELLED`, no tool calls (any
            # in-progress call's arguments are truncated mid-JSON and
            # would corrupt `Agent.step()`'s tool-dispatch loop).
            yield StreamCompleteEvent(
                response=ModelResponse(
                    message=AssistantMessage(
                        content=(
                            [TextPart(text="".join(text_parts))]
                            if text_parts else []
                        ),
                    ),
                    usage=TokenUsage(),
                    stop_reason=StopReason.CANCELLED,
                    model=self._inner.model_name,
                ),
            )
        finally:
            if not cancel_task.done():
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_task
            # Explicit close, not left to GC: this is what actually stops
            # the provider from continuing to generate/bill for tokens
            # nobody will read once cancellation wins the race above. A
            # no-op on the natural-completion path (the generator is
            # already exhausted).
            with contextlib.suppress(BaseException):
                await stream_iter.aclose()


def with_cancellation(
    transport: LLMTransport, token: "CancellationToken | None",
) -> LLMTransport:
    """`transport` with cooperative cancellation applied, or unchanged when
    no token is wired (e.g. a sub-agent call built without one) -- same
    "no policy configured behaves exactly as before" rule
    `image_guard.py::with_image_cap` follows for the image cap.
    """
    if token is None:
        return transport
    return CancellationAwareTransport(transport, token)
