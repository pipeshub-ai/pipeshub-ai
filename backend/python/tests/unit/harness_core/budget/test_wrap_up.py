"""BUDGETEND: the wrap-up reserve is part of the run's budget, not a bypass of it.

The reserve is held on the run's ledger account before the working phase spends, so a working call that would eat
into it is refused by the ledger itself, and the answer-only turn draws on the held reservation: it is never refused
and spends at most what was held (plus what its own call reports beyond its estimate).
"""

from __future__ import annotations

import pytest

from harness_core.budget.errors import BudgetExhausted, BudgetInsufficient
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import ZERO, Amount, Cap, PinnedBudget, RunAccountRef
from harness_core.budget.wrap_up import (
    WRAP_UP_INPUT_TOKENS,
    WRAP_UP_MIN_INPUT_TOKENS,
    WRAP_UP_MIN_OUTPUT_TOKENS,
    WRAP_UP_OUTPUT_TOKENS,
    WRAP_UP_RENEW_MARGIN_S,
    WRAP_UP_TTL_S,
    WrapUpReserve,
    WrapUpSize,
)
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.wrap_up import WrapUpContract
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

RUN = RunAccountRef("org-1", "run-1")
MODEL = "gpt-5.4"
SIZE = WrapUpSize(input_tokens=2_000, output_tokens=500)


async def _world(cap_tokens: int) -> tuple[InMemoryBudgetLedger, WrapUpReserve, MeteredTransport, RecordedTransport, ManualClock]:
    clock = ManualClock()
    ledger = InMemoryBudgetLedger(clock)
    await ledger.open_root(RUN, Cap(None, cap_tokens))
    reserve = WrapUpReserve(accounts=ledger, account=RUN, pricing_model=MODEL, ids=SeededIds(1), size=SIZE, clock=clock)
    inner = RecordedTransport()
    inner.events = [reply_events("ok", usage=TokenUsage(input_tokens=100, output_tokens=20))]
    metered = MeteredTransport(
        inner, accounts=ledger, account=RUN, pricing_model=MODEL, ids=SeededIds(2), wrap_up=reserve
    )
    return ledger, reserve, metered, inner, clock


def test_the_size_is_capped_by_the_window() -> None:
    assert WrapUpSize.for_window(None) == WrapUpSize(WRAP_UP_INPUT_TOKENS, WRAP_UP_OUTPUT_TOKENS)
    assert WrapUpSize.for_window(16_000).input_tokens == 8_000, "at most half of a small window"
    assert WrapUpSize.for_window(1_000_000).input_tokens == WRAP_UP_INPUT_TOKENS


async def test_the_reserve_is_held_on_the_account_and_working_calls_stop_short_of_it() -> None:
    ledger, reserve, metered, inner, _ = await _world(cap_tokens=27_500)
    assert await reserve.hold()
    left = await ledger.remaining(RUN)
    assert left.reserved.tokens == SIZE.input_tokens + SIZE.output_tokens
    # 25,000 tokens are free beside the reserve: a call whose input alone needs more, even read from the cache
    # (a tenth of a fresh token on gpt-5.4, ENGBUDGET), is refused by the ledger. 300,000 words are 300,000 tokens
    # by gpt-5.4's own tokenizer (WP-66), as by the fallback; a run of one letter is not (BPE merges it).
    with pytest.raises((BudgetInsufficient, BudgetExhausted)):
        await metered.complete([UserMessage(content=" word" * 300_000)])
    assert inner.calls == [], "refused before the provider"
    assert (await ledger.remaining(RUN)).reserved.tokens == SIZE.input_tokens + SIZE.output_tokens


async def test_the_armed_call_draws_on_the_held_reserve_without_a_new_reservation() -> None:
    ledger, reserve, metered, inner, _ = await _world(cap_tokens=27_500)
    assert await reserve.hold()
    # Spend what is left beside the reserve: the account has nothing more to give a new reservation.
    other = await ledger.reserve(RUN, "work", Amount(0, 25_000), minimum=ZERO, ttl_s=60)
    _ = await ledger.commit(other, Amount(0, 25_000))
    # Reserved but unspent, the ledger calls it insufficient: either way the working phase stops (BUDGET_STOPS).
    with pytest.raises((BudgetInsufficient, BudgetExhausted)):
        await metered.complete([UserMessage(content="hi")])
    assert reserve.arm()
    response = await metered.complete([UserMessage(content="answer from what you have")])
    assert response.message.text == "ok"
    left = await ledger.remaining(RUN)
    assert left.reserved == ZERO and left.committed.tokens == 25_000 + 120
    assert not reserve.held and not reserve.arm(), "drawn once"


async def test_a_small_budget_holds_a_smaller_reserve() -> None:
    """At most a tenth of what the account has left (a deep run's sub-agent share keeps most of it for work)."""
    ledger, reserve, _, _, _ = await _world(cap_tokens=40_000)
    big = WrapUpReserve(
        accounts=ledger, account=RUN, pricing_model=MODEL, ids=SeededIds(4), size=WrapUpSize(20_000, 8_000)
    )
    assert await big.hold()
    assert big.size.input_tokens + big.size.output_tokens <= 4_000
    assert big.size.input_tokens >= WRAP_UP_MIN_INPUT_TOKENS and big.size.output_tokens >= WRAP_UP_MIN_OUTPUT_TOKENS
    assert (await ledger.remaining(RUN)).reserved.tokens == big.amount.tokens
    assert await reserve.hold() and reserve.size == SIZE, "a reserve that fits its share is the model's size"


async def test_hold_refused_when_the_account_cannot_cover_it() -> None:
    _, reserve, _, _, _ = await _world(cap_tokens=1_000)
    assert not await reserve.hold()
    assert not reserve.held and not reserve.arm()


async def test_release_returns_the_reserve_and_a_release_twice_is_harmless() -> None:
    ledger, reserve, _, _, _ = await _world(cap_tokens=10_000)
    assert await reserve.hold()
    await reserve.release()
    await reserve.release()
    assert (await ledger.remaining(RUN)).reserved == ZERO


async def test_a_reserve_near_its_expiry_is_held_again() -> None:
    ledger, reserve, _, _, clock = await _world(cap_tokens=100_000)
    assert await reserve.hold()
    _ = clock.advance(WRAP_UP_TTL_S - WRAP_UP_RENEW_MARGIN_S + 1)
    assert await reserve.hold()
    left = await ledger.remaining(RUN)
    assert left.reserved.tokens == SIZE.input_tokens + SIZE.output_tokens, "the old hold released, one new one held"


class LedgerWrapUps:
    """``WrapUpReserve`` over the in-memory ledger: an account with room for the reserve, and one without."""

    def __init__(self) -> None:
        self.ledger = InMemoryBudgetLedger(ManualClock())
        self.account = RunAccountRef("org-c", "fresh")

    def _port(self, run: str, cap_tokens: int) -> WrapUpReserve:
        self.account = RunAccountRef("org-c", run)
        self.ledger.pin(self.account, PinnedBudget(Cap(None, cap_tokens)))
        return WrapUpReserve(accounts=self.ledger, account=self.account, pricing_model=MODEL, ids=SeededIds(3), size=SIZE)

    def fresh(self) -> WrapUpReserve:
        return self._port("fresh", 100_000)

    def starved(self) -> WrapUpReserve:
        return self._port("starved", 100)

    async def reserved_tokens(self) -> int:
        return (await self.ledger.remaining(self.account)).reserved.tokens


class TestWrapUpReserve(WrapUpContract):
    @pytest.fixture
    def wrap_ups(self) -> LedgerWrapUps:
        return LedgerWrapUps()
