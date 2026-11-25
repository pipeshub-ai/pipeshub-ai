"""DEEPROUTE, scripted and durable: a recorded shape on the real harness, routed to the coding engine, not ground.

In a manual test, a quick chat asked to triage "many open pull requests"; the loop kept it, fetched page after page
(results of 766k-867k tokens before capping) and failed ``budget_exhausted`` at 1.14M tokens with no answer.

The same shape here: a quick-mode agent chat whose spec pins an engine authority (so ``code_task`` is offered), on the
real ``PipesHubRuntimeAssembler``, ``RunDriver`` and durable kernel over the in-memory journal, with the code_task child
on the engine kernel (``EngineWorld``'s scripted engine). The parent's model is the recorded one: it fetches one more
page per turn, each request larger than the last, and the reply's budget is the ledger's (a refused call fails the run
``budget_exhausted``, as the recorded run did). It does what it is told: when PipesHub's system channel asks for a
hand-off it calls ``code_task`` with a brief, and once the engine answers it presents that answer.

* routing on, a request the router keeps in the loop: the run is escalated at the threshold, the engine answers, the
  run completes within the budget with the engine's answer and its citations;
* routing on, the recorded request: handed off before the first fetch;
* routing on, a model that ignores the instruction: the director hands off itself, once;
* routing off (the org's switch): the same run grinds until the budget refuses it, as the recorded run did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.assembly.deep_route import HANDOFF_EVENT

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.journal.types import JournalEvent

from tests.support.longtask_rig.deeproute import (
    ANSWER,
    BUDGET,
    CODE_TASK_ID,
    JIRA_SEARCH,
    NARROW,
    PARENT,
    RECORDED_REQUEST,
    RecordedModel,
    Rig,
    deployment,
)


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with deployment():
        yield


def _child_tokens(rig: Rig) -> int:
    total = 0
    for child in rig.world.journal.children_of(PARENT):
        for event in rig.events(child):
            usage = event.data.get("usage") if event.type in ("model.response", "engine.turn_completed") else None
            if isinstance(usage, dict):
                total += int(usage.get("inputTokens") or 0) + int(usage.get("outputTokens") or 0)
    return total


def _intents(rig: Rig, tool: str) -> list[JournalEvent]:
    return [e for e in rig.parent("tool.intent") if e.data.get("toolId") == tool]


def _director_turns(rig: Rig) -> list[JournalEvent]:
    return [e for e in rig.parent("model.response") if e.data.get("provider") == "director"]


async def test_deeproute_recorded_shape_is_escalated_and_answers_within_budget() -> None:
    rig = Rig(RecordedModel())
    done = await rig.drive(NARROW)
    assert done.status == "completed", done
    (handoff,) = rig.parent(HANDOFF_EVENT)
    assert handoff.data == {"trigger": "escalation", "reason": "many_steps"}
    # The recorded first five turns (14 calls) cross the 12-call threshold; the sixth turn is the hand-off.
    assert len(_intents(rig, JIRA_SEARCH)) == 14
    assert len(_intents(rig, CODE_TASK_ID)) == 1
    completed = next(e for e in rig.events() if e.type == "child.completed")
    assert completed.data["outcome"] == "answered"
    # The engine's answer is the run's answer, with its citation; the loop does not re-read its history to repeat it.
    (answered,) = _director_turns(rig)
    answers = rig.parent("x.pipeshub.answer.finalized")
    assert answers and ANSWER in str(answers[-1].data.get("answer")), answers
    assert rig.model.turns == 6
    assert rig.model.spent + _child_tokens(rig) < BUDGET, (rig.model.spent, _child_tokens(rig))
    assert answered.data.get("provider") == "director"


async def test_deeproute_recorded_request_is_handed_off_before_the_first_fetch() -> None:
    rig = Rig(RecordedModel())
    done = await rig.drive(RECORDED_REQUEST)
    assert done.status == "completed", done
    (handoff,) = rig.parent(HANDOFF_EVENT)
    assert handoff.data == {"trigger": "upfront", "reason": "analysis"}
    assert not _intents(rig, JIRA_SEARCH)
    assert rig.model.turns == 1  # the hand-off; the answer is the engine's
    answers = rig.parent("x.pipeshub.answer.finalized")
    assert answers and ANSWER in str(answers[-1].data.get("answer"))
    assert rig.model.spent < 50_000


async def test_deeproute_a_model_that_ignores_the_instruction_is_handed_off_by_the_director() -> None:
    rig = Rig(RecordedModel(obeys=False))
    done = await rig.drive(NARROW)
    assert done.status == "completed", done
    # One synthetic code_task (the director's own brief, from the journal), then the engine's answer.
    assert len(_director_turns(rig)) == 2
    (spawned,) = _intents(rig, CODE_TASK_ID)
    # The instruction came with turn 6, whose model fetched again; the director took turn 7 itself.
    assert spawned.data["invocationId"] == "t7.0"
    child = rig.world.journal.children_of(PARENT)[0]
    assert any(e.type == "run.completed" for e in rig.events(child))
    assert rig.parent("x.pipeshub.answer.finalized")


async def test_deeproute_off_the_same_run_grinds_until_the_budget_cuts_it_short() -> None:
    rig = Rig(RecordedModel())
    done = await rig.drive(NARROW, auto_route=False)
    # BUDGETEND's wrap-up reserve turns the spent budget into a partial answer flagged as cut short, not a failure.
    assert done.status == "completed", done
    assert [e.data.get("truncatedBy") for e in rig.parent("run.completed")] == ["budget"]
    assert not rig.parent(HANDOFF_EVENT) and not rig.parent("child.spawned")
    assert len(_intents(rig, JIRA_SEARCH)) > 14
