# pyright: basic
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import TYPE_CHECKING, Any

from harness_core import bindings
from harness_core.kernel.core.responses import ModelResponse
from harness_core.models.base import Model
from harness_core.models.output_bound import (
    OutputLimits,
    OutputPurpose,
    bound_response,
    bound_stream,
)
from harness_core.models.stream_watch import StreamTimeouts, bounded, watched

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import StructuredResponse
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
    from harness_core.models.transports.base import LLMTransport

__all__ = ["TransportModel"]


class TransportModel(Model):
    """Adapts any `LLMTransport` (a provider-protocol implementation) to
    the `Model` interface `Agent` depends on. A thin pass-through today
    since `LLMTransport` itself already returns `ModelResponse`/
    `StructuredResponse`/`StreamEvent` — the value is structural (DIP):
    `Agent` can be handed any `Model`, including a decorator that wraps a
    `TransportModel` with retry/caching/fallback behavior, without ever
    importing `LLMTransport` itself.

    Every call is bounded by its progress (P4.9, `stream_watch`): this is the
    one adapter both loops' model calls go through. `timeouts=None` leaves a
    call unbounded.

    Every call's output is bounded too (P4.17): the limit of its purpose on the wire, and a stream that loops cut
    as a truncated turn. ``purpose`` is what ``complete``/``stream`` calls are for (an agent turn, or a summary);
    ``complete_structured`` is always a structured call. ``limits=None`` follows the host's bindings.
    """

    def __init__(
        self,
        transport: "LLMTransport",
        *,
        timeouts: StreamTimeouts | None = StreamTimeouts(),
        purpose: OutputPurpose = "turn",
        limits: OutputLimits | None = None,
    ) -> None:
        self._transport = transport
        self._timeouts = timeouts
        self._purpose: OutputPurpose = purpose
        self._limits = limits

    @property
    def transport(self) -> "LLMTransport":
        return self._transport

    @property
    def model_name(self) -> str:
        return getattr(self._transport, "model_name", "") or ""

    @property
    def provider(self) -> str:
        return self._transport.provider

    @property
    def accepts_cache_marks(self) -> bool:
        return self._transport.accepts_cache_marks

    def output_limits(self) -> OutputLimits:
        if self._limits is not None:
            return self._limits
        resolve = bindings.current().output_limits
        return resolve() if resolve is not None else OutputLimits()

    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        cache_marks: Sequence[int] | None = None,
        *,
        tool_choice: "ToolChoice | None" = None,
        max_output_tokens: int | None = None,
    ) -> "ModelResponse":
        limits = self.output_limits()
        response = await self._bounded(self._transport.complete(
            messages=messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort,
            system_blocks=system_blocks, **_marks(cache_marks),
            **({"tool_choice": tool_choice} if tool_choice else {}),
            **self._limit(limits, self._purpose, model, max_output_tokens),
        ))
        # A test double may return anything: only a real response is bounded.
        return bound_response(response) if limits.repetition and isinstance(response, ModelResponse) else response

    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse":
        return await self._bounded(self._transport.complete_structured(
            messages=messages, output_schema=output_schema, system=system, model=model,
            **self._limit(self.output_limits(), "structured", model),
        ))

    def stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        cache_marks: Sequence[int] | None = None,
        *,
        tool_choice: "ToolChoice | None" = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator["StreamEvent"]:
        limits = self.output_limits()
        stream = self._transport.stream(
            messages=messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort,
            system_blocks=system_blocks, **_marks(cache_marks),
            **({"tool_choice": tool_choice} if tool_choice else {}),
            **self._limit(limits, self._purpose, model, max_output_tokens),
        )
        if limits.repetition:
            stream = bound_stream(stream, model=model or self.model_name)
        return stream if self._timeouts is None else watched(stream, self._timeouts)

    def _limit(
        self, limits: OutputLimits, purpose: OutputPurpose, model: str | None, cap: int | None = None
    ) -> dict[str, Any]:
        """``max_output_tokens`` only for a transport that puts it on the wire: any other is called as before.
        ``cap``: the call's own bound (the wrap-up turn's, BUDGETEND), never above the purpose's."""
        if getattr(self._transport, "accepts_output_limit", False) is not True:
            return {}
        limit = limits.for_call(purpose, model or self.model_name)
        if cap is not None:
            limit = cap if limit is None else min(limit, cap)
        return {} if limit is None else {"max_output_tokens": limit}

    async def _bounded[T](self, call: "Awaitable[T]") -> T:
        return await (call if self._timeouts is None else bounded(call, self._timeouts))


def _marks(cache_marks: Sequence[int] | None) -> dict[str, Any]:
    """``cache_marks`` only when set: a transport that never takes them is called as before."""
    return {} if cache_marks is None else {"cache_marks": cache_marks}
