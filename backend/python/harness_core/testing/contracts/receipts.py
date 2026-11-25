"""Contract suite for ``ExecutionReceipts`` adapters (P5.11, ER-1..ER-5).

Subclass ``ExecutionReceiptsContract`` with a ``receipts`` fixture (the adapter
under test) and, for ER-5, a ``stored_names`` fixture returning whatever names
the adapter's backing store holds (``[]`` for one that stores no names).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from harness_core.effects.receipts import ExecutionReceipt, ReceiptRegressed

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from harness_core.effects.receipts import ExecutionReceipts

KEY = "org-1:run-1:t1.0"
AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


class ExecutionReceiptsContract:
    async def test_er1_a_missing_key_has_no_receipt(self, receipts: ExecutionReceipts) -> None:
        assert await receipts.get(KEY) is None

    async def test_er2_a_receipt_reads_back_as_written(self, receipts: ExecutionReceipts) -> None:
        content: JsonValue = {"stdout": "42\n", "exit_code": 0, "artifacts": [{"name": "out.csv", "version": 1}]}
        await receipts.put(KEY, ExecutionReceipt("started", AT))
        await receipts.put(KEY, ExecutionReceipt("executed", AT, content))
        await receipts.put(KEY, ExecutionReceipt("done", AT, content, is_error=False))
        assert await receipts.get(KEY) == ExecutionReceipt("done", AT, content)

    async def test_er3_states_only_move_forward(self, receipts: ExecutionReceipts) -> None:
        await receipts.put(KEY, ExecutionReceipt("done", AT, "ok"))
        with pytest.raises(ReceiptRegressed):
            await receipts.put(KEY, ExecutionReceipt("started", AT))
        with pytest.raises(ReceiptRegressed):
            await receipts.put(KEY, ExecutionReceipt("executed", AT, "late"))
        await receipts.put(KEY, ExecutionReceipt("done", AT, "ok"))
        assert await receipts.get(KEY) == ExecutionReceipt("done", AT, "ok")

    async def test_er4_keys_are_isolated(self, receipts: ExecutionReceipts) -> None:
        await receipts.put(KEY, ExecutionReceipt("started", AT))
        await receipts.put(KEY + "x", ExecutionReceipt("done", AT, "other", is_error=True))
        assert await receipts.get(KEY) == ExecutionReceipt("started", AT)
        assert await receipts.get(KEY + "x") == ExecutionReceipt("done", AT, "other", is_error=True)
        assert await receipts.get("org-2:run-1:t1.0") is None

    async def test_er5_the_key_is_never_stored_in_clear(
        self, receipts: ExecutionReceipts, stored_names: Callable[[], list[str]]
    ) -> None:
        await receipts.put(KEY, ExecutionReceipt("started", AT))
        assert all("run-1" not in name and "org-1" not in name for name in stored_names())


__all__ = ["AT", "KEY", "ExecutionReceiptsContract"]
