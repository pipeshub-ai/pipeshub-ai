"""SEC1 LC-1..LC-5: the ledger charges a cached call exactly what the provider bills, on every path.

One request: 1,000 uncached input tokens, 4,000 cached reads, 300 cache writes, 50 output tokens,
priced as ``gpt-6-sol`` (input 2.00, read 0.20, write 2.50, output 10.00 per million):
2,000 + 800 + 750 + 500 = 4,050 micro-USD. Every transport reports it as one ``TokenUsage`` (the whole
prompt in ``input_tokens``, USAGE1), so the family behind a call changes nothing; the transports'
own payloads are in ``models/test_usage1_convention.py``.
"""

from __future__ import annotations

import pytest

from harness_core.access.spend import OutputAllowance, reservation_for, usage_spend
from harness_core.access.types import PinnedRoute
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from tests.unit.harness_core.gateway.test_model_gateway import spent
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES

MODEL = "gpt-6-sol"
EXPECTED_MICROS = 4_050
USAGE = TokenUsage(input_tokens=5_300, output_tokens=50, cache_read_tokens=4_000, cache_write_tokens=300)
ROOT = RunAccountRef("org-1", "root")
FAMILIES = ("anthropic", "azure_direct", "bedrock", "gemini", "langchain", "openai")


def test_lc1_the_expected_amount_is_the_providers_own_arithmetic() -> None:
    spend = usage_spend(USAGE, MODEL, charges_usd=True)
    assert spend.usd_micros == EXPECTED_MICROS
    # LC-2: reads and writes are tokens of the prompt, counted once; ENGBUDGET: a read weighs its price ratio
    # (gpt-6-sol: 0.20 / 2.00), so 1,000 fresh + 300 written + 4,000 x 0.1 read + 50 output.
    assert spend.tokens == 1_000 + 300 + 400 + 50


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("stream", [False, True])
async def test_lc1_the_loops_metered_transport_commits_the_same_amount(family: str, stream: bool) -> None:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, Cap(None, 10_000_000))
    inner = RecordedTransport([TextDeltaEvent(delta="ok"), reply_events("ok", usage=USAGE)], family=family)
    metered = MeteredTransport(inner, accounts=ledger, account=ROOT, pricing_model=MODEL, ids=SeededIds(0))
    if stream:
        async for _ in metered.stream([UserMessage(content="hi")]):
            pass
    else:
        await metered.complete([UserMessage(content="hi")])
    assert (await ledger.remaining(ROOT)).committed.usd_micros == EXPECTED_MICROS


GATEWAY_FAMILIES = ("openai", "azure_direct", "anthropic", "gemini")


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("family", GATEWAY_FAMILIES)
@pytest.mark.parametrize("stream", [False, True])
async def test_lc3_the_model_gateway_commits_the_same_amount_on_every_wire(wire: str, family: str, stream: bool) -> None:
    rig = GatewayRig()
    transport = RecordedTransport([TextDeltaEvent(delta="ok"), reply_events("ok", usage=USAGE)], family=family)
    route = PinnedRoute(family, "gpt-x", config_key="key-1", config_model=MODEL)
    binding = rig.pin(routes=(route,), transport=transport)
    codec, body = WIRES[wire]
    outcome = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body(stream=stream)))
    chunks = getattr(outcome, "chunks", None)
    if chunks is not None:
        async for _ in chunks:
            pass
    assert (await spent(rig)).usd_micros == EXPECTED_MICROS


def test_lc5_a_reservation_prices_the_whole_estimate_at_the_input_price() -> None:
    """The cap holds whatever the cache does: the minimum a call must find is its estimated input at
    the full price, an upper bound of what a cached call costs (deliberate, not a cache discount)."""
    requested, minimum = reservation_for(5_300, MODEL, OutputAllowance(cap_tokens=50), charges_usd=True)
    assert minimum.usd_micros == 10_600 > EXPECTED_MICROS
    assert requested.usd_micros == 10_600 + 500
