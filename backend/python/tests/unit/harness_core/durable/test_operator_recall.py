"""OR-1..OR-4 (H1, P2.9 FB-2): an operator's ``approved`` on an unknown effect re-calls once with a fresh intent,
for every tool, so a crash during the re-call is never re-called again automatically."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.effects.reconcile import (
    LookupAbsent,
    ReconcileTiming,
    TimedReconciler,
)
from harness_core.failpoints import Raise
from harness_core.testing import armed

from .support import RUN, Crash, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.effects.reconcile import LookupResult, UnknownEffect
    from harness_core.kernel.durable import DurableKernel
    from harness_core.testing.durable import ScriptedModelPort

CRASH = Raise(Crash)
UNKNOWN = "ix.unknown.t1.0"


class _OtherToolLookup:
    """A hook for a tool the run never calls: the reconciler exists and does not cover ``post_message``."""

    timing = ReconcileTiming(tool_timeout_s=1.0, read_after_write=True)

    async def lookup(self, effect: UnknownEffect) -> LookupResult:
        raise AssertionError("never asked")


def _kernel(world: World, port: ScriptedModelPort, *, uncovered_reconciler: bool) -> DurableKernel:
    if not uncovered_reconciler:
        return world.kernel(port)
    reconciler = TimedReconciler({"local@default/send_note": _OtherToolLookup()}, world.clock, world.clock.sleep)
    return world.kernel(port, reconciler=reconciler)


async def _parked_then_approved(world: World, port: ScriptedModelPort, *, uncovered_reconciler: bool) -> None:
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, uncovered_reconciler=uncovered_reconciler))
    parked = await world.run(_kernel(world, port, uncovered_reconciler=uncovered_reconciler))
    assert parked.status == "suspended" and world.saas.effects == []
    world.journal.resolve_interaction(RUN, UNKNOWN, "approved", by="p_admin")


@pytest.fixture(params=[False, True], ids=["no_reconciler", "uncovered_tool"])
def uncovered(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


async def test_or1_an_operator_re_call_journals_a_fresh_intent_and_runs_once(uncovered: bool) -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("sent"))
    await _parked_then_approved(world, port, uncovered_reconciler=uncovered)
    done = await world.run(_kernel(world, port, uncovered_reconciler=uncovered))
    assert done.status == "completed"
    assert len(world.saas.effects) == 1
    assert len(world.events("tool.intent")) == 2


async def test_or2_a_crash_after_the_re_calls_effect_is_never_re_executed(uncovered: bool) -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("sent"))
    await _parked_then_approved(world, port, uncovered_reconciler=uncovered)
    with armed({"worker.tool.after_effect": CRASH}):
        await world.crash(_kernel(world, port, uncovered_reconciler=uncovered))
    assert len(world.saas.effects) == 1
    parked = await world.run(_kernel(world, port, uncovered_reconciler=uncovered))
    assert parked.status == "suspended"
    assert len(world.saas.effects) == 1
    assert [e.data["interactionId"] for e in world.events("interaction.requested")] == [UNKNOWN, UNKNOWN]


async def test_or3_a_crash_after_the_re_calls_intent_parks_it_again(uncovered: bool) -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("sent"))
    await _parked_then_approved(world, port, uncovered_reconciler=uncovered)
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, uncovered_reconciler=uncovered))
    parked = await world.run(_kernel(world, port, uncovered_reconciler=uncovered))
    assert parked.status == "suspended" and world.saas.effects == []


async def test_or4_a_second_operator_decision_after_the_crash_still_runs_at_most_once() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("sent"))
    await _parked_then_approved(world, port, uncovered_reconciler=False)
    with armed({"worker.tool.after_effect": CRASH}):
        await world.crash(world.kernel(port))
    parked = await world.run(world.kernel(port))
    assert parked.status == "suspended"
    # The operator sees the effect landed and confirms it: never executed again.
    world.journal.resolve_interaction(RUN, UNKNOWN, "answered", by="p_admin")
    done = await world.run(world.kernel(port))
    assert done.status == "completed" and len(world.saas.effects) == 1


_ = LookupAbsent
