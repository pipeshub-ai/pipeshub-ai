"""Connect waits (WP-33, G-12) on the engine path beside the run's rules and its dry run (WP-31, G-03).

An engine is told to end its turn and wait for the owner to connect an item only for a call that would be made: a
call the rules refuse is refused at once, and a dry run's shadowed call is recorded without the credential.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.interactions.connect import ConnectNeed
from harness_core.interactions.connect_wait import BridgeConnectWait
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
    def need(self, call: ToolCall) -> ConnectNeed | None:
        return JIRA if call.name == "create" else None

    def declined(self, call: ToolCall) -> str:
        return "interaction_denied: Jira was not connected, so this call was not made"


async def test_r8_engine_a_call_the_rules_refuse_is_refused_not_held_for_a_connect() -> None:
    bridge = FakeInteractionBridge()
    world = build_endpoint(
        approvals={"create": "deny"},
        needs_authentication=frozenset({"create"}),
        connect=BridgeConnectWait(bridge, RUN, Gate()),
    )
    result = await world.endpoint.call_tool(claims_for(BINDING), "create", {"q": "x"})
    assert (result.disposition, result.is_error) == ("denied", True)
    assert bridge.requests == [] and world.runs == []


async def test_r8_engine_a_dry_runs_write_is_shadowed_without_a_connect_wait() -> None:
    bridge = FakeInteractionBridge()
    world = build_endpoint(
        needs_authentication=frozenset({"create"}),
        connect=BridgeConnectWait(bridge, RUN, Gate()),
        dry_run=True,
    )
    result = await world.endpoint.call_tool(claims_for(BINDING), "create", {"q": "x"})
    assert result.disposition == "executed" and "Dry run" in result.text
    assert bridge.requests == [] and world.runs == []


async def test_r8_engine_a_write_the_rules_allow_still_waits_for_the_connect() -> None:
    bridge = FakeInteractionBridge()
    world = build_endpoint(needs_authentication=frozenset({"create"}), connect=BridgeConnectWait(bridge, RUN, Gate()))
    result = await world.endpoint.call_tool(claims_for(BINDING), "create", {"q": "x"})
    assert result.disposition == "deferred" and len(bridge.requests) == 1 and world.runs == []
