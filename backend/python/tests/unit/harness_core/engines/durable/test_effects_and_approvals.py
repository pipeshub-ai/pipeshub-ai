"""EK-8..EK-11 (E1): the run's tool endpoint per attempt, effects a dead attempt left, approvals."""

from __future__ import annotations

import asyncio

import pytest

from harness_core.effects import args_digest
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.journal.types import CommitRequest, NewEvent
from harness_core.testing.engines.durable import CREATE, LOOKUP, SESSION, EngineWorld

RUN = "run-fx"


@pytest.fixture
def world() -> EngineWorld:
    return EngineWorld(approvals={"create": "allow"})


def state(world: EngineWorld):  # noqa: ANN201
    return fold_engine_run(world.journal.events(SESSION), run_id=RUN)


async def crash(world: EngineWorld, point: str) -> None:
    assert await world.crash_at(RUN, point)


async def test_ek8_intents_and_outcomes_are_journaled_and_an_identical_call_is_recorded(world: EngineWorld) -> None:
    world.admit(RUN, 'tool create {"name": "a"}\ntool create {"name": "a"}\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    assert sum(world.effects.values()) == 1
    types = world.types(RUN)
    assert types.count("tool.intent") == 1 and types.count("tool.outcome") == 1
    ids = [e.data["invocationId"] for e in world.events(RUN) if e.type == "tool.intent"]
    assert all(str(i).startswith(f"inv_{RUN}.a1_") for i in ids)


async def test_ek8_a_completed_effect_is_recorded_for_a_later_attempt(world: EngineWorld) -> None:
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    await crash(world, "engine.turn.before_checkpoint")
    assert sum(world.effects.values()) == 1
    assert (await world.attempt(RUN)).status == "completed"
    assert world.duplicates() == {}
    assert sum(world.effects.values()) == 1


async def test_ek9_a_non_idempotent_effect_without_outcome_becomes_unknown_and_never_runs_again(world: EngineWorld) -> None:
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    await crash(world, "worker.tool.after_effect")
    assert sum(world.effects.values()) == 1
    assert (await world.attempt(RUN)).status == "completed"
    assert world.duplicates() == {}
    assert [i.state for i in state(world).invocations.values()] == ["unknown"]
    assert "tool.unknown" in world.types(RUN)


async def test_ek9_an_idempotent_effect_without_outcome_runs_once_more(world: EngineWorld) -> None:
    world.admit(RUN, 'tool lookup {"q": "a"}' + "\nsay done")
    claim = await world.journal.claim(RUN, worker_id="dead")
    assert claim is not None
    intent = NewEvent(
        "tool.intent",
        {
            "invocationId": "inv_dead_1",
            "toolId": LOOKUP.effect.tool_id,
            "argsDigest": args_digest({"q": "a"}),
            "effect": "read",
            "sink": {"egress": "external", "argsLeaveBoundary": True},
        },
    )
    _ = await world.journal.commit(CommitRequest("dead:intent:1", claim.token, (intent,)))
    world.expire()
    assert (await world.attempt(RUN)).status == "completed"
    invocations = state(world).invocations
    assert invocations["inv_dead_1"].status == "error"
    assert sum(world.effects.values()) == 1


async def test_ek10_an_approval_decided_within_the_hold_runs_once() -> None:
    world = EngineWorld(approvals={"create": "ask"})
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')

    async def approve() -> None:
        while True:
            opened = world.journal.open_interactions(RUN)
            if opened:
                world.journal.resolve_interaction(RUN, next(iter(opened)), "approved")
                world.wakeup.wake(RUN)
                return
            await asyncio.sleep(0.001)

    approver = asyncio.ensure_future(approve())
    disposition = await world.attempt(RUN)
    await approver
    assert disposition.status == "completed"
    assert sum(world.effects.values()) == 1


async def test_ek10_a_denied_call_never_runs() -> None:
    world = EngineWorld(approvals={"create": "deny"})
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    assert sum(world.effects.values()) == 0
    assert "tool.denied" in world.types(RUN)


async def test_ek10_ek11_an_undecided_approval_suspends_and_the_re_call_finds_the_decision() -> None:
    world = EngineWorld(approvals={"create": "ask"})
    world.admit(RUN, 'tool create {"name": "a"}\nsay waiting')
    disposition = await world.attempt(RUN)
    assert disposition.status == "suspended"
    interaction = disposition.reason
    assert interaction is not None and interaction.startswith("ix.approve.k")
    assert sum(world.effects.values()) == 0
    approval = [c for c in world.presenter.calls if c[0] == "approval"]
    assert approval == [("approval", "create")]
    requested = [e.type for e in world.events(RUN)]
    assert requested.index("x.test.engine.progress", requested.index("turn.started")) < requested.index(
        "interaction.requested"
    )
    _ = world.journal.queue_input(RUN, 'tool create {"name": "a"}\nsay done')
    world.journal.resolve_interaction(RUN, interaction, "approved")
    assert world.journal.status(RUN) == "queued"
    assert (await world.attempt(RUN)).status == "completed"
    assert world.effects == {("create", next(iter(world.effects))[1]): 1}
    assert world.types(RUN).count("turn.started") == 2


async def test_ek10_a_re_call_after_a_denial_is_refused() -> None:
    world = EngineWorld(approvals={"create": "ask"})
    world.admit(RUN, 'tool create {"name": "a"}\nsay waiting')
    disposition = await world.attempt(RUN)
    assert disposition.status == "suspended" and disposition.reason is not None
    _ = world.journal.queue_input(RUN, 'tool create {"name": "a"}\nsay done')
    world.journal.resolve_interaction(RUN, disposition.reason, "denied")
    assert (await world.attempt(RUN)).status == "completed"
    assert sum(world.effects.values()) == 0


def test_create_is_not_idempotent() -> None:
    assert CREATE.effect.idempotent is False


async def test_fixp2_b3_a_turn_the_engine_fails_on_a_deferred_call_suspends_for_the_approval() -> None:
    """FIX-P2-B3: when a call waits for an approval past the gateway's hold, Codex 0.160 gets it back deferred and
    ends the turn failed; the kernel failed the run, so a person who answered after two minutes found the run gone
    (S3 workflow-refunds-1h and workflow-injected-ticket on Codex, 2 of 12 workflow trials: turn_ended reason=failed
    deferred=1). A turn that ends with an undecided deferred call waits for the person, as a completed one does;
    the decision then reaches the engine and the call runs once."""
    from harness_core.testing.engines.durable import AFTER_TOOLS

    world = EngineWorld(approvals={"create": "ask"})
    world.adapter.fail_with.append(f"{AFTER_TOOLS}other")  # type: ignore[attr-defined]
    world.admit(RUN, 'tool create {"name": "a"}\nsay waiting')
    disposition = await world.attempt(RUN)
    assert disposition.status == "suspended", disposition
    interaction = disposition.reason
    assert interaction is not None and interaction.startswith("ix.approve.k")
    assert "run.failed" not in world.types(RUN)
    _ = world.journal.queue_input(RUN, 'tool create {"name": "a"}\nsay done')
    world.journal.resolve_interaction(RUN, interaction, "approved")
    assert (await world.attempt(RUN)).status == "completed"
    assert sum(world.effects.values()) == 1


async def test_fixp2_b3_a_failed_turn_with_no_call_waiting_still_fails() -> None:
    from harness_core.testing.engines.durable import AFTER_TOOLS

    world = EngineWorld(approvals={"create": "allow"})
    world.adapter.fail_with.append(f"{AFTER_TOOLS}other")  # type: ignore[attr-defined]
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    assert (await world.attempt(RUN)).status == "failed"
