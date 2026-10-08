"""Hold the indexing LLM slot for every agent turn.

``LangChainTransport`` does not take the slot. Extraction running in the
indexing process must, or NER turns bypass ``MAX_CONCURRENT_INDEXING_LLM_CALLS``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.agent_loop_lib.core.messages import Message
from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
from app.agent_loop_lib.core.streaming import StreamEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.base import LLMTransport
from app.utils.concurrency import indexing_llm_slot


class SlottedTransport(LLMTransport):
    def __init__(self, inner: LLMTransport) -> None:
        self._inner = inner

    @property
    def provider(self) -> str:
        return "indexing_slotted"

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> ModelResponse:
        async with indexing_llm_slot():
            return await self._inner.complete(
                messages,
                tools=tools,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
                effort=effort,
                system_blocks=system_blocks,
            )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        async with indexing_llm_slot():
            return await self._inner.complete_structured(
                messages, output_schema, system=system, model=model
            )

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        return self._stream(
            messages,
            tools=tools,
            system=system,
            model=model,
            thinking_budget=thinking_budget,
            effort=effort,
            system_blocks=system_blocks,
        )

    async def _stream(self, messages, **kwargs) -> AsyncIterator[StreamEvent]:
        async with indexing_llm_slot():
            async for event in self._inner.stream(messages, **kwargs):
                yield event
