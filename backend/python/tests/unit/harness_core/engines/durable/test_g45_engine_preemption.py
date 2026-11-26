"""G-45 / GA-24: an engine run asked to give its slot to a higher class yields between turns, never inside one.

The engine kernel drives a whole engine turn at a time; interrupting one (as a drain does) costs the engine its place
in the turn. Preemption is cheaper than that: the run finishes the turn it is in, yields ``preempted`` before the
next, and the next attempt resumes from the journal. Each attempt drives at least one turn, so a run asked again
and again still moves on.
"""

from __future__ import annotations

import asyncio

from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.priority import PreemptRequests
from harness_core.testing.engines.durable import EngineWorld

RUN = "run-g45"


async def test_ga24_an_engine_run_asked_to_yield_finishes_its_turn_and_yields_before_the_next() -> None:
    world = EngineWorld()
    world.admit(RUN, "say first")
    _ = world.journal.queue_input(RUN, "say second")
    asked = PreemptRequests()
    asked.request(RUN)
    first = await asyncio.wait_for(world.attempt(RUN, kernel=world.kernel(preempt=asked)), 10)
    assert (first.status, first.reason) == ("yielded", "preempted")
    types = world.types(RUN)
    assert types.count("turn.completed") == 1, "the turn in progress was finished, not cut"
    yielded = [e for e in world.events(RUN) if e.type == "run.yielded"]
    assert [e.data["reason"] for e in yielded] == ["preempted"]
    asked.clear(RUN)
    for _ in range(3):
        if world.journal.status(RUN) in TERMINAL_STATUSES:
            break
        world.clock.advance(1)
        _ = await asyncio.wait_for(world.attempt(RUN), 10)
    assert world.journal.status(RUN) == "completed"
    assert world.types(RUN).count("turn.completed") == 2


async def test_ga24_an_engine_run_asked_before_its_first_turn_still_drives_one() -> None:
    world = EngineWorld()
    world.admit(RUN, "say only")
    asked = PreemptRequests()
    asked.request(RUN)
    done = await asyncio.wait_for(world.attempt(RUN, kernel=world.kernel(preempt=asked)), 10)
    assert done.status == "completed"
    assert not [e for e in world.events(RUN) if e.type == "run.yielded"]
