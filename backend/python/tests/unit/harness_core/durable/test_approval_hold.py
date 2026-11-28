"""KH-*: approvals held in the running run (K-H, P2.6c).

The worker requests, holds up to the hold, and Node resolves into the running
run; past the hold the run suspends. Node's half is the reference journal's
``resolve_interaction`` (no ``run.resumed`` unless the run suspended) and its
refusal of a suspend with nothing left open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.testing.interactions import ManualWakeup

from .support import LEASE_MS, RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.journal.types import (
        CommitReceipt,
        CommitRequest,
        InteractionDecision,
    )
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import DurableKernel, ToolEffect
    from harness_core.testing.durable import ScriptedModelPort

HOLD_S = 10.0
POLL_S = 2.0
FIRST = "ix.approve.t1.0"
SECOND = "ix.approve.t1.1"


def _ask_for_writes(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if effect.needs_intent_commit else "allow"


def _world() -> World:
    world = World()
    world.admit()
    return world


def _kernel(world: World, port: ScriptedModelPort, wakeup: ManualWakeup) -> DurableKernel:
    return world.kernel(
        port,
        approval=_ask_for_writes,
        interactions=wakeup,
        approval_hold_s=HOLD_S,
        interaction_poll_s=POLL_S,
    )


def _resolve_at(
    world: World, wakeup: ManualWakeup, n: int, decisions: dict[str, InteractionDecision]
) -> Callable[[int], None]:
    def on_wait(waits: int) -> None:
        if waits == n:
            for ix, decision in decisions.items():
                world.journal.resolve_interaction(RUN, ix, decision)
            wakeup.wake(RUN)

    return on_wait


def _posts(world: World) -> int:
    return sum(1 for e in world.saas.effects if e.op == "post_message")


async def test_kh2_approved_within_the_hold_runs_without_suspending() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _resolve_at(world, wakeup, 2, {FIRST: "approved"})
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert _posts(world) == 1
    types = world.types()
    assert "run.suspended" not in types and "run.resumed" not in types
    tail = types[types.index("interaction.requested") :]
    assert tail[:4] == ["interaction.requested", "interaction.resolved", "tool.intent", "tool.outcome"]
    assert wakeup.woken == 1


async def test_kh3_denied_within_the_hold_refuses_and_the_model_goes_on() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("not posted"))
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _resolve_at(world, wakeup, 1, {FIRST: "denied"})
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert _posts(world) == 0
    assert world.events("tool.denied")[0].data["rule"] == "interaction_denied"
    assert len(port.calls) == 2


async def test_kh4_undecided_suspends_once_and_resolution_resumes_it() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)
    suspended = await world.run(_kernel(world, port, wakeup))
    assert suspended.status == "suspended" and suspended.reason == FIRST
    assert wakeup.waited_s == HOLD_S
    assert len(world.events("interaction.requested")) == 1
    (event,) = world.events("run.suspended")
    assert event.data == {"reason": "approval", "interactionId": FIRST}
    world.journal.resolve_interaction(RUN, FIRST, "approved")
    assert world.types()[-2:] == ["interaction.resolved", "run.resumed"]
    done = await world.run(_kernel(world, port, ManualWakeup(world.clock)))
    assert done.status == "completed"
    assert _posts(world) == 1
    assert len(world.events("interaction.requested")) == 1


async def test_kh5_no_intent_is_journaled_before_the_approval() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _resolve_at(world, wakeup, 3, {FIRST: "approved"})
    _ = await world.run(_kernel(world, port, wakeup))
    types = world.types()
    assert types.index("tool.intent") > types.index("interaction.resolved")


async def test_kh6_a_decision_landing_as_the_hold_ends_is_not_lost() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)
    original = world.journal.commit

    async def resolve_before_the_suspend(request: CommitRequest) -> CommitReceipt:
        if getattr(request.transition, "reason", None) == "approval":
            world.journal.resolve_interaction(RUN, FIRST, "approved")
        return await original(request)

    world.journal.commit = resolve_before_the_suspend  # type: ignore[method-assign]
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert _posts(world) == 1
    assert "run.suspended" not in world.types()
    assert world.types().count("interaction.resolved") == 1


async def test_kh7_a_worker_restart_while_holding_keeps_the_approval_pending() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    long_hold = world.kernel(
        port,
        approval=_ask_for_writes,
        interactions=ManualWakeup(world.clock),
        approval_hold_s=LEASE_MS / 1000 * 2,
        interaction_poll_s=POLL_S,
    )
    lost = await world.run(long_hold)
    assert lost.status == "lost"
    assert world.journal.open_interactions(RUN) == {FIRST: "approval"}
    world.expire()
    assert world.journal.status(RUN) == "queued"
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _resolve_at(world, wakeup, 1, {FIRST: "approved"})
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert _posts(world) == 1
    assert len(world.events("interaction.requested")) == 1
    assert "run.suspended" not in world.types()


async def test_kh8_a_decision_made_while_no_worker_holds_applies_on_the_next_claim() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    long_hold = world.kernel(
        port,
        approval=_ask_for_writes,
        interactions=ManualWakeup(world.clock),
        approval_hold_s=LEASE_MS / 1000 * 2,
        interaction_poll_s=POLL_S,
    )
    assert (await world.run(long_hold)).status == "lost"
    world.expire()
    world.journal.resolve_interaction(RUN, FIRST, "approved")
    assert world.journal.status(RUN) == "queued"
    assert "run.resumed" not in world.types()
    wakeup = ManualWakeup(world.clock)
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert _posts(world) == 1
    assert wakeup.waits == 0


async def test_kh9_a_cancel_while_holding_ends_the_run_and_the_call_never_runs() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)

    def cancel(waits: int) -> None:
        if waits == 1:
            world.journal.request_cancel(RUN)

    wakeup.on_wait = cancel
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "cancelled"
    assert _posts(world) == 0
    assert world.events("tool.outcome")[-1].data["status"] == "cancelled"


async def test_kh10_two_asks_are_requested_together_and_decided_apart() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("post_message", {"text": "a"}), ("post_message", {"text": "b"})),
        reply("one posted"),
    )
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _resolve_at(world, wakeup, 1, {FIRST: "approved", SECOND: "denied"})
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert _posts(world) == 1
    requested = [c for c in world.journal.commits(RUN) if "interaction.requested" in c[1]]
    assert len(requested) == 1 and requested[0][1].count("interaction.requested") == 2
    assert [e.data["invocationId"] for e in world.events("tool.denied")] == ["t1.1"]


async def test_kh11_a_wake_reads_before_the_poll_and_a_lost_wake_costs_one_poll() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))
    wakeup = ManualWakeup(world.clock)

    def resolve_quietly(waits: int) -> None:
        if waits == 2:
            world.journal.resolve_interaction(RUN, FIRST, "approved")

    wakeup.on_wait = resolve_quietly
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert wakeup.waits == 2 and wakeup.woken == 0
    assert wakeup.waited_s == POLL_S * 2


async def test_kh1_without_a_wakeup_asking_suspends_in_one_commit() -> None:
    world = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})))
    suspended = await world.run(world.kernel(port, approval=_ask_for_writes))
    assert suspended.status == "suspended"
    (commit,) = [c for c in world.journal.commits(RUN) if "interaction.requested" in c[1]]
    # WP-97: a policy that names no rule still gives the card the plain reason, in the same commit.
    assert commit[1] == ["interaction.explained", "interaction.requested", "run.suspended"]
