"""Contract suites for ``InteractionBridge`` and ``InteractionWakeup`` adapters.

Subclass ``InteractionBridgeContract`` and provide a ``bridge_harness`` fixture:
the bridge plus a way to decide an interaction as a person would (through Node
for a journal-backed bridge). A bridge with simulated time (the engine
conformance fake) runs ``InteractionBridgeBasics`` only.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.interactions.ports import InteractionRequest, RunBinding
from harness_core.journal.types import InteractionNotOpen

if TYPE_CHECKING:
    from harness_core.interactions.ports import (
        InteractionBridge,
        InteractionDecision,
        InteractionWakeup,
    )

BINDING = RunBinding(tenant="tenant-1", scope="scope-1", run_id="run-1")
# Long enough that only a wake (or a short poll) ends a wait before it.
LONG_S = 5.0


def _req(ix: str, digest: str = "d1") -> InteractionRequest:
    return InteractionRequest(ix, "approval", "create_issue", digest)


class BridgeHarness(Protocol):
    @property
    def bridge(self) -> InteractionBridge: ...

    def decide(self, interaction_id: str, decision: InteractionDecision) -> None:
        """Raises ``InteractionNotOpen`` when it is not open (never requested, or decided)."""
        ...


class RecordingBridgeHarness(BridgeHarness, Protocol):
    async def requested_count(self, interaction_id: str) -> int: ...


class InteractionBridgeBasics:
    @pytest.fixture
    def bridge_harness(self) -> BridgeHarness:
        raise NotImplementedError("provide a bridge_harness fixture")

    async def test_ib1_undecided_returns_none_within_the_hold(
        self, bridge_harness: BridgeHarness
    ) -> None:
        ticket = await bridge_harness.bridge.request(BINDING, _req("ix-1"))
        assert ticket.interaction_id == "ix-1"
        assert await bridge_harness.bridge.wait(ticket, within_s=0.01) is None

    async def test_ib2_decision_is_returned_once_made(self, bridge_harness: BridgeHarness) -> None:
        ticket = await bridge_harness.bridge.request(BINDING, _req("ix-2", "d2"))
        bridge_harness.decide("ix-2", "denied")
        assert await bridge_harness.bridge.wait(ticket, within_s=0.01) == "denied"


class InteractionBridgeContract(InteractionBridgeBasics):
    @pytest.fixture
    def bridge_harness(self) -> RecordingBridgeHarness:
        raise NotImplementedError("provide a bridge_harness fixture")

    async def test_ib3_a_second_request_is_the_same_interaction(
        self, bridge_harness: RecordingBridgeHarness
    ) -> None:
        first = await bridge_harness.bridge.request(BINDING, _req("ix-3"))
        again = await bridge_harness.bridge.request(BINDING, _req("ix-3"))
        assert first == again
        assert await bridge_harness.requested_count("ix-3") == 1

    async def test_ib4_a_decision_made_while_waiting_ends_the_wait(
        self, bridge_harness: RecordingBridgeHarness
    ) -> None:
        ticket = await bridge_harness.bridge.request(BINDING, _req("ix-4"))
        started = time.monotonic()
        waiting = asyncio.create_task(bridge_harness.bridge.wait(ticket, within_s=LONG_S))
        await asyncio.sleep(0.05)
        bridge_harness.decide("ix-4", "approved")
        assert await waiting == "approved"
        assert time.monotonic() - started < LONG_S / 2

    async def test_ib5_a_decision_on_another_interaction_does_not_answer(
        self, bridge_harness: RecordingBridgeHarness
    ) -> None:
        mine = await bridge_harness.bridge.request(BINDING, _req("ix-5a", "d5a"))
        _ = await bridge_harness.bridge.request(BINDING, _req("ix-5b", "d5b"))
        bridge_harness.decide("ix-5b", "approved")
        assert await bridge_harness.bridge.wait(mine, within_s=0.05) is None

    async def test_ib6_a_decision_is_final(self, bridge_harness: RecordingBridgeHarness) -> None:
        ticket = await bridge_harness.bridge.request(BINDING, _req("ix-6"))
        bridge_harness.decide("ix-6", "denied")
        with pytest.raises(InteractionNotOpen):
            bridge_harness.decide("ix-6", "approved")
        assert await bridge_harness.bridge.wait(ticket, within_s=0.01) == "denied"

    async def test_ib6_an_interaction_never_requested_cannot_be_decided(
        self, bridge_harness: RecordingBridgeHarness
    ) -> None:
        with pytest.raises(InteractionNotOpen):
            bridge_harness.decide("ix-never", "approved")

    async def test_ib7_a_re_request_after_the_decision_answers_at_once(
        self, bridge_harness: RecordingBridgeHarness
    ) -> None:
        _ = await bridge_harness.bridge.request(BINDING, _req("ix-7"))
        bridge_harness.decide("ix-7", "approved")
        again = await bridge_harness.bridge.request(BINDING, _req("ix-7"))
        assert await bridge_harness.bridge.wait(again, within_s=0.01) == "approved"
        assert await bridge_harness.requested_count("ix-7") == 1


class WakeupHarness(Protocol):
    @property
    def wakeup(self) -> InteractionWakeup: ...

    async def wake(self, run_id: str) -> None: ...


class InteractionWakeupContract:
    @pytest.fixture
    def wakeup_harness(self) -> WakeupHarness:
        raise NotImplementedError("provide a wakeup_harness fixture")

    async def test_a_wake_ends_the_wait(self, wakeup_harness: WakeupHarness) -> None:
        started = time.monotonic()
        waiting = asyncio.create_task(wakeup_harness.wakeup.wait("run-w1", LONG_S))
        await asyncio.sleep(0.05)
        await wakeup_harness.wake("run-w1")
        assert await waiting is True
        assert time.monotonic() - started < LONG_S / 2

    async def test_without_a_wake_the_wait_times_out(self, wakeup_harness: WakeupHarness) -> None:
        assert await wakeup_harness.wakeup.wait("run-w2", 0.05) is False

    async def test_a_wake_for_another_run_does_not_end_the_wait(
        self, wakeup_harness: WakeupHarness
    ) -> None:
        waiting = asyncio.create_task(wakeup_harness.wakeup.wait("run-w3", 0.3))
        await asyncio.sleep(0.05)
        await wakeup_harness.wake("run-other")
        assert await waiting is False


__all__ = [
    "BINDING",
    "BridgeHarness",
    "InteractionBridgeBasics",
    "InteractionBridgeContract",
    "InteractionWakeupContract",
    "RecordingBridgeHarness",
    "WakeupHarness",
]
