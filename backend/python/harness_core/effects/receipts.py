"""Receipts of an execution a tool deduplicates itself (P5.11).

A tool whose effect stays inside its own sandbox (``run_code``) cannot be
reconciled by reading a provider: what it produced dies with the attempt
that ran it. It records, under the call's idempotency key (the same on every
attempt), how far the call got, so a re-driven attempt answers from the
receipt instead of running the program a second time:

* ``started``: before the program runs; a re-drive that finds only this
  knows the program may have run and its output is gone;
* ``executed``: the program returned; its output is in ``content``;
* ``done``: everything the call does after the program finished is done;
  ``content`` is the result the model is shown.

States only move forward. Product-neutral: the store is a port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from pydantic import JsonValue

ReceiptState = Literal["started", "executed", "done"]
RECEIPT_ORDER: Final[dict[ReceiptState, int]] = {"started": 0, "executed": 1, "done": 2}


@dataclass(frozen=True)
class ExecutionReceipt:
    state: ReceiptState
    started_at: datetime
    content: JsonValue = None
    is_error: bool = False


class ReceiptRegressed(Exception):
    """A receipt written over one further along: a late writer, never applied."""

    reason_code = "receipt_regressed"


class ExecutionReceipts(Protocol):
    async def get(self, key: str) -> ExecutionReceipt | None: ...

    async def put(self, key: str, receipt: ExecutionReceipt) -> None:
        """Raises ``ReceiptRegressed`` when ``key`` already holds a later state."""
        ...


def check_forward(current: ExecutionReceipt | None, receipt: ExecutionReceipt) -> None:
    if current is not None and RECEIPT_ORDER[receipt.state] < RECEIPT_ORDER[current.state]:
        raise ReceiptRegressed(f"{current.state} -> {receipt.state}")


class InMemoryExecutionReceipts:
    def __init__(self) -> None:
        self.receipts: dict[str, ExecutionReceipt] = {}

    async def get(self, key: str) -> ExecutionReceipt | None:
        return self.receipts.get(key)

    async def put(self, key: str, receipt: ExecutionReceipt) -> None:
        check_forward(self.receipts.get(key), receipt)
        self.receipts[key] = receipt


__all__ = [
    "RECEIPT_ORDER",
    "ExecutionReceipt",
    "ExecutionReceipts",
    "InMemoryExecutionReceipts",
    "ReceiptRegressed",
    "ReceiptState",
    "check_forward",
]
