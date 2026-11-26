"""The in-memory journal passes the ``ChildRunPort`` contract (CRC), and survives a restart."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.journal import InMemoryJournal
from harness_core.journal.children import ChildRequest
from harness_core.testing import ManualClock
from harness_core.testing.contracts.children import ChildRunContract, ChildRunHarness
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from pathlib import Path

ORG = "org1"


class MemoryChildHarness:
    def __init__(self, oplog: Path | None = None) -> None:
        self.memory = InMemoryJournal(clock=ManualClock(), oplog=oplog)

    @property
    def journal(self) -> InMemoryJournal:
        return self.memory

    @property
    def children(self) -> InMemoryJournal:
        return self.memory

    @property
    def org_id(self) -> str:
        return ORG

    def admit_parent(self, run_id: str, *, session_id: str, max_children: int) -> None:
        self.memory.admit(
            run_id,
            org_id=ORG,
            session_id=session_id,
            spec=minimal_run_spec(max_children=max_children),
            input_text="parent task",
        )


class TestMemoryChildRunContract(ChildRunContract):
    @pytest.fixture
    def harness(self) -> ChildRunHarness:
        return MemoryChildHarness()


async def test_spawns_replay_from_the_op_log(tmp_path: Path) -> None:
    oplog = tmp_path / "journal.jsonl"
    first = MemoryChildHarness(oplog)
    first.admit_parent("p", session_id="s", max_children=1)
    claim = await first.journal.claim("p", worker_id="w")
    assert claim is not None
    admitted = await first.children.spawn(claim.token, ChildRequest("t1.0", "go"))
    restarted = MemoryChildHarness(oplog)
    assert restarted.journal.children_of("p") == [admitted.child_run_id]
    assert restarted.journal.events("s") == first.journal.events("s")
    assert restarted.journal.events(admitted.child_run_id) == first.journal.events(
        admitted.child_run_id
    )
