"""ENGBUDGET: a Codex child stopped on a token cap it had spent on cached prompt reads.

A recorded run: the parent loop used 169k tokens, DEEPROUTE handed the
work to a Codex child, and the child's 20 model calls, each resending its growing history, were charged 773,807
tokens for USD 0.019. Most of each prompt was a cached read. Its 21st call was refused ``BUDGET_INSUFFICIENT``
with USD 4.95 of 5 left, and the child's answer was the no-model fallback.

``RECORDED_CHILD_CALLS`` are the recorded child's calls as the gateway logged them (``model_gateway.call``: input and output
tokens, and the cached share recovered from ``costMicroUsd`` at gpt-6-luna's prices).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pytest

from harness_core.access.spend import (
    OutputAllowance,
    budget_tokens,
    cache_read_weight,
    reservation_for,
    usage_spend,
)
from harness_core.access.types import RunBudget
from harness_core.budget.errors import BudgetInsufficient
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import ZERO, Amount, Cap, PinnedBudget, RunAccountRef
from harness_core.budget.wrap_up import WrapUpReserve, WrapUpSize
from harness_core.engines.durable.wrap_up import ModelEngineWrapUp
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.engine_wrap_up import (
    GATHERED,
    REQUEST,
    EngineWrapUpContract,
    EngineWrapUpHarness,
)
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

MODEL = "gpt-6-luna"
# (input tokens, of which cached, output tokens) per call, in order.
RECORDED_CHILD_CALLS: tuple[tuple[int, int, int], ...] = (
    (3857, 0, 56), (7067, 2952, 74), (10318, 6153, 152), (10515, 10256, 83), (12877, 9847, 139),
    (16797, 11774, 230), (20846, 15662, 192), (24794, 19743, 155), (29012, 23613, 264), (33217, 27830, 238),
    (37355, 32061, 216), (41546, 36179, 417), (45407, 40469, 753), (57777, 41958, 1227), (61606, 56701, 603),
    (65467, 60530, 130), (69450, 64356, 102), (71388, 68898, 102), (73462, 70802, 102), (75686, 72840, 128),
)
REFUSED_PROMPT = 77_500
PARENT_TOKENS = 169_000
PARENT_RESERVE = Amount(6_400, 32_000)
ROOT = RunAccountRef("org-1", "parent")
CHILD = RunAccountRef("org-1", "child")
CAP = Cap(5_000_000, 1_000_000)


def _usage(call: tuple[int, int, int]) -> TokenUsage:
    prompt, cached, output = call
    return TokenUsage(input_tokens=prompt, output_tokens=output, cache_read_tokens=cached)


async def _recorded_tree(*, cached_weight: bool) -> InMemoryBudgetLedger:
    """The recorded ledger just before the refused call: the parent's spend and wrap-up hold, the child's 20 calls."""
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, CAP)
    await ledger.open_child(ROOT, CHILD, Cap(None, None))
    held = await ledger.reserve(ROOT, "parent", Amount(0, PARENT_TOKENS), minimum=ZERO, ttl_s=60)
    _ = await ledger.commit(held, Amount(28_000, PARENT_TOKENS))
    _ = await ledger.reserve(ROOT, "wrapup", PARENT_RESERVE, minimum=PARENT_RESERVE, ttl_s=3_600)
    for n, call in enumerate(RECORDED_CHILD_CALLS):
        usage = _usage(call)
        spend = usage_spend(usage, MODEL, charges_usd=True)
        if not cached_weight:
            spend = Amount(spend.usd_micros, usage.input_tokens + usage.output_tokens)
        held = await ledger.reserve(CHILD, f"call-{n}", spend, minimum=ZERO, ttl_s=60)
        _ = await ledger.commit(held, spend)
    return ledger


def test_a_cached_read_weighs_its_price_ratio() -> None:
    assert cache_read_weight(MODEL) == pytest.approx(0.1)
    usage = TokenUsage(input_tokens=75_686, output_tokens=128, cache_read_tokens=72_840)
    assert budget_tokens(usage, MODEL) == (75_686 - 72_840) + math.ceil(72_840 * 0.1) + 128
    # A model whose provider publishes no cache price is weighed at the default multiplier, never above 1.
    assert 0 < cache_read_weight("some-local-model") <= 1


def test_writes_and_fresh_tokens_count_in_full() -> None:
    usage = TokenUsage(input_tokens=10_000, output_tokens=500, cache_read_tokens=6_000, cache_write_tokens=3_000)
    assert budget_tokens(usage, "claude-sonnet-4-6") == 1_000 + 3_000 + 600 + 500


def test_usd_is_the_providers_arithmetic_either_way() -> None:
    spent = sum(usage_spend(_usage(call), MODEL, charges_usd=True).usd_micros for call in RECORDED_CHILD_CALLS)
    # The child's logged costs summed to USD 0.0191 (the recorded ledger: 19,087 micro-USD).
    assert 18_000 <= spent <= 20_500


def test_recorded_full_weight_reproduces_the_charge() -> None:
    assert sum(prompt + output for prompt, _, output in RECORDED_CHILD_CALLS) == 773_807


async def test_recorded_before_the_fix_the_next_call_is_refused() -> None:
    """The incident as it was: counted in full, the child's next prompt does not fit what is left."""
    ledger = await _recorded_tree(cached_weight=False)
    requested = Amount(20_000, REFUSED_PROMPT + 32_768)
    with pytest.raises(BudgetInsufficient):
        _ = await ledger.reserve(CHILD, "call-20", requested, minimum=Amount(7_750, REFUSED_PROMPT), ttl_s=60)


async def test_recorded_after_the_fix_the_child_keeps_working() -> None:
    ledger = await _recorded_tree(cached_weight=True)
    left = (await ledger.remaining(CHILD)).available
    assert left.tokens is not None and left.tokens > 500_000, left
    requested, minimum = reservation_for(REFUSED_PROMPT, MODEL, OutputAllowance(), charges_usd=True)
    held = await ledger.reserve(CHILD, "call-20", requested, minimum=minimum, ttl_s=60)
    assert held.granted.tokens >= minimum.tokens


def test_the_minimum_is_the_least_the_input_can_be_charged() -> None:
    """Sized consistently with the charge: the token minimum is the prompt all read from the cache, which is what
    ``budget_tokens`` charges at least; the money minimum stays the fresh price."""
    _, minimum = reservation_for(REFUSED_PROMPT, MODEL, OutputAllowance(), charges_usd=True)
    floor = budget_tokens(TokenUsage(input_tokens=REFUSED_PROMPT, cache_read_tokens=REFUSED_PROMPT), MODEL)
    assert minimum.tokens == floor
    assert minimum.usd_micros == math.ceil(REFUSED_PROMPT * 0.10)


async def test_the_gateway_charges_an_engine_call_the_weighted_tokens() -> None:
    """The engine path: the model gateway settles an engine's call with the same weighing as a loop's."""
    from harness_core.access.types import PinnedRoute
    from harness_core.testing.model_gateway import GatewayRig, body_of

    route = PinnedRoute("openai", MODEL, config_key="key-g", config_model=MODEL)
    rig = GatewayRig()
    usage = _usage(RECORDED_CHILD_CALLS[-1])
    rig.transport.events = [reply_events("ok", usage=usage)]
    binding = rig.pin(routes=(route,), budget=RunBudget(max_cost_usd=5.0, max_tokens=1_000_000))
    payload: dict[str, Any] = {"model": "engine-model", "messages": [{"role": "user", "content": "x" * 4_000}]}
    _ = await rig.gateway.chat(rig.token(binding), {}, body_of(payload))
    spent = (await rig.ledger.remaining(RunAccountRef("org-1", "run-1"))).committed
    assert spent == usage_spend(usage, MODEL, charges_usd=True)
    assert spent.tokens == budget_tokens(usage, MODEL) < usage.input_tokens


# ---- the pre-run race: the reserve is sized before the run's account exists ----


async def test_the_first_hold_is_sized_on_an_account_not_opened_yet(caplog: pytest.LogCaptureFixture) -> None:
    """A recorded parent logged ``budget.wrap_up_unsized BudgetAccountMissing``: the reserve is held before the run's
    first call, and the account opens on its first reservation. The hold now opens it and sizes from it."""
    ledger = InMemoryBudgetLedger(ManualClock())
    run = RunAccountRef("org-1", "fresh")
    ledger.pin(run, PinnedBudget(Cap(None, 40_000)))
    reserve = WrapUpReserve(accounts=ledger, account=run, pricing_model=MODEL, ids=SeededIds(3), size=WrapUpSize(20_000, 8_000))
    with caplog.at_level(logging.WARNING):
        assert await reserve.hold()
    assert "budget.wrap_up_unsized" not in caplog.text
    # Sized to a tenth of the 40,000 the account had, as a hold on an open account is.
    assert reserve.amount.tokens <= 4_000
    left = await ledger.remaining(run)
    assert left.reserved.tokens == reserve.amount.tokens and left.committed == ZERO


# ---- the engine run's wrap-up answer ----


class _Harness(EngineWrapUpHarness):
    def __init__(self) -> None:
        self.ledger = InMemoryBudgetLedger(ManualClock())
        self.inner = RecordedTransport()
        self.inner.events = [reply_events("Pick PP-102 first.", usage=TokenUsage(input_tokens=900, output_tokens=40))]
        self._runs = 0
        self._last = RunAccountRef("org-1", "none")

    def _port(self, cap_tokens: int) -> ModelEngineWrapUp:
        self._runs += 1
        run = RunAccountRef("org-1", f"engine-{self._runs}")
        self.ledger.pin(run, PinnedBudget(Cap(None, cap_tokens)))
        self._last = run
        reserve = WrapUpReserve(
            accounts=self.ledger, account=run, pricing_model=MODEL, ids=SeededIds(self._runs), size=WrapUpSize(4_000, 1_000)
        )
        metered = MeteredTransport(
            self.inner, accounts=self.ledger, account=run, pricing_model=MODEL, ids=SeededIds(100 + self._runs), wrap_up=reserve
        )
        return ModelEngineWrapUp(reserve=reserve, transport=metered)

    def fresh(self) -> ModelEngineWrapUp:
        return self._port(500_000)

    def starved(self) -> ModelEngineWrapUp:
        return self._port(5)

    async def reserved_tokens(self) -> int:
        return (await self.ledger.remaining(self._last)).reserved.tokens

    async def committed_tokens(self) -> int:
        return (await self.ledger.remaining(self._last)).committed.tokens

    def prompts(self) -> list[str]:
        return ["\n".join(str(m.content) for m in call.messages) for call in self.inner.calls]


class TestModelEngineWrapUp(EngineWrapUpContract):
    @pytest.fixture
    def engine_wrap_ups(self) -> EngineWrapUpHarness:
        return _Harness()


async def test_the_wrap_up_prompt_keeps_the_newest_results_that_fit() -> None:
    from harness_core.engines.durable.ports import GatheredResult
    from harness_core.engines.durable.wrap_up import wrap_up_prompt

    many = [GatheredResult(f"search{n}", f"PP-{n} " + "x" * 3_000) for n in range(40)]
    prompt = wrap_up_prompt(REQUEST, many, input_tokens=4_000)
    assert REQUEST in prompt and "PP-39" in prompt and "PP-0 " not in prompt
    assert "earlier results did not fit" in prompt
    assert len(prompt) // 4 <= 4_000
    assert "PP-101" in wrap_up_prompt(REQUEST, GATHERED, input_tokens=4_000)
