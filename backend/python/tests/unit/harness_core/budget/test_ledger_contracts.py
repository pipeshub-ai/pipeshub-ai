"""P6.5 LV, BAC, BLC: the shared table and the ledger contracts against ``InMemoryBudgetLedger``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing.contracts.budget import (
    BudgetAccountsContract,
    BudgetLedgerContract,
)
from tests.unit.harness_core.budget.support import InMemoryBudgetHarness, scenarios

if TYPE_CHECKING:
    from harness_core.budget.memory import InMemoryBudgetLedger


class TestInMemoryBudgetAccounts(BudgetAccountsContract):
    @pytest.fixture
    def budget_harness(self) -> InMemoryBudgetHarness:
        return InMemoryBudgetHarness()

    @pytest.fixture
    def budget_scenarios(self) -> list[dict[str, object]]:
        return scenarios()


class TestInMemoryBudgetLedger(BudgetLedgerContract):
    @pytest.fixture
    def harness(self) -> InMemoryBudgetHarness:
        return InMemoryBudgetHarness()

    @pytest.fixture
    def budget_harness(self, harness: InMemoryBudgetHarness) -> InMemoryBudgetHarness:
        return harness

    @pytest.fixture
    def budget_ledger(self, harness: InMemoryBudgetHarness) -> InMemoryBudgetLedger:
        return harness.ledger

    @pytest.fixture
    def budget_scenarios(self) -> list[dict[str, object]]:
        return scenarios()
