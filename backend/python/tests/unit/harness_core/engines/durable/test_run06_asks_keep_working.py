"""ADR-RUN-06: an ask never stops the work (RUN06-1..6).

A Codex research job asked to clone a repository three minutes in, was told "End this turn", and sat
suspended for the 40 minutes the person took to allow it; its second turn then ran eight minutes on a time plan whose
clock times had passed. Here the held call tells the engine to keep working, the decision reaches the turn that is
still running (steered, or on the next result for an engine that cannot be steered), the engine calls the approved
tool again and uses its result in the same turn, and a later turn gets an allowance of its own.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from harness_core.engines.durable.kernel import RENEWED_ALLOWANCE
from harness_core.gateway.endpoint import WHILE_WAITING
from harness_core.testing.engines.durable import EngineWorld
from harness_core.testing.engines.fake_engine import FakeEngineAdapter
from harness_core.testing.engines.script import KEEP_WORKING_MARK

RUN = "run-asks"
# Ask to create (held past the gateway's hold), keep reading while the person decides, then finish.
SCRIPT = 'tool create {"name": "a"}\nhold\ntool lookup {"q": "one"}\ntool lookup {"q": "two"}\nsay done'
RELAYED = "engine.kernel.decision_relayed"


def world(*, can_steer: bool = True) -> EngineWorld:
    built = EngineWorld(approvals={"create": "ask"})
    if not can_steer:
        built.adapter = FakeEngineAdapter(can_steer=False)
    return built


async def _held(w: EngineWorld) -> str:
    """The person's card, once the engine's call has come back deferred and the engine went on (its model holds)."""
    while not w.gateway.holding.is_set():  # noqa: ASYNC110 - the gateway's event says the model call is held
        await asyncio.sleep(0.001)
    [interaction] = w.journal.open_interactions(RUN)
    return interaction


async def _relayed(caplog: pytest.LogCaptureFixture) -> None:
    while not any(r.getMessage().startswith(RELAYED) for r in caplog.records):  # noqa: ASYNC110
        await asyncio.sleep(0.001)


def _lookups(w: EngineWorld) -> int:
    return sum(n for (tool, _digest), n in w.effects.items() if tool == "lookup")


def _creates(w: EngineWorld) -> int:
    return sum(n for (tool, _digest), n in w.effects.items() if tool == "create")


def test_run06_1_the_held_call_says_to_keep_working_not_to_end_the_turn() -> None:
    assert "End this turn" not in WHILE_WAITING
    assert WHILE_WAITING.startswith(KEEP_WORKING_MARK)


@pytest.mark.parametrize("can_steer", [True, False], ids=["steered", "on_the_next_result"])
async def test_run06_2_the_person_allows_later_and_the_same_turn_uses_the_result(
    can_steer: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="harness_core.engines.durable")
    w = world(can_steer=can_steer)
    w.admit(RUN, SCRIPT)
    attempt = asyncio.ensure_future(w.attempt(RUN))
    interaction = await _held(w)
    assert _creates(w) == 0
    w.journal.resolve_interaction(RUN, interaction, "approved")
    w.wakeup.wake(RUN)
    await _relayed(caplog)
    w.gateway.release()
    disposition = await attempt
    assert disposition.status == "completed"
    # One turn: the job never suspended on its ask, and the approved call ran once, within it.
    assert w.types(RUN).count("turn.started") == 1
    assert _creates(w) == 1 and _lookups(w) == 2
    relayed = next(r.getMessage() for r in caplog.records if r.getMessage().startswith(RELAYED))
    assert ("via=steer" if can_steer else "via=result") in relayed


async def test_run06_3_research_goes_on_while_the_ask_waits() -> None:
    """Before anyone decides, the engine's other calls run: the ask held nothing but its own call."""
    w = world()
    w.admit(RUN, SCRIPT)
    attempt = asyncio.ensure_future(w.attempt(RUN))
    _ = await _held(w)
    w.gateway.release()
    disposition = await attempt
    # Nothing left to do without the call: the turn ends and the run waits for the person, its reads done.
    assert disposition.status == "suspended"
    assert _lookups(w) == 2 and _creates(w) == 0


async def test_run06_4_a_decision_after_the_turn_ended_reaches_the_next_turn_once() -> None:
    w = world()
    w.admit(RUN, SCRIPT)
    attempt = asyncio.ensure_future(w.attempt(RUN))
    _ = await _held(w)
    w.gateway.release()
    disposition = await attempt
    assert disposition.status == "suspended" and disposition.reason is not None
    w.journal.resolve_interaction(RUN, disposition.reason, "approved")
    assert (await w.attempt(RUN)).status == "completed"
    assert _creates(w) == 1
    follow_up = [c for c in w.gateway.model_calls if any("are decided" in m.content for m in c.messages)]
    assert follow_up, "the next turn tells the engine the decision"


async def test_run06_5_a_later_turn_gets_an_allowance_of_its_own() -> None:
    """D4: the restored session's first time plan is stale after a wait; the follow-up turn says this turn's."""
    w = world()
    w.admit(RUN, SCRIPT)
    attempt = asyncio.ensure_future(w.attempt(RUN))
    _ = await _held(w)
    w.gateway.release()
    disposition = await attempt
    assert disposition.reason is not None
    w.journal.resolve_interaction(RUN, disposition.reason, "approved")
    assert (await w.attempt(RUN)).status == "completed"
    assert "Your allowance for this task is" in w.gateway.model_calls[0].system
    follow_ups = [
        m.content for c in w.gateway.model_calls for m in c.messages if m.role == "user" and "are decided" in m.content
    ]
    assert follow_ups and all(RENEWED_ALLOWANCE + "Your allowance for this task is" in t for t in follow_ups)


async def test_run06_6_a_denial_mid_turn_is_relayed_and_the_call_never_runs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="harness_core.engines.durable")
    w = world()
    w.admit(RUN, SCRIPT)
    attempt = asyncio.ensure_future(w.attempt(RUN))
    interaction = await _held(w)
    w.journal.resolve_interaction(RUN, interaction, "denied")
    w.wakeup.wake(RUN)
    await _relayed(caplog)
    w.gateway.release()
    assert (await attempt).status == "completed"
    assert _creates(w) == 0 and _lookups(w) == 2
    assert w.types(RUN).count("turn.started") == 1
