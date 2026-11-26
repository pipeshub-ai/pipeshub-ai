"""WP-64 B on the kernels: a toolset whose saved sign-in the vendor rejects mid-run pauses the run for a reconnect.

The vendor rejects a call's sign-in (WP62-TS withdraws it); the attempt's connect gate then names the toolset for its
later calls (``CatalogConnectGate`` reads the attempt's live record). The next call waits on the connect card as a
never-connected item's does (G-12): reconnected, it runs once and the run goes on; skipped or not answered in time,
it is refused in plain words and the run goes on without the toolset, never asked again in the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.interactions.connect import ConnectNeed
from harness_core.interactions.connect_wait import BridgeConnectWait
from harness_core.interactions.ports import RunBinding
from harness_core.kernel.core.messages import ToolCall
from harness_core.testing.engines.gateway import FakeInteractionBridge

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.durable import DurableKernel
    from harness_core.testing.durable import ScriptedModelPort

JIRA = ConnectNeed("Jira", "toolset", "inst_jira_1")
SECOND = "ix.connect.t2.0"


def _posts(world: World) -> int:
    return sum(1 for e in world.saas.effects if e.op == "post_message")


class RejectedAfterFirstCall:
    """Connected at assembly; the vendor rejected the sign-in the first post used, so later posts need a reconnect."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.reconnected = False

    def need(self, call: ToolCall) -> ConnectNeed | None:
        if self.reconnected or call.name != "post_message" or _posts(self.world) == 0:
            return None
        return JIRA

    def declined(self, call: ToolCall) -> str:
        return "interaction_denied: Jira was not connected, so this call was not made. Tell the user they can connect it."


def _world() -> World:
    world = World()
    world.admit()
    return world


def _kernel(world: World, port: ScriptedModelPort, gate: RejectedAfterFirstCall) -> DurableKernel:
    return world.kernel(port, connect=gate)


async def test_wp64_ts_k1_a_rejected_sign_in_pauses_the_next_call_and_reconnected_it_runs_once() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("post_message", {"text": "one"})),
        reply("", ("post_message", {"text": "two"})),
        reply("both posted"),
    )
    gate = RejectedAfterFirstCall(world)
    suspended = await world.run(_kernel(world, port, gate))
    assert suspended.status == "suspended" and suspended.reason == SECOND
    (requested,) = world.events("interaction.requested")
    assert requested.data["kind"] == "connect" and requested.data["connect"]["instanceId"] == "inst_jira_1"
    assert _posts(world) == 1, "the second post waits; nothing reaches the vendor with the rejected sign-in"

    world.journal.resolve_interaction(RUN, SECOND, "approved")
    gate.reconnected = True
    done = await world.run(_kernel(world, port, gate))
    assert done.status == "completed"
    assert _posts(world) == 2, "reconnected, the waiting call runs once and the run carries on"
    assert len(world.events("interaction.requested")) == 1


async def test_wp64_ts_k2_skipped_or_not_in_time_the_run_goes_on_without_it_and_is_not_asked_again() -> None:
    for decision in ("denied", "expired"):
        world = _world()
        _, port = scripted(
            reply("", ("post_message", {"text": "one"})),
            reply("", ("post_message", {"text": "two"})),
            reply("", ("post_message", {"text": "three"})),
            reply("Jira was not reconnected, so I posted only the first message."),
        )
        gate = RejectedAfterFirstCall(world)
        assert (await world.run(_kernel(world, port, gate))).reason == SECOND
        world.journal.resolve_interaction(RUN, SECOND, decision)  # type: ignore[arg-type]
        done = await world.run(_kernel(world, port, gate))
        assert done.status == "completed", decision
        assert _posts(world) == 1, decision
        assert len(world.events("interaction.requested")) == 1, f"{decision}: asked once, never again in the run"
        refused = [e for e in world.events("tool.outcome") if e.data.get("status") == "error"]
        # The waiting call is refused as declined, the later one at once with the same words.
        assert len(refused) == 2 and len(world.events("tool.denied")) == 1, decision
        assert "Jira was not connected" in str(port.calls[3].messages[-1]), "the model is told in plain words"


async def test_wp64_ts_k3_the_engine_path_does_not_ask_again_for_an_item_skipped_in_the_attempt() -> None:
    bridge = FakeInteractionBridge()

    class Gate:
        def need(self, call: ToolCall) -> ConnectNeed | None:
            return JIRA if call.name.startswith("jira_") else None

        def declined(self, call: ToolCall) -> str:
            return "interaction_denied: Jira was not connected, so this call was not made"

    wait = BridgeConnectWait(bridge, RunBinding("org-1", "ses-1", "run-1"), Gate())
    first = ToolCall(id="a", name="jira_search", arguments={"q": "x"})
    assert (await wait.answer(first)).state == "waiting"  # type: ignore[union-attr]
    (asked,) = bridge.requests
    bridge.resolve(asked.interaction_id, "denied")
    assert (await wait.answer(first)).state == "declined"  # type: ignore[union-attr]
    other = await wait.answer(ToolCall(id="b", name="jira_create", arguments={"title": "y"}))
    assert other is not None and other.state == "declined"
    assert {r.interaction_id for r in bridge.requests} == {asked.interaction_id}, "a skipped item is not asked again"


def test_wp64_ts_k4_one_card_per_item_per_task_tree() -> None:
    """Found in a manual test: the parent waited out its own backoff for the GitHub sign-in its child had already waited
    on. A skip anywhere in the run tree (the parent, a sub-agent, a sub-agent's sub-agent) holds for every run of it,
    and for no other task of the conversation."""
    from types import SimpleNamespace

    from harness_core.interactions.connect import run_tree, skipped_connects

    def event(run_id: str, type_: str, **data: object) -> SimpleNamespace:
        return SimpleNamespace(run_id=run_id, type=type_, data=data)

    connect = {"item": "GitHub", "itemKind": "toolset", "instanceId": "gh"}
    events = [
        event("parent", "child.spawned", childRunId="child"),
        event("child", "child.spawned", childRunId="grandchild"),
        event("child", "interaction.requested", interactionId="ix.connect.t3.0", kind="connect", connect=connect),
        event("child", "interaction.resolved", interactionId="ix.connect.t3.0", decision="expired"),
        event("other", "interaction.requested", interactionId="ix.connect.t1.0", kind="connect", connect={**connect, "instanceId": "jira"}),
        event("other", "interaction.resolved", interactionId="ix.connect.t1.0", decision="denied"),
    ]
    assert run_tree(events, "grandchild") == {"parent", "child", "grandchild"}  # type: ignore[arg-type]
    for run in ("parent", "child", "grandchild"):
        assert skipped_connects(events, run) == {"gh"}, run  # type: ignore[arg-type]
    assert skipped_connects(events, "other") == {"jira"}  # type: ignore[arg-type]
    approved = [*events[:3], event("child", "interaction.resolved", interactionId="ix.connect.t3.0", decision="approved")]
    assert skipped_connects(approved, "parent") == set()  # type: ignore[arg-type]


def _skipped_by_a_sibling(world: World, decision: str = "expired") -> None:
    """The run is a sub-agent; its sibling (another sub-agent of the same parent) asked for Jira on its own stream
    and the person let it time out. Only the journal sees both streams."""
    connect = {"item": "Jira", "itemKind": "toolset", "instanceId": "inst_jira_1"}
    world.journal.inject("ses1", {"runId": "parent", "type": "child.spawned", "critical": False, "data": {"childRunId": RUN}})
    world.journal.inject("ses1", {"runId": "parent", "type": "child.spawned", "critical": False, "data": {"childRunId": "sibling"}})
    world.journal.inject(
        "sibling", {"runId": "sibling", "type": "interaction.requested", "critical": False,
                    "data": {"interactionId": "ix.connect.t1.0", "kind": "connect", "connect": connect}},
    )
    world.journal.inject(
        "sibling", {"runId": "sibling", "type": "interaction.resolved", "critical": False,
                    "data": {"interactionId": "ix.connect.t1.0", "decision": decision}},
    )


@pytest.mark.journal_history_unchecked("injects another run's streams of the same tree")
async def test_wp64_ts_k5_a_run_is_not_asked_for_an_item_skipped_elsewhere_in_its_tree() -> None:
    """The rule (fail closed, ask once): an item skipped or left to time out by a parent, a sibling or any run
    of the tree is never asked for again by another; the journal names the tree's skips on the claim."""
    world = _world()
    _skipped_by_a_sibling(world)

    class AlwaysJira:
        def need(self, call: ToolCall) -> ConnectNeed | None:
            return JIRA if call.name == "post_message" else None

        def declined(self, call: ToolCall) -> str:
            return "interaction_denied: Jira was not connected, so this call was not made."

    _, port = scripted(reply("", ("post_message", {"text": "one"})), reply("went on without Jira"))
    done = await world.run(world.kernel(port, connect=AlwaysJira()))
    assert done.status == "completed"
    assert world.events("interaction.requested") == [], "no second card for an item the tree already skipped"
    assert _posts(world) == 0


async def test_wp64_ts_k6_the_engine_attempt_is_seeded_with_the_trees_skips_from_the_claim() -> None:
    bridge = FakeInteractionBridge()

    class Gate:
        def need(self, call: ToolCall) -> ConnectNeed | None:
            return JIRA

        def declined(self, call: ToolCall) -> str:
            return "interaction_denied: Jira was not connected, so this call was not made"

    wait = BridgeConnectWait(bridge, RunBinding("org-1", "ses-1", "child"), Gate(), skipped={"inst_jira_1"})
    answer = await wait.answer(ToolCall(id="a", name="jira_search", arguments={}))
    assert answer is not None and answer.state == "declined"
    assert bridge.requests == [], "the parent's skip holds for its engine sub-agent: no card"
