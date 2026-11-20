"""`with_ledger`: the request's transports report every model call to its ledger.

Every agent of a request resolves its model from the request's transport
registry (the main loop, intent, sub-agents, planners, critics, compaction),
so wrapping the registered factories observes all of them at one place: the
prompt each call is given, and the tool calls each response asks for. The
call runs inside a `model_call_scope`, which is how the LangChain tripwire
knows a call is accounted for.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.core.usage_source import current_llm_usage_source
from app.agent_loop_lib.transport.base import LLMTransport
from app.modules.agents.privacy.ledger import CallKind, LabelLedger, model_call_scope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolChoice, ToolSchema

logger = logging.getLogger(__name__)


class LedgerTransport(LLMTransport):
    """Decorates any `LLMTransport`: each call is observed by `ledger` before it is sent."""

    def __init__(self, inner: LLMTransport, ledger: LabelLedger) -> None:
        self._inner = inner
        self._ledger = ledger

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def _scope(self, messages: list[Message]) -> model_call_scope:
        return model_call_scope(current_llm_usage_source(), CallKind.OBSERVED, messages, ledger=self._ledger)

    def _output(self, response: ModelResponse | None) -> None:
        try:
            calls = getattr(getattr(response, "message", None), "tool_calls", None) or ()
            if calls:
                self._ledger.observe_output((call.id, call.name) for call in calls)
        except Exception:
            logger.warning("answer label: a response's tool calls were not recorded", exc_info=True)
            self._ledger.untagged("model_output")

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
        with self._scope(messages):
            response = await self._inner.complete(
                messages, tools, system, model, thinking_budget, effort, system_blocks,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            )
        self._output(response)
        return response

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        with self._scope(messages):
            return await self._inner.complete_structured(messages, output_schema, system, model)

    async def stream(
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
        scope = self._scope(messages)
        scope.__enter__()
        try:
            async for event in self._inner.stream(
                messages, tools, system, model, thinking_budget, effort, system_blocks,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ):
                if isinstance(event, StreamCompleteEvent):
                    self._output(event.response)
                yield event
        finally:
            scope.__exit__(None, None, None)


def with_ledger(factory: Callable[[], LLMTransport], ledger: LabelLedger) -> Callable[[], LLMTransport]:
    """The registry factory `factory`, observed by `ledger`; unchanged when the ledger labels nothing."""
    if not ledger.labels:
        return factory
    return lambda: LedgerTransport(factory(), ledger)


__all__ = ["LedgerTransport", "with_ledger"]
