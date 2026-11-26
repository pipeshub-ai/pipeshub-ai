"""Connect waits (WP-33, G-12) beside the run gates (WP-31, G-03/G-04), as round 8 merged them.

A run is asked to connect an item only for a call that would be made: a dry run's write is shadowed and never needs
the credential, and a write the plan gate refuses before the plan is approved is refused at once with the gate's own
words, so nobody is asked to connect Jira for a call that cannot run. A call the gates let through still waits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.contracts.generated.run_spec import RunGates
from harness_core.gates.plan import PLAN_RULE
from harness_core.interactions.connect import ConnectNeed
from harness_core.testing.run_spec import minimal_run_spec

from .support import World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import ToolCall

CONNECT = "ix.connect.t1.0"
JIRA = ConnectNeed("Jira", "toolset", "inst_jira_1")


class _Unconnected:
    def need(self, call: ToolCall) -> ConnectNeed | None:
        return JIRA if call.name == "post_message" else None

    def declined(self, call: ToolCall) -> str:
        return f"interaction_denied: Jira was not connected, so {call.name} was not made"


def _world(gates: RunGates) -> World:
    world = World()
    world.admit(spec=minimal_run_spec().model_copy(update={"gates": gates}))
    return world


async def test_r8_a_dry_run_shadows_an_unconnected_write_without_asking_to_connect() -> None:
    world = _world(RunGates(dryRun=True))
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("I would have posted; nothing was sent."))
    done = await world.run(world.kernel(port, connect=_Unconnected()))
    assert done.status == "completed"
    assert world.events("interaction.requested") == []
    (outcome,) = world.events("tool.outcome")
    assert outcome.data.get("shadow") is not None
    assert [e for e in world.saas.effects if e.op == "post_message"] == []


async def test_r8_a_write_before_the_plan_is_approved_is_refused_not_held_for_a_connect() -> None:
    world = _world(RunGates(planApproval="required"))
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("I need an approved plan first."))
    done = await world.run(world.kernel(port, connect=_Unconnected()))
    assert done.status == "completed"
    assert world.events("interaction.requested") == []
    (denied,) = world.events("tool.denied")
    assert denied.data["rule"] == PLAN_RULE


async def test_r8_a_write_the_gates_let_through_still_waits_for_the_connect() -> None:
    world = _world(RunGates())
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    suspended = await world.run(world.kernel(port, connect=_Unconnected()))
    assert suspended.status == "suspended" and suspended.reason == CONNECT
