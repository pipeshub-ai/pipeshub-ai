"""P6.5 MT-1..MT-6: a run's own model calls charged to its ledger account; K-L's child share from it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.budget.errors import (
    BudgetExhausted,
    BudgetInsufficient,
    BudgetUnavailable,
)
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import (
    LedgerChildBudget,
    MeteredTransport,
    RunBudgetView,
)
from harness_core.budget.types import ZERO, Amount, Cap, LedgerUsage, RunAccountRef
from harness_core.journal.children import child_budget_share
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

if TYPE_CHECKING:
    from harness_core.kernel.durable.delegation import ChildBudget

ROOT = RunAccountRef("org-1", "root")
CHILD = RunAccountRef("org-1", "child")
HI = [UserMessage(content="hi")]


async def _metered(cap: Cap = Cap(None, 100_000), **options: object) -> tuple[MeteredTransport, RecordedTransport, InMemoryBudgetLedger]:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, cap)
    inner = RecordedTransport()
    inner.events = [reply_events("ok", usage=TokenUsage(input_tokens=100, output_tokens=20))]
    metered = MeteredTransport(
        inner, accounts=ledger, account=ROOT, pricing_model="gpt-5.4", ids=SeededIds(0), **options  # type: ignore[arg-type]
    )
    return metered, inner, ledger


@pytest.mark.parametrize("streaming", [False, True])
async def test_mt1_a_call_is_reserved_then_charged_its_usage(streaming: bool) -> None:
    metered, inner, ledger = await _metered()
    if streaming:
        _ = [e async for e in metered.stream(HI)]
    else:
        await metered.complete(HI)
    left = await ledger.remaining(ROOT)
    assert (left.committed.tokens, left.reserved) == (120, ZERO)
    assert left.committed.usd_micros == 550  # gpt-5.4: 100 x $2.50 + 20 x $15 per Mtok
    assert metered.view.remaining == left
    assert len(inner.calls) == 1
    # EV-10: the run's own call tells the ledger its usage too, as the model gateway's calls do.
    assert left.used == LedgerUsage(100, 20)


async def test_mt2_a_tree_that_cannot_pay_refuses_before_the_provider() -> None:
    metered, inner, ledger = await _metered(Cap(None, 5))
    with pytest.raises(BudgetInsufficient):
        await metered.complete([UserMessage(content="x" * 400)])
    held = await ledger.reserve(ROOT, "other", Amount(0, 5), minimum=ZERO, ttl_s=60)
    await ledger.commit(held, Amount(0, 5))
    with pytest.raises(BudgetExhausted):
        await metered.complete(HI)
    assert inner.calls == []


@pytest.mark.parametrize("streaming", [False, True])
async def test_mt3_a_provider_error_before_any_output_releases(streaming: bool) -> None:
    from harness_core.kernel.core.exceptions import TransportError

    metered, inner, ledger = await _metered()
    inner.fail = TransportError("down", status_code=503)
    with pytest.raises(TransportError):
        if streaming:
            _ = [e async for e in metered.stream(HI)]
        else:
            await metered.complete(HI)
    left = await ledger.remaining(ROOT)
    assert (left.committed, left.reserved) == (ZERO, ZERO)


async def test_mt4_a_stream_that_breaks_is_charged_its_estimate() -> None:
    from harness_core.kernel.core.exceptions import TransportError

    metered, inner, ledger = await _metered()
    inner.events = [TextDeltaEvent(delta="partial " * 10), reply_events("never")]
    inner.fail = TransportError("boom", status_code=503)
    inner.fail_after = 1
    with pytest.raises(TransportError):
        _ = [e async for e in metered.stream(HI)]
    left = await ledger.remaining(ROOT)
    assert left.committed.tokens > 0 and left.reserved == ZERO


async def test_mt5_an_unreachable_ledger_is_unavailable() -> None:
    class Down:
        async def reserve(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("node down")

    metered = MeteredTransport(
        RecordedTransport(), accounts=Down(), account=ROOT, pricing_model="m", ids=SeededIds(0)  # type: ignore[arg-type]
    )
    with pytest.raises(BudgetUnavailable):
        await metered.complete(HI)


async def test_mt6_a_child_draws_from_its_parent() -> None:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, Cap(None, 1_000))
    await ledger.open_child(ROOT, CHILD, Cap(None, 300))
    inner = RecordedTransport()
    inner.events = [reply_events("ok", usage=TokenUsage(input_tokens=100, output_tokens=100))]
    child = MeteredTransport(inner, accounts=ledger, account=CHILD, pricing_model="m", ids=SeededIds(1))
    await child.complete(HI)
    assert (await ledger.remaining(ROOT)).committed.tokens == 200
    await child.complete(HI)
    with pytest.raises(BudgetExhausted):
        await child.complete(HI)


def test_cb1_the_ledger_child_budget_is_k_ls_seam() -> None:
    view = RunBudgetView()
    budget: ChildBudget = LedgerChildBudget(view)
    pinned = {"parent_max_turns": 15, "parent_max_tokens": 1_000_000, "definition_max_turns": 8, "batch": 3}
    assert budget.share(**pinned) == child_budget_share(**pinned)  # nothing known yet: the pinned budget


async def test_cb2_shares_come_from_what_the_parent_has_left() -> None:
    metered, _, ledger = await _metered(Cap(None, 100_000))
    held = await ledger.reserve(ROOT, "spent", Amount(0, 60_000), minimum=ZERO, ttl_s=60)
    await ledger.commit(held, Amount(0, 60_000))
    await metered.complete(HI)
    share = LedgerChildBudget(metered.view).share(
        parent_max_turns=15, parent_max_tokens=100_000, definition_max_turns=8, batch=1
    )
    assert share.max_tokens == (100_000 - 60_000 - 120) // 2
    assert share.max_turns == 8
