"""``InMemoryExecutionReceipts`` against the receipt contract (P5.11 RC-6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.effects.receipts import InMemoryExecutionReceipts
from harness_core.testing.contracts.receipts import ExecutionReceiptsContract

if TYPE_CHECKING:
    from collections.abc import Callable


class TestInMemoryExecutionReceipts(ExecutionReceiptsContract):
    @pytest.fixture
    def receipts(self) -> InMemoryExecutionReceipts:
        return InMemoryExecutionReceipts()

    @pytest.fixture
    def stored_names(self) -> Callable[[], list[str]]:
        return lambda: []
