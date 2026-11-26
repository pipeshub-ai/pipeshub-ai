"""P6.5 DB-1..DB-3: a deep run and its durable children draw from one ledger tree.

The K-L harness (``run_deep_durably``: the real assembler, the kernel, the in-memory
journal) with every run's model calls charged to an in-memory ledger: the root's
account opens with the pinned budget (tokens lowered so the scripted run crosses
it), each child's under the root with the share ``LedgerChildBudget`` gave it, as
Node opens them at admission. Every scripted call reports 1,000 input and 1,000
output tokens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.budget.errors import BudgetRefused
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.tree import child_cap, usd_micros
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent
from harness_core.models.transports.base import LLMTransport
from harness_core.testing import ManualClock
from tests.contracts.deep_scenarios import DEEP_SCENARIOS
from tests.contracts.test_durable_deep_goldens import RUN, SESSION, run_deep_durably
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

CALL = TokenUsage(input_tokens=1_000, output_tokens=1_000)
PER_CALL = 2_000


class _Usage(LLMTransport):
    """Every answer reports ``CALL`` and counts the calls that reached the provider."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner
        self.calls = 0

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def complete(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.calls += 1
        response = await self._inner.complete(*args, **kwargs)
        return response.model_copy(update={"usage": CALL})

    async def complete_structured(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.calls += 1
        response = await self._inner.complete_structured(*args, **kwargs)
        return response.model_copy(update={"usage": CALL})

    def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:  # noqa: ANN401
        return self._stream(*args, **kwargs)

    async def _stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:  # noqa: ANN401
        self.calls += 1
        async for event in self._inner.stream(*args, **kwargs):
            yield (
                StreamCompleteEvent(response=event.response.model_copy(update={"usage": CALL}))
                if isinstance(event, StreamCompleteEvent)
                else event
            )


class _Tree:
    """The ledger as Node keeps it for the run tree: the root opened from its pin (tokens
    capped at ``root_tokens``), a child under the root with its pinned share."""

    def __init__(self, root_tokens: int | None) -> None:
        self.ledger = InMemoryBudgetLedger(ManualClock())
        self.root_tokens = root_tokens
        self.refused: list[str] = []
        self.root_cap = Cap()
        self.transport: _Usage | None = None

    def wrap(self, inner: Any) -> _Usage:  # noqa: ANN401
        self.transport = _Usage(inner)
        return self.transport

    def account(self, run_id: str) -> RunAccountRef:
        return RunAccountRef(w.ORG, run_id)

    async def accounts(self, claim: Any) -> InMemoryBudgetLedger:  # noqa: ANN401
        budgets = claim.spec.budgets
        pinned = Cap(usd_micros(budgets.max_cost_usd), budgets.max_tokens)
        if claim.run_id == RUN:
            self.root_cap = Cap(pinned.usd_micros, self.root_tokens or pinned.tokens)
            await self.ledger.open_root(self.account(RUN), self.root_cap)
        else:
            try:
                await self.ledger.open_child(
                    self.account(RUN), self.account(claim.run_id), child_cap(self.root_cap, pinned)
                )
            except BudgetRefused as refused:
                # Node refuses such a child at admission (CHILD_BUDGET_EXCEEDS_REMAINING); this
                # journal admitted it already, so its calls find no account.
                self.refused.append(refused.reason_code)
        return self.ledger


async def _run(name: str, root_tokens: int | None) -> tuple[Any, _Tree]:
    tree = _Tree(root_tokens)
    deep = next(d for d in DEEP_SCENARIOS if d.name == name)
    run = await run_deep_durably(deep, transport_wrapper=tree.wrap, budget=tree.accounts)
    return run, tree


async def test_db1_an_unlimited_tree_charges_every_call_to_the_root() -> None:
    run, tree = await _run("deep_dependent_steps", None)
    assert tree.transport is not None and tree.transport.calls > 0
    root = await tree.ledger.remaining(tree.account(RUN))
    assert root.committed.tokens == PER_CALL * tree.transport.calls
    assert root.reserved.tokens == 0
    for child in run.children():
        spent = (await tree.ledger.remaining(tree.account(child))).committed.tokens
        assert 0 < spent < root.committed.tokens


async def test_db2_a_deep_runs_children_cannot_exceed_the_root_cap() -> None:
    _, unlimited = await _run("deep_dependent_steps", None)
    assert unlimited.transport is not None
    whole = PER_CALL * unlimited.transport.calls
    cap = whole // 2
    run, tree = await _run("deep_dependent_steps", cap)
    assert tree.transport is not None
    root = await tree.ledger.remaining(tree.account(RUN))
    # Each call reserves at least its input; one call may cross by its uncovered output.
    assert root.committed.tokens <= cap + PER_CALL
    assert _budget_failures(run)
    assert tree.transport.calls < unlimited.transport.calls
    for child in run.children():
        try:
            spent = await tree.ledger.remaining(tree.account(child))
        except BudgetRefused:
            continue
        assert spent.committed.tokens <= root.committed.tokens


def _budget_failures(run: Any) -> list[str]:  # noqa: ANN401
    """The runs of the tree that ended because the tree could not pay for a call: failed for it, or (BUDGETEND)
    completed cut short by it, answering from what they had gathered."""
    return [
        str(e.data.get("errorType") or e.data.get("truncatedBy"))
        for stream in (SESSION, *run.children())
        for e in run.events(stream)
        if (e.type == "run.failed" and str(e.data.get("errorType")).startswith("budget_"))
        or (e.type == "run.completed" and e.data.get("truncatedBy") == "budget")
    ]


async def test_db3_the_run_that_cannot_pay_ends_named_for_it() -> None:
    run, tree = await _run("deep_single_child", PER_CALL)
    assert _budget_failures(run)
    root = await tree.ledger.remaining(tree.account(RUN))
    assert root.committed.tokens <= 2 * PER_CALL
