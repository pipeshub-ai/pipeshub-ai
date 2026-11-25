"""GA-07 (G-12) on the engine path: the Tool Gateway defers a call of an unconnected item on a connect wait.

The engine is told to end its turn (``deferred``, ``auth_required``, the interaction it waits on) and the engine
kernel suspends on it as on an approval. The wait is named by the call, so the engine's re-call after the owner
answered finds it; nothing runs before then. Connected, the next attempt's gateway has no wait for the call and
it runs once; declined, the re-call is refused with the next step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.durable.kernel import _decisions_note  # pyright: ignore[reportPrivateUsage]
from harness_core.gateway.endpoint import WHILE_WAITING
from harness_core.interactions.connect import ConnectNeed
from harness_core.interactions.connect_wait import (
    AUTH_REQUIRED,
    BridgeConnectWait,
    connect_interaction_id,
)
from harness_core.interactions.ports import RunBinding
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.engines.gateway import FakeInteractionBridge

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import ToolCall

RUN = RunBinding("org-1", "ses-1", "run-1")
JIRA = ConnectNeed("Jira", "toolset", "inst_jira_1")


class Gate:
    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected

    def need(self, call: ToolCall) -> ConnectNeed | None:
        return None if self.connected or call.name != "lookup" else JIRA

    def declined(self, call: ToolCall) -> str:
        return "interaction_denied: Jira was not connected, so this call was not made"


def _world(bridge: FakeInteractionBridge, gate: Gate, *, masked: bool = True):  # noqa: ANN202
    return build_endpoint(
        needs_authentication=frozenset({"lookup"}) if masked else frozenset(),
        connect=BridgeConnectWait(bridge, RUN, gate),
    )


async def test_ga07_engine_a_call_of_an_unconnected_item_is_deferred_on_a_connect_wait() -> None:
    bridge = FakeInteractionBridge()
    world = _world(bridge, Gate())
    result = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    ix = connect_interaction_id("run-1", "lookup", {"q": "x"})
    assert (result.disposition, result.reason_code, result.interaction_id) == ("deferred", AUTH_REQUIRED, ix)
    # ADR-RUN-06 D1: the engine keeps working on the rest while the owner connects it.
    assert not result.is_error and WHILE_WAITING in result.text and "Jira" in result.text
    assert world.runs == [], "nothing ran before the owner connected"
    (request,) = bridge.requests
    assert (request.kind, request.connect) == ("connect", JIRA)
    # The engine's re-call with the same arguments waits on the same interaction.
    again = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    assert again.interaction_id == ix and world.runs == []


async def test_ga07_engine_connected_the_next_attempt_runs_the_call_once() -> None:
    bridge = FakeInteractionBridge()
    _ = await _world(bridge, Gate()).endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    bridge.resolve(connect_interaction_id("run-1", "lookup", {"q": "x"}), "approved")
    # The attempt after the resume assembles the tools with the credential: no mark, no wait.
    resumed = _world(bridge, Gate(connected=True), masked=False)
    result = await resumed.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    assert (result.disposition, result.text) == ("executed", "lookup:x")
    assert resumed.runs == ["lookup"]
    assert len(bridge.requests) == 1


async def test_ga07_engine_declined_the_re_call_is_refused_with_the_next_step() -> None:
    bridge = FakeInteractionBridge()
    world = _world(bridge, Gate())
    _ = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    bridge.resolve(connect_interaction_id("run-1", "lookup", {"q": "x"}), "denied")
    result = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    assert (result.disposition, result.is_error) == ("denied", True)
    assert "was not connected" in result.text
    assert world.runs == []


async def test_ga07_engine_a_connected_tool_and_a_run_without_connect_waits_are_as_before() -> None:
    bridge = FakeInteractionBridge()
    plain = await _world(bridge, Gate()).endpoint.call_tool(claims_for(BINDING), "search", {"q": "x"})
    assert plain.disposition == "executed" and bridge.requests == []
    old = build_endpoint(needs_authentication=frozenset({"lookup"}))
    result = await old.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    assert result.disposition != "deferred"


def test_ga07_engine_the_decisions_note_names_what_was_connected() -> None:
    ix = connect_interaction_id("run-1", "lookup", {"q": "x"})
    note = _decisions_note({ix: "approved"})
    assert f"Connected: {ix}." in note and "Call each approved tool again with the same arguments" in note
    declined = _decisions_note({ix: "denied"})
    assert f"Not connected: {ix}." in declined
    plain = _decisions_note({"ix.approve.k1": "approved"})
    assert "Connected" not in plain
