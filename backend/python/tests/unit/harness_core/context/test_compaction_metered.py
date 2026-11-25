"""P4.2 LD-9: the compaction call goes through the run's metered model (P6.5); refused, the run
ends with the ledger's reason and nothing is compacted (refuse, never downgrade)."""

from __future__ import annotations

from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import ZERO, Amount, Cap, RunAccountRef
from harness_core.context import ModelCompaction
from harness_core.kernel.core.responses import TokenUsage
from harness_core.models.transport_model import TransportModel
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events
from tests.unit.harness_core.durable.support import World, scripted

from .kernel_support import answered, compiler, looked_up

ACCOUNT = RunAccountRef("org1", "run1")
USAGES = [300, 300, 300, 300, 8_500, 300]


async def _world(cap: Cap) -> tuple[World, InMemoryBudgetLedger, RecordedTransport, ModelCompaction]:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ACCOUNT, cap)
    inner = RecordedTransport([reply_events("Goal: look things up.", usage=TokenUsage(input_tokens=400, output_tokens=40))])
    metered = MeteredTransport(inner, accounts=ledger, account=ACCOUNT, pricing_model="m", ids=SeededIds(0))
    world = World()
    world.admit(max_turns=30)
    return world, ledger, inner, ModelCompaction(TransportModel(metered))


async def test_ld9_the_compaction_call_is_reserved_and_charged_on_the_run_s_account() -> None:
    world, ledger, inner, strategy = await _world(Cap(None, 1_000_000))
    _, port = scripted(*(looked_up(u, f"q{i}") for i, u in enumerate(USAGES)), answered())
    assert (await world.run(world.kernel(port, context=compiler(strategy=strategy)))).status == "completed"
    [compacted] = world.events("context.compacted")
    assert (await world.blob(compacted.data["summaryRef"])).decode() == "Goal: look things up."
    assert len(inner.calls) == 1 and inner.calls[0].tools is None
    left = await ledger.remaining(ACCOUNT)
    assert (left.committed.tokens, left.reserved) == (440, ZERO)


async def test_ld9_with_the_budget_exhausted_nothing_is_compacted_and_the_run_answers_cut_short() -> None:
    """BUDGETEND: a compaction the budget cannot pay for ends the working phase; with no wrap-up reserve held the
    run answers without a model, from what it gathered, flagged ``budget``: never a bare failure."""
    world, ledger, inner, strategy = await _world(Cap(None, 1_000))
    held = await ledger.reserve(ACCOUNT, "spent", Amount(0, 1_000), minimum=ZERO, ttl_s=60)
    _ = await ledger.commit(held, Amount(0, 1_000))
    _, port = scripted(*(looked_up(u, f"q{i}") for i, u in enumerate(USAGES)), answered())
    disposition = await world.run(world.kernel(port, context=compiler(strategy=strategy)))
    assert disposition.status == "completed"
    assert world.events("context.compacted") == []
    assert inner.calls == []
    # The main model was not called after the refused compaction.
    assert len(port.calls) == 5
    [completed] = world.events("run.completed")
    assert completed.data == {"outcome": "answered", "truncatedBy": "budget"}
