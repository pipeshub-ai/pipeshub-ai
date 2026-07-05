from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.agent_loop_lib.models.base import Model

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from app.agent_loop_lib.transport.base import LLMTransport

__all__ = ["TransportModel"]


class TransportModel(Model):
    """Adapts any `LLMTransport` (a provider-protocol implementation) to
    the `Model` interface `Agent` depends on. A thin pass-through today
    since `LLMTransport` itself already returns `ModelResponse`/
    `StructuredResponse`/`StreamEvent` — the value is structural (DIP):
    `Agent` can be handed any `Model`, including a decorator that wraps a
    `TransportModel` with retry/caching/fallback behavior, without ever
    importing `LLMTransport` itself.
    """

    def __init__(self, transport: "LLMTransport") -> None:
        self._transport = transport

    @property
    def transport(self) -> "LLMTransport":
        return self._transport

    @property
    def model_name(self) -> str:
        return getattr(self._transport, "model_name", "") or ""

    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> "ModelResponse":
        return await self._transport.complete(
            messages=messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort,
        )

    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse":
        return await self._transport.complete_structured(
            messages=messages, output_schema=output_schema, system=system, model=model,
        )

    def stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator["StreamEvent"]:
        return self._transport.stream(
            messages=messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort,
        )
