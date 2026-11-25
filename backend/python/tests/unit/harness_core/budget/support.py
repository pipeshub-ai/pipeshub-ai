"""The in-memory ledger as a ``BudgetHarness``, and the shared table's path."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import RunAccountRef
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.budget import load_budget_scenarios

if TYPE_CHECKING:
    from harness_core.budget.types import Cap, PinnedBudget, Remaining

VECTORS = Path(__file__).resolve().parents[6] / "contracts/harness/vectors/budget-ledger.json"
TENANT = "org-v"


def scenarios() -> list[dict[str, object]]:
    return load_budget_scenarios(VECTORS)


class InMemoryBudgetHarness:
    def __init__(self) -> None:
        self.clock = ManualClock()
        self.ledger = InMemoryBudgetLedger(self.clock)

    @property
    def accounts(self) -> InMemoryBudgetLedger:
        return self.ledger

    def account(self, run: str) -> RunAccountRef:
        return RunAccountRef(TENANT, run)

    def credential(self, account: RunAccountRef) -> str | None:
        return None

    async def open_root(self, account: RunAccountRef, cap: Cap) -> None:
        await self.ledger.open_root(account, cap)

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        return await self.ledger.open_child(parent, child, cap)

    async def pin(self, account: RunAccountRef, pinned: PinnedBudget) -> None:
        self.ledger.pin(account, pinned)

    async def close(self, account: RunAccountRef) -> None:
        await self.ledger.close(account)

    async def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
