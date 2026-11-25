"""SPENDCONT: the token cap is a runaway backstop; the USD cap decides.

A recorded run: a Codex ``code_task`` child gathering a project's security bugs made 30
model calls in one engine turn, reading 3,469,658 prompt tokens of which 2,878,113 came from the provider's cache,
and 27,772 output tokens: USD 0.117 at gpt-6-luna's prices, 907,144 tokens as ENGBUDGET weighs a cached read. With
its parent's spend the tree committed 1,001,452 tokens for USD 0.13, and the gateway refused the next call
``budget_insufficient`` on the 1,000,000-token cap with USD 4.87 of 5 left. The release now pins 10M tokens for
coding-engine work (an engine run, or a loop run that may hand off to one) and 2M for a loop that cannot.

``RECORDED_CALLS`` are the child's calls as the model gateway logged them (``model_gateway.call``: input, cached,
output).
"""

from __future__ import annotations

import pytest

from harness_core.access.spend import (
    OutputAllowance,
    budget_tokens,
    reservation_for,
    usage_spend,
)
from harness_core.budget.errors import BudgetExhausted, BudgetInsufficient
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import ZERO, Amount, Cap, RunAccountRef
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.clock import ManualClock

MODEL = "gpt-6-luna"
RECORDED_CALLS: tuple[tuple[int, int, int], ...] = (
    (69788, 68570, 312), (72790, 69785, 256), (76637, 72787, 664), (93252, 76634, 756), (109196, 93249, 929),
    (129167, 109193, 1143), (143819, 129164, 740), (175333, 143816, 327), (179117, 175330, 893),
    (193819, 179114, 463), (197929, 193816, 435), (221654, 197926, 979), (231880, 221651, 617),
    (257691, 0, 5212), (8044, 0, 98), (8134, 8041, 53), (11284, 8131, 499), (27514, 11281, 787),
    (42937, 27511, 369), (46704, 42934, 781), (62538, 46701, 916), (71898, 62535, 1118), (90916, 71895, 1064),
    (99977, 90913, 933), (111785, 99974, 1044), (123099, 111782, 1280), (137064, 123096, 1129),
    (145300, 137061, 1277), (159929, 145297, 1109), (170463, 159926, 1589),
)
# The parent's own spend before it handed off, and its wrap-up hold (the recorded root committed 1,001,452 in all).
PARENT = Amount(15_000, 94_300)
PARENT_RESERVE = Amount(6_400, 32_000)
NEXT_PROMPT = 180_000
ROOT = RunAccountRef("org-1", "parent")
CHILD = RunAccountRef("org-1", "child")
USD_CAP = 5_000_000


def _usage(call: tuple[int, int, int]) -> TokenUsage:
    prompt, cached, output = call
    return TokenUsage(input_tokens=prompt, output_tokens=output, cache_read_tokens=cached)


async def _tree(tokens: int) -> InMemoryBudgetLedger:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, Cap(USD_CAP, tokens))
    await ledger.open_child(ROOT, CHILD, Cap(None, None))
    held = await ledger.reserve(ROOT, "parent", PARENT, minimum=ZERO, ttl_s=60)
    _ = await ledger.commit(held, PARENT)
    _ = await ledger.reserve(ROOT, "wrapup", PARENT_RESERVE, minimum=PARENT_RESERVE, ttl_s=3_600)
    for n, call in enumerate(RECORDED_CALLS):
        spend = usage_spend(_usage(call), MODEL, charges_usd=True)
        held = await ledger.reserve(CHILD, f"call-{n}", spend, minimum=ZERO, ttl_s=60)
        _ = await ledger.commit(held, spend)
    return ledger


def test_the_recorded_child_is_cheap_in_money_and_large_in_tokens() -> None:
    assert sum(c[0] for c in RECORDED_CALLS) == 3_469_658 and sum(c[1] for c in RECORDED_CALLS) == 2_878_113
    weighed = sum(budget_tokens(_usage(c), MODEL) for c in RECORDED_CALLS)
    usd = sum(usage_spend(_usage(c), MODEL, charges_usd=True).usd_micros for c in RECORDED_CALLS)
    assert 880_000 <= weighed <= 930_000, weighed
    assert usd < 200_000, "about USD 0.12 of a USD 5 cap"


def test_the_release_pins_backstops_and_keeps_the_usd_cap() -> None:
    budgets = RELEASE_DEFAULTS.budgets
    assert budgets.max_tokens == 10_000_000 and budgets.loop_max_tokens == 2_000_000
    assert budgets.loop_max_tokens <= budgets.max_tokens
    assert budgets.max_cost_usd == 5


async def test_recorded_on_the_old_cap_the_tree_runs_out_of_tokens() -> None:
    requested, minimum = reservation_for(NEXT_PROMPT, MODEL, OutputAllowance(), charges_usd=True)
    with pytest.raises((BudgetInsufficient, BudgetExhausted)):
        ledger = await _tree(1_000_000)
        _ = await ledger.reserve(CHILD, "call-30", requested, minimum=minimum, ttl_s=60)


async def test_recorded_on_the_engine_backstop_the_usd_cap_decides() -> None:
    ledger = await _tree(RELEASE_DEFAULTS.budgets.max_tokens)
    requested, minimum = reservation_for(NEXT_PROMPT, MODEL, OutputAllowance(), charges_usd=True)
    held = await ledger.reserve(CHILD, "call-30", requested, minimum=minimum, ttl_s=60)
    assert held.granted.tokens >= minimum.tokens
    left = (await ledger.remaining(ROOT)).available
    assert left.tokens is not None and left.tokens > 8_000_000
    assert left.usd_micros is not None and left.usd_micros > 4_500_000


async def test_the_usd_cap_still_stops_a_runaway() -> None:
    """Tokens are no longer the binding cap; money is: past USD 5 the next call is refused whatever tokens remain."""
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, Cap(USD_CAP, RELEASE_DEFAULTS.budgets.max_tokens))
    spent = Amount(USD_CAP - 1_000, 2_000_000)
    held = await ledger.reserve(ROOT, "spent", spent, minimum=ZERO, ttl_s=60)
    _ = await ledger.commit(held, spent)
    requested, minimum = reservation_for(NEXT_PROMPT, MODEL, OutputAllowance(), charges_usd=True)
    with pytest.raises(BudgetInsufficient):
        _ = await ledger.reserve(ROOT, "next", requested, minimum=minimum, ttl_s=60)
