"""GA-07 (G-12, C20.13): a call whose item its owner has not connected pauses the run.

The run waits on ``ix.connect.<invocation>`` (kind ``connect``) as it waits on
an approval, suspended at once (never held in the attempt: its tools were
assembled before the credential existed). Once the owner connects it the call
runs exactly once on the attempt that follows, under the run's own rules;
declined, it is refused with the next step and the model goes on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.interactions.connect import ConnectGate, ConnectNeed
from harness_core.journal.types import CONNECT_PAUSES
from harness_core.testing.interactions import ManualWakeup

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import DurableKernel, ToolEffect
    from harness_core.testing.durable import ScriptedModelPort

CONNECT = "ix.connect.t1.0"
APPROVE = "ix.approve.t1.0"
JIRA = ConnectNeed("Jira", "toolset", "inst_jira_1")


class FakeGate:
    """The attempt's unconnected items: ``connected`` stands for the credential the next assembly would find."""

    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected

    def need(self, call: ToolCall) -> ConnectNeed | None:
        return None if self.connected or call.name != "post_message" else JIRA

    def declined(self, call: ToolCall) -> str:
        return f"interaction_denied: Jira was not connected, so {call.name} was not made"


def _ask_for_writes(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if effect.needs_intent_commit else "allow"


def _world(accepts: frozenset[str] | None = None) -> World:
    world = World(journal_accepts=accepts)
    world.admit()
    return world


def _kernel(world: World, port: ScriptedModelPort, gate: ConnectGate, **overrides: object) -> DurableKernel:
    return world.kernel(port, connect=gate, **overrides)


def _posts(world: World) -> int:
    return sum(1 for e in world.saas.effects if e.op == "post_message")


async def test_ga07_the_run_pauses_on_connect_and_after_connecting_the_call_runs_exactly_once() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    suspended = await world.run(_kernel(world, port, FakeGate()))
    assert suspended.status == "suspended" and suspended.reason == CONNECT
    (requested,) = world.events("interaction.requested")
    assert requested.data["kind"] == "connect"
    assert requested.data["connect"] == {"item": "Jira", "itemKind": "toolset", "instanceId": "inst_jira_1"}
    assert world.events("run.suspended")[0].data == {"reason": "approval", "interactionId": CONNECT}
    # Nothing of the call is journaled before the owner connected: no intent, no outcome.
    assert world.events("tool.intent") == [] and world.events("tool.outcome") == []
    assert _posts(world) == 0

    world.journal.resolve_interaction(RUN, CONNECT, "approved")
    done = await world.run(_kernel(world, port, FakeGate(connected=True)))
    assert done.status == "completed"
    assert _posts(world) == 1
    assert len(world.events("tool.intent")) == 1 and len(world.events("tool.outcome")) == 1
    assert world.events("tool.outcome")[0].data["status"] == "ok"
    # No re-prompt: the model was called for the call and once after it, never asked again.
    assert len(port.calls) == 2


async def test_ga07_connecting_answers_only_the_wait_the_call_still_goes_through_the_runs_rules() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    gate = FakeGate()
    assert (await world.run(_kernel(world, port, gate, approval=_ask_for_writes))).reason == CONNECT
    world.journal.resolve_interaction(RUN, CONNECT, "approved")
    gate.connected = True
    waiting = await world.run(_kernel(world, port, gate, approval=_ask_for_writes))
    assert waiting.status == "suspended" and waiting.reason == APPROVE
    assert [e.data["kind"] for e in world.events("interaction.requested")] == ["connect", "approval"]
    assert _posts(world) == 0
    world.journal.resolve_interaction(RUN, APPROVE, "approved")
    done = await world.run(_kernel(world, port, gate, approval=_ask_for_writes))
    assert done.status == "completed" and _posts(world) == 1


async def test_ga07_a_connect_is_never_held_in_the_attempt_even_with_a_wakeup() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)
    suspended = await world.run(_kernel(world, port, FakeGate(), interactions=wakeup, approval_hold_s=10.0))
    assert suspended.status == "suspended" and suspended.reason == CONNECT
    assert wakeup.waited_s == 0


async def test_ga07_declined_the_call_is_refused_with_the_next_step_and_the_model_goes_on() -> None:
    for decision in ("denied", "expired"):
        world = _world()
        _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("told the user"))
        gate = FakeGate()
        assert (await world.run(_kernel(world, port, gate))).reason == CONNECT
        world.journal.resolve_interaction(RUN, CONNECT, decision)  # type: ignore[arg-type]
        done = await world.run(_kernel(world, port, gate))
        assert done.status == "completed", decision
        assert _posts(world) == 0
        (denied,) = world.events("tool.denied")
        assert denied.data["rule"] == "interaction_denied"
        assert len(port.calls) == 2
        assert "Jira was not connected" in str(port.calls[1].messages[-1])


async def test_ga07_still_not_connected_after_saying_so_the_call_runs_once_and_answers_how_to_connect() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("told the user"))
    gate = FakeGate()
    assert (await world.run(_kernel(world, port, gate))).reason == CONNECT
    world.journal.resolve_interaction(RUN, CONNECT, "approved")
    # The next attempt's tools still find no credential: no second wait, the call goes to the tool once.
    done = await world.run(_kernel(world, port, gate))
    assert done.status == "completed"
    assert len(world.events("interaction.requested")) == 1
    assert len(world.events("tool.outcome")) == 1


async def test_ga07_a_control_plane_without_connect_pauses_gets_the_call_as_before() -> None:
    world = _world(accepts=frozenset({"approval_reasons"}))
    assert CONNECT_PAUSES not in world.journal_accepts  # type: ignore[operator]
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    done = await world.run(_kernel(world, port, FakeGate()))
    assert done.status == "completed"
    assert world.events("interaction.requested") == []
