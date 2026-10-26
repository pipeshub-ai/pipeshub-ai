"""Send every agent turn through the indexing LLM gateway.

``LangChainTransport`` does not. Extraction is indexing-time work, so its turns
take the gateway's process-wide cap (``MAX_CONCURRENT_INDEXING_LLM_CALLS``), the
model's breaker and the hang ceiling like every other indexing model call. A
streamed turn holds the cap's permit only: it cannot be retried as one call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import Message
from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
from app.agent_loop_lib.core.streaming import StreamEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.base import LLMTransport
from app.services.llm_gateway.gateway import get_llm_gateway
from app.utils.concurrency import indexing_llm_slot

if TYPE_CHECKING:
    from app.agent_loop_lib.core.tool_schema import ToolChoice


class SlottedTransport(LLMTransport):
    def __init__(self, inner: LLMTransport) -> None:
        self._inner = inner

    @property
    def provider(self) -> str:
        return "indexing_slotted"

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def _gateway_provider(self) -> str:
        # The breaker and metrics key: the model and its provider, never credentials.
        return f"{self._inner.provider}:{self._inner.model_name}"

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        return await get_llm_gateway().call(
            lambda: self._inner.complete(
                messages,
                tools=tools,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
                effort=effort,
                system_blocks=system_blocks,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ),
            provider=self._gateway_provider,
            call_site="named_entities",
        )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        return await get_llm_gateway().call(
            lambda: self._inner.complete_structured(
                messages, output_schema, system=system, model=model
            ),
            provider=self._gateway_provider,
            call_site="named_entities",
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
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        return self._stream(
            messages,
            tools=tools,
            system=system,
            model=model,
            thinking_budget=thinking_budget,
            effort=effort,
            system_blocks=system_blocks,
            **({"tool_choice": tool_choice} if tool_choice else {}),
        )

    async def _stream(self, messages, **kwargs) -> AsyncIterator[StreamEvent]:
        async with indexing_llm_slot():
            async for event in self._inner.stream(messages, **kwargs):
                yield event
