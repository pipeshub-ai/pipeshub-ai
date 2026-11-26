"""EA-5: the reference ``SourceLedger`` over core item labels (P2.5a)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.effects.flow import GrantingLedger
from harness_core.engines.durable.ledger import ItemLabelLedger
from harness_core.testing.contracts.source_ledger import (
    LedgerWorld,
    SourceLedgerContract,
    Stream,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class _ItemLabelWorld:
    def __init__(self) -> None:
        self._ledger = ItemLabelLedger()

    @property
    def ledger(self) -> ItemLabelLedger:
        return self._ledger

    def recorded(self, stream: Stream, run_id: str, invocation_id: str, record_ids: Sequence[str]) -> None:
        _ = stream.add(
            "tool.outcome",
            {
                "invocationId": invocation_id,
                "status": "ok",
                "labels": {"integrity": "internal", "confidentiality": {"readers": "org", "sources": list(record_ids)}},
            },
            run_id=run_id,
        )

    def unrecorded(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        _ = stream.add(
            "tool.outcome",
            {"invocationId": invocation_id, "status": "ok", "labels": {"integrity": "internal"}},
            run_id=run_id,
        )

    def refused(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        _ = stream.add("tool.denied", {"invocationId": invocation_id, "rule": "policy"}, run_id=run_id)
        self.unrecorded(stream, run_id, invocation_id)

    def ended(self, stream: Stream, run_id: str) -> None:
        _ = stream.add("run.completed", {"outcome": "answered"}, run_id=run_id, writer="node")


class TestItemLabelLedger(SourceLedgerContract):
    @pytest.fixture
    def ledger_world(self) -> LedgerWorld:
        return _ItemLabelWorld()


class _GrantingWorld(_ItemLabelWorld):
    def __init__(self) -> None:
        super().__init__()
        self._granting = GrantingLedger(self._ledger)

    @property
    def ledger(self) -> GrantingLedger:  # type: ignore[override]
        return self._granting


class TestGrantingLedger(SourceLedgerContract):
    """UX1: the ledger that also keeps conversation grants answers as the ledger it wraps."""

    @pytest.fixture
    def ledger_world(self) -> LedgerWorld:
        return _GrantingWorld()  # type: ignore[return-value]


def test_a_cancelled_outcome_is_the_harness_text() -> None:
    world = _ItemLabelWorld()
    stream = Stream()
    _ = stream.add("tool.outcome", {"invocationId": "i", "status": "cancelled", "labels": {"integrity": "trusted"}}, run_id="r")
    world.ledger.apply(stream.events)
    assert world.ledger.source_records("r") == frozenset()
