"""IB-* on the real Node: ``InteractionBridgeContract`` over ``JournalInteractionBridge``
with Node's journal routes and ``resolveActive`` (journal-http-server.ts).

The worker requests through its fenced commit; Node resolves into the running
run (no ``run.resumed``) and refuses a second decision. Skipped where the Node
workspace has no ``node_modules``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harness_core.clock import SystemClock
from harness_core.interactions import JournalInteractionBridge, NoWakeup
from harness_core.interactions.run_view import ClaimInteractionView
from harness_core.journal.types import InteractionNotOpen
from harness_core.testing.contracts.interactions import (
    BINDING,
    InteractionBridgeContract,
    RecordingBridgeHarness,
)
from tests.unit.harness.test_node_journal_contract import (
    NodeHarness,
    _NodeServer,
    node_server,  # noqa: F401 - the module-scoped Node server fixture
)

if TYPE_CHECKING:
    from harness_core.journal.types import InteractionDecision


@dataclass
class _NodeBridgeHarness:
    node: NodeHarness
    server: _NodeServer
    bridge: JournalInteractionBridge

    def decide(self, interaction_id: str, decision: InteractionDecision) -> None:
        response = self.server.control.post(
            "/resolve",
            json={"runId": BINDING.run_id, "interactionId": interaction_id, "decision": decision},
        )
        if response.status_code == 409:
            raise InteractionNotOpen(interaction_id)
        _ = response.raise_for_status()

    async def requested_count(self, interaction_id: str) -> int:
        events = await self.node.journal.read(BINDING.scope)
        return sum(
            1
            for e in events
            if e.type == "interaction.requested" and e.data["interactionId"] == interaction_id
        )


class TestNodeJournalInteractionBridge(InteractionBridgeContract):
    @pytest.fixture
    async def bridge_harness(self, node_server: _NodeServer) -> RecordingBridgeHarness:  # noqa: F811
        node = NodeHarness(node_server)
        node.admit(BINDING.run_id, session_id=BINDING.scope)
        claim = await node.journal.claim(BINDING.run_id, worker_id="w1")
        assert claim is not None
        view = ClaimInteractionView(node.journal, claim)  # type: ignore[arg-type]
        # No wake over HTTP here: the bridge's poll finds Node's decision.
        bridge = JournalInteractionBridge(view, NoWakeup(), SystemClock(), poll_s=0.05)
        return _NodeBridgeHarness(node, node_server, bridge)


async def test_a_decision_on_the_running_run_does_not_resume_it(node_server: _NodeServer) -> None:  # noqa: F811
    node = NodeHarness(node_server)
    node.admit(BINDING.run_id, session_id=BINDING.scope)
    claim = await node.journal.claim(BINDING.run_id, worker_id="w1")
    assert claim is not None
    view = ClaimInteractionView(node.journal, claim)  # type: ignore[arg-type]
    bridge = JournalInteractionBridge(view, NoWakeup(), SystemClock(), poll_s=0.05)
    from harness_core.interactions import InteractionRequest

    ticket = await bridge.request(BINDING, InteractionRequest("ix-r", "approval", "t", "d"))
    _NodeBridgeHarness(node, node_server, bridge).decide("ix-r", "approved")
    assert await bridge.wait(ticket, within_s=1) == "approved"
    types = [e.type for e in await node.journal.read(BINDING.scope)]
    assert types[-2:] == ["interaction.requested", "interaction.resolved"]
    assert "run.resumed" not in types
