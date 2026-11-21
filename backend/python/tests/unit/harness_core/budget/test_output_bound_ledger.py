"""P4.17 LG-1..LG-3: the run's ledger counts a bounded call correctly, and the run's cap still applies."""

from __future__ import annotations

from typing import Any, override

import pytest

from harness_core.access.spend import estimate_spend, input_estimate
from harness_core.budget.errors import BudgetExhausted
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Amount, Cap, RunAccountRef
from harness_core.kernel.core.messages import AssistantMessage, TextPart, UserMessage
from harness_core.kernel.core.responses import ModelResponse, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from harness_core.models.output_bound import OutputLimits
from harness_core.models.transport_model import TransportModel
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

ROOT = RunAccountRef("org-1", "root")
HI = [UserMessage(content="hi")]
MODEL = "gpt-6-luna"


class _Recording(InMemoryBudgetLedger):
    def __init__(self) -> None:
        super().__init__(ManualClock())
        self.requested: list[Amount] = []

    @override
    async def reserve(self, account: RunAccountRef, key: str, requested: Amount, **kwargs: Any) -> Any:
        self.requested.append(requested)
        return await super().reserve(account, key, requested, **kwargs)


async def _model(events: list[Any], cap: Cap = Cap(None, 10_000_000)) -> tuple[TransportModel, _Recording, RecordedTransport]:
    ledger = _Recording()
    _ = await ledger.open_root(ROOT, cap)
    inner = RecordedTransport(events)
    metered = MeteredTransport(inner, accounts=ledger, account=ROOT, pricing_model=MODEL, ids=SeededIds(0))
    return TransportModel(metered, limits=OutputLimits()), ledger, inner


def _final(text: str = "x", usage: TokenUsage | None = None) -> StreamCompleteEvent:
    return StreamCompleteEvent(
        response=ModelResponse(message=AssistantMessage(content=[TextPart(text=text)]), usage=usage or TokenUsage())
    )


async def test_lg1_a_stream_the_bound_cut_is_charged_its_reasoning_and_text() -> None:
    loop = [ThinkingDeltaEvent(delta="Let me check the dates again. ") for _ in range(600)]
    loop += [TextDeltaEvent(delta="Renewal passed. ") for _ in range(600)]
    model, ledger, _ = await _model([*loop, _final()])
    _ = [e async for e in model.stream(HI)]
    committed = (await ledger.remaining(ROOT)).committed
    # The reasoning loop alone is about 4 KB (~1,000 tokens) before the cut: it is billed, so it is charged.
    assert committed.tokens > 900
    assert committed.usd_micros > 0


@pytest.mark.parametrize("streaming", [False, True])
async def test_lg2_a_response_without_usage_is_charged_the_estimate_not_zero(streaming: bool) -> None:
    text = "An answer whose provider reported no usage. " * 20
    model, ledger, _ = await _model([TextDeltaEvent(delta=text), _final(text)])
    if streaming:
        _ = [e async for e in model.stream(HI)]
    else:
        _ = await model.complete(HI)
    committed = (await ledger.remaining(ROOT)).committed
    expected_input = input_estimate(HI, None, [])
    assert committed.tokens > expected_input + 100
    assert committed.usd_micros > 0


async def test_lg2_reported_usage_is_still_what_is_charged() -> None:
    model, ledger, _ = await _model([reply_events("ok", usage=TokenUsage(input_tokens=100, output_tokens=20))])
    _ = await model.complete(HI)
    assert (await ledger.remaining(ROOT)).committed.tokens == 120


async def test_lg3_the_reservation_is_the_input_plus_the_calls_limit() -> None:
    model, ledger, _ = await _model([reply_events("ok", usage=TokenUsage(input_tokens=10, output_tokens=5))])
    _ = await model.complete(HI)
    estimate = input_estimate(HI, None, [])
    assert ledger.requested == [estimate_spend(estimate, 32_768, MODEL, charges_usd=True)]


async def test_lg3_the_run_cap_still_refuses_a_call_it_cannot_pay() -> None:
    model, ledger, inner = await _model(
        [reply_events("ok", usage=TokenUsage(input_tokens=10, output_tokens=5))], cap=Cap(None, 30)
    )
    _ = await model.complete(HI)
    _ = await model.complete(HI)
    with pytest.raises(BudgetExhausted):
        _ = await model.complete(HI)
    assert len(inner.calls) == 2
    assert all(call.max_output_tokens == 32_768 for call in inner.calls)
