"""A run's own model calls take their provider slot first (G-45), as an engine's calls do at the model gateway.

A loop run calls its model in the worker process, not through the gateway, so without this its background runs
held provider calls a person's turn then waited behind. ``AdmittedTransport`` sits next to the provider, under the
ledger's ``MeteredTransport``, so every call any decorator above it makes (the turn, a summary, a follow-up asked
after the run's lease) is admitted with the run's class, and the slot is held from before the provider is asked
until its answer settles: a stream's slot until the stream ends or its reader leaves it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from harness_core.gateway.priority_gate import provider_key
from harness_core.models.output_bound import output_limit
from harness_core.models.stream_watch import unwatched
from harness_core.models.transports.base import LLMTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from harness_core.gateway.priority_gate import AdmissionSlot, ProviderAdmission
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import ModelResponse, StructuredResponse
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
    from harness_core.priority import PriorityClass


class AdmittedTransport(LLMTransport):
    def __init__(
        self,
        inner: LLMTransport,
        *,
        admission: ProviderAdmission,
        priority_class: PriorityClass | None,
        provider: str | None = None,
        key: str | None = None,
        org: str | None = None,
    ) -> None:
        """``provider``: the configured provider the gateway's routes name too (``azureOpenAI``), when the
        transport's own family differs from it. ``key``: the limit the calls count against, the upstream account
        and endpoint (``upstream_key``) as the gateway names it for the same account; unset, the provider's. ``org``: the run's org, so orgs sharing one upstream key share it fairly (WP56-FAIR)."""
        self._inner = inner
        self._admission = admission
        self._priority_class = priority_class
        self.key = key or provider_key(provider or inner.provider)
        self._org = org

    @property
    def priority_class(self) -> PriorityClass | None:
        return self._priority_class

    @property
    def unadmitted(self) -> LLMTransport:
        """The provider's transport, for a caller that took the slot itself first (``admit``)."""
        return self._inner

    async def admit(self) -> AdmissionSlot:
        """This call's provider slot. The wait is the queue's, not the model's: a stall bound the call runs under
        is paused for it. A caller that reserves budget (``MeteredTransport``) takes the slot first, as the model
        gateway does, so a queued call holds no reservation while it waits."""
        with unwatched():
            return await self._admission.acquire(self.key, self._priority_class, org=self._org)

    @property
    @override
    def provider(self) -> str:
        return self._inner.provider

    @property
    @override
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    @override
    def accepts_cache_marks(self) -> bool:
        return self._inner.accepts_cache_marks

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return self._inner.accepts_output_limit

    @override
    def keep_provider_state(self) -> bool:
        return self._inner.keep_provider_state()

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        cache_marks: Sequence[int] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        slot = await self.admit()
        try:
            return await self._inner.complete(
                messages=messages,
                tools=tools,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
                effort=effort,
                system_blocks=system_blocks,
                **_marks(cache_marks),
                **output_limit(max_output_tokens),
                **({"tool_choice": tool_choice} if tool_choice else {}),
            )
        finally:
            slot.release()

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResponse:
        slot = await self.admit()
        try:
            return await self._inner.complete_structured(
                messages=messages,
                output_schema=output_schema,
                system=system,
                model=model,
                **output_limit(max_output_tokens),
            )
        finally:
            slot.release()

    @override
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        cache_marks: Sequence[int] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        return self._admitted_stream(
            messages, tools, system, model, thinking_budget, effort, system_blocks, cache_marks, max_output_tokens,
            tool_choice=tool_choice,
        )

    async def _admitted_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        system: str | None,
        model: str | None,
        thinking_budget: int | None,
        effort: str | None,
        system_blocks: list[str] | None,
        cache_marks: Sequence[int] | None,
        max_output_tokens: int | None,
        *,
        tool_choice: ToolChoice | None,
    ) -> AsyncIterator[StreamEvent]:
        slot = await self.admit()
        try:
            async for event in self._inner.stream(
                messages=messages,
                tools=tools,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
                effort=effort,
                system_blocks=system_blocks,
                **_marks(cache_marks),
                **output_limit(max_output_tokens),
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ):
                yield event
        finally:
            slot.release()


def _marks(cache_marks: Sequence[int] | None) -> dict[str, Any]:
    return {} if cache_marks is None else {"cache_marks": cache_marks}


__all__ = ["AdmittedTransport"]
