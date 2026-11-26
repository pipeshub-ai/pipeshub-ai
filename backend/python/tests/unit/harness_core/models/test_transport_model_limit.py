"""P4.17 EN-1, EN-2, WF-7: ``TransportModel`` puts each purpose's limit on the transport, the wrappers forward it,
and a provider that produces without end stops at it."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any, override
from unittest.mock import patch

import pytest

from harness_core import bindings
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.kernel.core.messages import AssistantMessage, TextPart, UserMessage
from harness_core.kernel.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.models.output_bound import OFF, OutputLimits
from harness_core.models.transport_model import TransportModel
from harness_core.models.transports.base import LLMTransport
from harness_core.models.transports.opik_tracing import OpikTracingTransport
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolSchema

_MESSAGES: list[Message] = [UserMessage(content="write the report")]


class EndlessProvider(LLMTransport):
    """A provider whose model never stops on its own: distinct words until the request's limit, where it ends the
    turn as a provider does (``length``), with the usage it billed. Without a limit it would run forever, so a
    request without one is refused here rather than hanging the test."""

    def __init__(self, *, accepts: bool = True) -> None:
        self._accepts = accepts
        self.limits: list[int | None] = []

    @property
    @override
    def provider(self) -> str:
        return "openai"

    @property
    @override
    def model_name(self) -> str:
        return "gpt-6-luna"

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return self._accepts

    def _limit(self, kwargs: dict[str, Any]) -> int:
        limit = kwargs.get("max_output_tokens")
        self.limits.append(limit)
        if limit is None:
            raise AssertionError("an endless provider called without an output limit")
        return limit

    @override
    async def complete(self, messages: list[Message], tools: list[ToolSchema] | None = None, system: str | None = None, model: str | None = None, thinking_budget: int | None = None, effort: str | None = None, system_blocks: list[str] | None = None, **kwargs: Any) -> ModelResponse:
        limit = self._limit(kwargs)
        text = " ".join(f"w{i}" for i in range(limit // 2))
        return _cut(text, limit)

    @override
    async def complete_structured(self, messages: list[Message], output_schema: dict[str, Any], system: str | None = None, model: str | None = None, **kwargs: Any) -> StructuredResponse:
        self._limit(kwargs)
        return StructuredResponse(data={"ok": True})

    @override
    async def stream(self, messages: list[Message], tools: list[ToolSchema] | None = None, system: str | None = None, model: str | None = None, thinking_budget: int | None = None, effort: str | None = None, system_blocks: list[str] | None = None, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        limit = self._limit(kwargs)
        sent: list[str] = []
        for i in itertools.count():
            # About two tokens a word; distinct words, so nothing repeats.
            if 2 * i >= limit:
                break
            word = f"w{i} "
            sent.append(word)
            yield TextDeltaEvent(delta=word)
        yield StreamCompleteEvent(response=_cut("".join(sent), limit))


def _cut(text: str, limit: int) -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(content=[TextPart(text=text)], truncated=True),
        usage=TokenUsage(input_tokens=20, output_tokens=limit),
        stop_reason=StopReason.MAX_TOKENS,
        model="gpt-6-luna",
    )


async def test_en1_an_endless_stream_stops_at_the_turn_limit() -> None:
    provider = EndlessProvider()
    events = [e async for e in TransportModel(provider, limits=OutputLimits()).stream(_MESSAGES)]
    final = events[-1]
    assert isinstance(final, StreamCompleteEvent)
    assert provider.limits == [32_768]
    assert final.response.stop_reason == StopReason.MAX_TOKENS and final.response.message.truncated
    assert final.response.usage.output_tokens == 32_768


async def test_en2_each_purpose_sends_its_limit() -> None:
    provider = EndlessProvider()
    _ = await TransportModel(provider, limits=OutputLimits()).complete(_MESSAGES)
    _ = await TransportModel(provider, limits=OutputLimits()).complete_structured(_MESSAGES, {"type": "object"})
    _ = await TransportModel(provider, purpose="summary", limits=OutputLimits()).complete(_MESSAGES)
    assert provider.limits == [32_768, 8_192, 8_192]


async def test_en2_the_hosts_bindings_set_the_limit() -> None:
    provider = EndlessProvider()
    limits = OutputLimits.from_turn_limit(10_000)
    with bindings.scoped(bindings.KernelBindings(output_limits=lambda: limits)):
        _ = await TransportModel(provider).complete(_MESSAGES)
    assert provider.limits == [10_000]


class _NoLimit(EndlessProvider):
    """A transport that cannot send a limit: it must be called exactly as before (no keyword at all)."""

    @override
    def _limit(self, kwargs: dict[str, Any]) -> int:
        assert "max_output_tokens" not in kwargs
        self.limits.append(None)
        return 50


async def test_en2_a_transport_without_the_capability_is_called_as_before() -> None:
    provider = _NoLimit(accepts=False)
    _ = await TransportModel(provider, limits=OutputLimits()).complete(_MESSAGES)
    _ = [e async for e in TransportModel(provider, limits=OutputLimits()).stream(_MESSAGES)]
    _ = await TransportModel(provider, limits=OutputLimits()).complete_structured(_MESSAGES, {})
    assert provider.limits == [None, None, None]


async def test_ob2_limits_off_sends_nothing() -> None:
    provider = _NoLimit()
    _ = await TransportModel(provider, limits=OFF).complete(_MESSAGES)
    assert provider.limits == [None]


@pytest.mark.parametrize("wrap", ["metered", "opik"])
async def test_wf7_wrappers_forward_the_capability_and_the_limit(wrap: str) -> None:
    provider = EndlessProvider()
    if wrap == "metered":
        ledger = InMemoryBudgetLedger(ManualClock())
        account = RunAccountRef("org", "run")
        _ = await ledger.open_root(account, Cap(usd_micros=10_000_000))
        transport: LLMTransport = MeteredTransport(
            provider, accounts=ledger, account=account, pricing_model="gpt-6-luna", ids=SeededIds(0)
        )
    else:
        transport = OpikTracingTransport(provider)
    assert transport.accepts_output_limit
    # No span reaches an Opik server: the tracing wrapper carries on when its span cannot start.
    with patch("opik.start_as_current_span", side_effect=RuntimeError("no opik here")):
        _ = await TransportModel(transport, limits=OutputLimits()).complete(_MESSAGES)
        _ = [e async for e in TransportModel(transport, limits=OutputLimits()).stream(_MESSAGES)]
        _ = await TransportModel(transport, limits=OutputLimits()).complete_structured(_MESSAGES, {})
    assert provider.limits == [32_768, 32_768, 8_192]
