"""K-L CR-1..CR-5: a deep run's parent and children crash, are cancelled and are
quarantined; the run recovers to legacy's answer, admitting each child once."""

from __future__ import annotations

import json

import pytest

from harness_core.contracts.generated.common import ContentRef
from tests.contracts.deep_scenarios import DEEP_SCENARIOS, ORCHESTRATOR
from tests.contracts.test_durable_deep_goldens import (
    RUN,
    DurableDeep,
    coordinator_may_ask,
    durable_answer,
    legacy_ending,
    record_legacy,
    requests,
    run_deep_durably,
)

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

SINGLE = next(d for d in DEEP_SCENARIOS if d.name == "deep_single_child")
WAVES = next(d for d in DEEP_SCENARIOS if d.name == "deep_dependent_steps")


def _count(run: DurableDeep, type_: str, stream: str = "ses-1") -> int:
    return sum(1 for e in run.journal.events(stream) if e.type == type_ and (stream != "ses-1" or e.run_id == RUN))


async def _same_as_legacy(run: DurableDeep, scenario: object) -> None:
    sse, legacy = await record_legacy(scenario)  # type: ignore[arg-type]
    assert await durable_answer(run) == legacy_ending(sse)
    expected = {ORCHESTRATOR: requests(legacy.agents[ORCHESTRATOR])}
    coordinator_may_ask(expected)
    assert requests(run.script.agents[ORCHESTRATOR]) == expected[ORCHESTRATOR]


async def test_cr1_a_parent_that_dies_after_spawning_rejoins_without_spawning_again() -> None:
    run = await run_deep_durably(WAVES, crash=("parent", "worker.spawn.after_admission", 1))
    assert len(run.children()) == 2
    assert _count(run, "child.spawned") == 2
    assert run.journal.claims(RUN) >= 3
    await _same_as_legacy(run, WAVES)


async def test_cr1b_a_parent_that_dies_before_it_suspends_joins_on_the_next_attempt() -> None:
    run = await run_deep_durably(SINGLE, crash=("parent", "worker.batch.before_suspend", 1))
    assert len(run.children()) == 1 and _count(run, "child.spawned") == 1
    await _same_as_legacy(run, SINGLE)


async def test_cr2_a_child_that_dies_is_reclaimed_completes_and_wakes_its_parent_once() -> None:
    run = await run_deep_durably(SINGLE, crash=("child", "worker.commit.after_ack", 2))
    (child,) = run.children()
    assert run.journal.status(child) == "completed"
    assert run.journal.claims(child) == 2
    assert _count(run, "run.resumed") == 1
    await _same_as_legacy(run, SINGLE)


async def test_cr3_cancelling_the_parent_cancels_its_children() -> None:
    run = await run_deep_durably(SINGLE, cancel_parent_after_spawn=True)
    (child,) = run.children()
    assert run.journal.status(RUN) == "cancelled"
    assert run.journal.status(child) == "cancelled"
    assert _count(run, "child.completed") == 0


async def test_cr4_a_quarantined_child_is_a_failed_result_and_the_parent_answers() -> None:
    run = await run_deep_durably(SINGLE, crash=("child", "worker.commit.after_ack", 1, 3))
    (child,) = run.children()
    assert run.journal.status(child) == "quarantined"
    (completed,) = [e for e in run.journal.events("ses-1") if e.type == "child.completed"]
    assert completed.data["outcome"] == "failed"
    shown = [e for e in run.journal.events("ses-1") if e.type == "tool.outcome" and e.data["invocationId"] == "t3.0"]
    assert [e.data["status"] for e in shown] == ["error"]
    content = json.loads(await run.content.get(ContentRef.model_validate(shown[0].data["resultRef"])))
    assert content["isError"] is True
    answer, outcome = await durable_answer(run)
    assert outcome == "answered" and answer


async def test_cr5_a_parent_that_dies_after_the_dispatch_turn_admits_each_child_once() -> None:
    # The third model commit is the programmatic dispatch (plan, critique, dispatch).
    run = await run_deep_durably(SINGLE, crash=("parent", "worker.model.after_response_commit", 3))
    assert len(run.children()) == 1 and _count(run, "child.spawned") == 1
    assert sum(1 for e in run.journal.events("ses-1") if e.type == "model.response" and e.run_id == RUN) == 5
    await _same_as_legacy(run, SINGLE)
