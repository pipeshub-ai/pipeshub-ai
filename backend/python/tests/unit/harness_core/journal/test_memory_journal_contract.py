"""The in-memory journal passes the shared journal contract suite."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from harness_core.journal import InMemoryJournal, db_timestamp
from harness_core.testing import ManualClock
from harness_core.testing.contracts.journal import JournalContract, JournalHarness
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from harness_core.journal.types import InteractionDecision

LEASE_MS = 30_000


class MemoryHarness:
    def __init__(self) -> None:
        self.clock = ManualClock()
        self.memory = InMemoryJournal(clock=self.clock, lease_ms=LEASE_MS)

    @property
    def journal(self) -> InMemoryJournal:
        return self.memory

    @property
    def lease_s(self) -> float:
        return LEASE_MS / 1000

    def admit(
        self,
        run_id: str,
        *,
        session_id: str,
        input_text: str = "hello",
        stream_id: str | None = None,
    ) -> None:
        self.memory.admit(
            run_id,
            org_id="org1",
            session_id=session_id,
            spec=minimal_run_spec(),
            input_text=input_text,
            stream_id=stream_id,
        )

    def advance(self, seconds: float) -> None:
        _ = self.clock.advance(seconds)

    def sweep(self) -> None:
        _ = self.memory.sweep()

    def request_cancel(self, run_id: str) -> None:
        self.memory.request_cancel(run_id)

    def resolve(
        self, run_id: str, interaction_id: str, decision: InteractionDecision
    ) -> None:
        self.memory.resolve_interaction(run_id, interaction_id, decision)

    def now_iso(self, plus_s: float = 0.0) -> str:
        return db_timestamp(self.clock.now() + timedelta(seconds=plus_s))


class TestInMemoryJournalContract(JournalContract):
    @pytest.fixture
    def harness(self) -> JournalHarness:
        return MemoryHarness()
