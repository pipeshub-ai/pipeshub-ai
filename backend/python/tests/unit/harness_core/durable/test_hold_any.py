"""UXINT-0 (R5): a decision on any card of a batch ends the hold, not only one on the first.

Before, ``_hold`` waited on the first open id until the hold expired, so deciding the second of three
cards did nothing visible for up to 120 s and then the run suspended.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.testing.interactions import ManualWakeup

from .support import RUN, Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.journal.types import InteractionDecision
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import DurableKernel, ToolEffect
    from harness_core.testing.durable import ScriptedModelPort

HOLD_S = 10.0
POLL_S = 2.0
FIRST, SECOND, THIRD = "ix.approve.t1.0", "ix.approve.t1.1", "ix.approve.t1.2"


def _ask_for_writes(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if effect.needs_intent_commit else "allow"


def _batch() -> tuple[World, ScriptedModelPort]:
    world = World()
    world.admit()
    _, port = scripted(
        reply(
            "",
            ("post_message", {"text": "a"}),
            ("post_message", {"text": "b"}),
            ("post_message", {"text": "c"}),
        ),
        reply("done"),
    )
    return world, port


def _kernel(world: World, port: ScriptedModelPort, wakeup: ManualWakeup) -> DurableKernel:
    return world.kernel(
        port,
        approval=_ask_for_writes,
        interactions=wakeup,
        approval_hold_s=HOLD_S,
        interaction_poll_s=POLL_S,
    )


def _posted(world: World) -> list[object]:
    return [e.payload.get("text") for e in world.saas.effects if e.op == "post_message"]


def _script(
    world: World, wakeup: ManualWakeup, plan: dict[int, dict[str, InteractionDecision]]
) -> Callable[[int], None]:
    def on_wait(waits: int) -> None:
        for ix, decision in plan.get(waits, {}).items():
            world.journal.resolve_interaction(RUN, ix, decision)
            wakeup.wake(RUN)

    return on_wait


async def test_deciding_the_second_of_three_first_runs_it_without_waiting_for_the_first() -> None:
    world, port = _batch()
    wakeup = ManualWakeup(world.clock)
    posted_at_second_hold: list[list[object]] = []

    def on_wait(waits: int) -> None:
        if waits == 1:
            world.journal.resolve_interaction(RUN, SECOND, "approved")
            wakeup.wake(RUN)
        elif waits == 2:
            posted_at_second_hold.append(_posted(world))
            world.journal.resolve_interaction(RUN, FIRST, "approved")
            world.journal.resolve_interaction(RUN, THIRD, "denied")
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    # The second card ran on its own wake, before anyone decided the first.
    assert posted_at_second_hold == [["b"]]
    assert wakeup.waited_s == 0.0
    assert sorted(_posted(world)) == ["a", "b"]
    assert [e.data["invocationId"] for e in world.events("tool.denied")] == ["t1.2"]
    assert "run.suspended" not in world.types()


async def _journal_for(plan: dict[int, dict[str, InteractionDecision]]) -> list[tuple[str, object]]:
    world, port = _batch()
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _script(world, wakeup, plan)
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    return [
        (e.type, e.data.get("interactionId") or e.data.get("invocationId"))
        for e in world.events()
        if e.type.startswith(("interaction.", "tool.", "run."))
    ]


async def test_the_hold_writes_nothing_so_the_same_decisions_make_the_same_journal() -> None:
    plan: dict[int, dict[str, InteractionDecision]] = {
        1: {THIRD: "approved"},
        2: {FIRST: "denied"},
        3: {SECOND: "approved"},
    }
    first = await _journal_for(plan)
    assert first == await _journal_for(plan)
    resolved = [ix for t, ix in first if t == "interaction.resolved"]
    assert resolved == [THIRD, FIRST, SECOND]


async def test_a_worker_lost_mid_batch_resumes_with_the_decided_call_run_once() -> None:
    world, port = _batch()
    wakeup = ManualWakeup(world.clock)

    def on_wait(waits: int) -> None:
        if waits == 1:
            world.journal.resolve_interaction(RUN, SECOND, "approved")
            wakeup.wake(RUN)
        elif waits == 2:
            raise Crash

    wakeup.on_wait = on_wait
    await world.crash(_kernel(world, port, wakeup))
    assert _posted(world) == ["b"]
    assert world.journal.open_interactions(RUN) == {FIRST: "approval", THIRD: "approval"}

    resumed = ManualWakeup(world.clock)
    resumed.on_wait = _script(world, resumed, {1: {THIRD: "approved", FIRST: "approved"}})
    done = await world.run(_kernel(world, port, resumed))
    assert done.status == "completed"
    assert sorted(_posted(world)) == ["a", "b", "c"]
    assert len(world.events("interaction.requested")) == 3


async def test_a_batch_decided_in_one_request_wakes_the_hold_once_and_runs_every_approved_call() -> None:
    """DECIDE409: "Allow all" journals every decision of the batch in one transaction, then wakes the run once;
    the hold returns once and the loop runs each approved call, refuses the denied one, and never holds again."""
    world, port = _batch()
    wakeup = ManualWakeup(world.clock)
    wakeup.on_wait = _script(world, wakeup, {1: {FIRST: "approved", SECOND: "approved", THIRD: "denied"}})
    done = await world.run(_kernel(world, port, wakeup))
    assert done.status == "completed"
    assert (wakeup.waits, wakeup.woken, wakeup.waited_s) == (1, 1, 0.0)
    assert sorted(_posted(world)) == ["a", "b"]
    assert [e.data["invocationId"] for e in world.events("tool.denied")] == ["t1.2"]
    assert "run.suspended" not in world.types()
