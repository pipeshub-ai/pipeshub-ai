"""IB-*: ``InteractionBridgeContract`` on the in-memory bridge and on the journal-backed
bridge over the reference journal; ``InteractionWakeupContract`` on the in-memory wakeup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harness_core.clock import SystemClock
from harness_core.interactions import (
    InMemoryInteractionBridge,
    InMemoryInteractionWakeup,
    JournalInteractionBridge,
)
from harness_core.interactions.run_view import ClaimInteractionView
from harness_core.journal import InMemoryJournal
from harness_core.testing.contracts.interactions import (
    BINDING,
    InteractionBridgeContract,
    InteractionWakeupContract,
    RecordingBridgeHarness,
    WakeupHarness,
)
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from harness_core.journal.types import InteractionDecision


@dataclass
class _MemoryHarness:
    bridge: InMemoryInteractionBridge

    def decide(self, interaction_id: str, decision: InteractionDecision) -> None:
        self.bridge.decide(interaction_id, decision)

    async def requested_count(self, interaction_id: str) -> int:
        return int(interaction_id in self.bridge.requests)


class TestInMemoryInteractionBridge(InteractionBridgeContract):
    @pytest.fixture
    def bridge_harness(self) -> RecordingBridgeHarness:
        return _MemoryHarness(InMemoryInteractionBridge())


@dataclass
class _JournalHarness:
    journal: InMemoryJournal
    wakeup: InMemoryInteractionWakeup
    bridge: JournalInteractionBridge

    def decide(self, interaction_id: str, decision: InteractionDecision) -> None:
        self.journal.resolve_interaction(BINDING.run_id, interaction_id, decision)
        self.wakeup.wake(BINDING.run_id)

    async def requested_count(self, interaction_id: str) -> int:
        return sum(
            1
            for e in self.journal.events(BINDING.scope)
            if e.type == "interaction.requested" and e.data["interactionId"] == interaction_id
        )


class TestJournalInteractionBridge(InteractionBridgeContract):
    @pytest.fixture
    async def bridge_harness(self) -> RecordingBridgeHarness:
        journal = InMemoryJournal()
        journal.admit(
            BINDING.run_id,
            org_id=BINDING.tenant,
            session_id=BINDING.scope,
            spec=minimal_run_spec(),
            input_text="hi",
        )
        claim = await journal.claim(BINDING.run_id, worker_id="w")
        assert claim is not None
        wakeup = InMemoryInteractionWakeup()
        # A long poll: only the wake can end IB-4 in time.
        bridge = JournalInteractionBridge(
            ClaimInteractionView(journal, claim), wakeup, SystemClock(), poll_s=30.0
        )
        return _JournalHarness(journal, wakeup, bridge)


@dataclass
class _Wakeup:
    wakeup: InMemoryInteractionWakeup

    async def wake(self, run_id: str) -> None:
        self.wakeup.wake(run_id)


class TestInMemoryInteractionWakeup(InteractionWakeupContract):
    @pytest.fixture
    def wakeup_harness(self) -> WakeupHarness:
        return _Wakeup(InMemoryInteractionWakeup())
