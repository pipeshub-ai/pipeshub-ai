"""`UsageReportingTransport`: records the usage of every model call a request
makes through its transports, labelled by `llm_usage_source`.

Every agent of a request resolves its model from the request's transport
registry: the main loop, sub-agents, one-turn helpers (intent, goal), context
compaction, plan and result critics, planners. Counting at the transport
covers all of them in one place, including callers that keep only a
response's data and drop its usage. Wrapped around the registry's transports
only when the request opted into the `run_usage` frame.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.core.usage_source import current_llm_usage_source
from app.agent_loop_lib.transport.base import LLMTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse, TokenUsage
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

logger = logging.getLogger(__name__)


class UsageReportingTransport(LLMTransport):
    """Decorates any `LLMTransport`, sending each finished call's usage to
    `sink` as `(source, usage)`. A call that raises, or a stream abandoned
    before its final event, has no usage to report."""

    def __init__(self, inner: LLMTransport, sink: Callable[[str, TokenUsage], None]) -> None:
        self._inner = inner
        self._sink = sink

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def _report(self, usage: TokenUsage | None) -> None:
        if usage is None:
            return
        try:
            self._sink(current_llm_usage_source(), usage)
        except Exception:
            # Accounting must never cost the call it accounts for.
            logger.warning("usage report failed", exc_info=True)

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
        response = await self._inner.complete(
            messages, tools, system, model, thinking_budget, effort, system_blocks,
        )
        self._report(response.usage)
        return response

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        response = await self._inner.complete_structured(messages, output_schema, system, model)
        self._report(response.usage)
        return response

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        async for event in self._inner.stream(
            messages, tools, system, model, thinking_budget, effort, system_blocks,
        ):
            if isinstance(event, StreamCompleteEvent):
                self._report(event.response.usage)
            yield event


def with_usage_reporting(
    factory: Callable[[], LLMTransport],
    sink: Callable[[str, TokenUsage], None] | None,
) -> Callable[[], LLMTransport]:
    """The registry factory `factory`, its transport wrapped to report usage
    to `sink`; `factory` unchanged when `sink` is None."""
    if sink is None:
        return factory
    return lambda: UsageReportingTransport(factory(), sink)


__all__ = ["UsageReportingTransport", "with_usage_reporting"]
