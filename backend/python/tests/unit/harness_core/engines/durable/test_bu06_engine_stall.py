"""BU-06 for coding engines: a runaway engine that keeps making the same PipesHub call is stopped and answers.

The Tool Gateway only de-duplicated calls still in flight, so an engine that called the same tool with the same
arguments over and over ran until its step, token or wall limit. The controller now counts the calls of each model
turn as a round (``harness_core.kernel.core.stall``; WP-62: a model turn, not a gateway call) and stops the turn the
way a limit does: the run completes with what it gathered, flagged ``truncatedBy: stall``.
"""

from __future__ import annotations

from harness_core.kernel.core.stall import STALL_REPEAT_ROUNDS
from harness_core.testing.engines.durable import SESSION, EngineWorld

RUN = "run-bu06"


def _calls(lines: list[str]) -> str:
    return "\n".join([*lines, "say done"])


async def test_bu06_an_engine_repeating_one_call_is_stopped_and_answers() -> None:
    # Metered as every gateway run is: the ledger marks each model turn the moment the gateway settles it.
    world = EngineWorld(metered=True)
    world.admit(RUN, _calls(['tool lookup {"jql": "project = PP"}'] * (STALL_REPEAT_ROUNDS + 6)))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_REPEAT_ROUNDS
    completed = [e.data for e in world.journal.events(SESSION) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "stall"}]


async def test_bu06_an_engine_making_new_calls_is_not_stopped() -> None:
    world = EngineWorld()
    lines = [f'tool lookup {{"jql": "project = P{i}"}}' for i in range(STALL_REPEAT_ROUNDS + 3)]
    world.admit(RUN, _calls(lines))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_REPEAT_ROUNDS + 3
    completed = [e.data for e in world.journal.events(SESSION) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered"}]


async def test_bu06_wp62_an_unmetered_engine_repeating_one_call_is_stopped_too() -> None:
    """Without the ledger (an engine on the person's own sign-in) the turns are the ones the engine reported, which its
    event stream may deliver a call or two late: the stop comes no earlier than metered, and still comes."""
    world = EngineWorld()
    total = STALL_REPEAT_ROUNDS + 6
    world.admit(RUN, _calls(['tool lookup {"jql": "project = PP"}'] * total))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert STALL_REPEAT_ROUNDS <= sum(world.effects.values()) < total
    completed = [e.data for e in world.journal.events(SESSION) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "stall"}]
