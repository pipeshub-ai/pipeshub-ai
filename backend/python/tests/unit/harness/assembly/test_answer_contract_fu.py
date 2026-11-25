"""ANSWER-CONTRACT-FU-2 (ADR-RUN-03): what the person sees reads cleanly, in their own language.

The not-found line and the grounding question card were English prose composed in Python. Now each travels as typed
fields beside its English (which a surface with no UI keeps): the answer's ``notFound`` (subject, hits, the closest
records' names when the person could not be asked, and the English ``line`` the answer starts with), and the card's
``grounding`` (subject, near matches and the English labels its reply is read by), which the web chat words through
its locale keys. A ``code_task`` the model calls while the person is asked first is refused by design (D1); it is
part of asking, so it is no step of its own and never reads as "1 failed".
"""

from __future__ import annotations

import json
from typing import Any

from app.harness.assembly.code_task import PENDING_QUESTION_KEY
from app.harness.assembly.loop import CLARIFYING_INTENT_KEY, GOAL_SLOT
from app.harness.contracts.generated.pipeshub.events.x_pipeshub_answer_finalized import (
    PipeshubAnswerFinalizedData,
)
from app.harness.grounding import (
    GROUNDING_CARD_KEY,
    GROUNDING_KEY,
    JOB_GROUNDING_SLOT,
    NAME_IT_LABEL,
    USE_FOUND_LABEL,
    Grounding,
    grounding_card,
    grounding_question,
    not_found_line,
    not_found_view,
)
from app.harness.presentation import QUESTION_ASKED, PipesHubPresenter
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import AssistantMessage, ToolCall
from harness_core.kernel.core.types import ToolResult
from harness_core.kernel.durable.ports import RunEnding
from harness_core.tools.registry import ToolRegistry
from tests.unit.agents.adapter.conftest import make_context

FINALIZED = "x.pipeshub.answer.finalized"
ASK = "internaltools__ask_user_question"
SAAS = Grounding("not_found", "SAAS launch", 7, 2, near=("product-faq", "on-call-runbook"))
ANSWERED = Grounding("not_found", "SAAS launch", 7, 2, near=("product-faq",), proceeded=True)


def _saved(seq: int, **slots: Any) -> JournalEvent:  # noqa: ANN401
    return JournalEvent(
        org_id="org", session_id="s", stream_id="s", seq=seq, run_id="run-1", type="loop.state_saved",
        critical=False, ts="2026-11-11T12:00:00Z", writer="worker", data={"slots": slots},
    )


def _presenter() -> PipesHubPresenter:
    return PipesHubPresenter(make_context(conversation_id="s-1", protocol="agui", run_id="run-1"), ToolRegistry())


async def _finalized(ending: RunEnding) -> list[tuple[str, dict[str, Any]]]:
    return [(e.type, dict(e.data)) for e in await _presenter().finalize(ending)]


# ---- the answer: a typed not-found fact beside its English line ----


def test_fu2_the_not_found_fact_names_the_subject_and_the_line_the_answer_starts_with() -> None:
    view = not_found_view(SAAS)
    assert view == {"subject": "SAAS launch", "hits": 2, "line": not_found_line(SAAS),
                    "near": ["product-faq", "on-call-runbook"]}
    # The person was asked and chose to go on: the near matches were on the card, so the answer does not name them.
    assert not_found_view(ANSWERED) == {"subject": "SAAS launch", "hits": 2, "line": not_found_line(ANSWERED),
                                        "proceeded": True}
    assert not_found_view(Grounding("found", "SAAS launch", 1, 1, 1)) is None
    assert not_found_view(None) is None
    # Record names stay out of the English: only the typed field carries them, which Node keeps from other viewers.
    assert "product-faq" not in str(not_found_line(SAAS))


async def test_fu2_the_finalized_answer_carries_the_fact_and_still_starts_with_the_english_line() -> None:
    ending = RunEnding(
        outcome="answered", texts=("Preliminary risk report.",),
        events=(_saved(1, **{JOB_GROUNDING_SLOT: SAAS.to_json()}),),
    )
    [(_, data)] = [(t, d) for t, d in await _finalized(ending) if t == FINALIZED]
    line = str(not_found_line(SAAS))
    assert data["notFound"] == not_found_view(SAAS)
    assert data["answer"] == f"{line}\n\nPreliminary risk report."
    assert data["confidence"] == "Low"
    assert PipeshubAnswerFinalizedData.model_validate(data).not_found is not None


async def test_fu2_a_found_subject_or_an_unchecked_run_carries_no_fact() -> None:
    for grounding in (Grounding("found", "SAAS launch", 1, 1, 1), Grounding("unchecked")):
        ending = RunEnding(outcome="answered", texts=("Report.",),
                           events=(_saved(1, **{JOB_GROUNDING_SLOT: grounding.to_json()}),))
        [(_, data)] = [(t, d) for t, d in await _finalized(ending) if t == FINALIZED]
        assert "notFound" not in data and data["answer"] == "Report."


# ---- the card: typed fields the web chat words ----


def test_fu2_the_card_s_typed_fields_hold_the_english_its_reply_is_read_by() -> None:
    assert grounding_card(SAAS) == {"subject": "SAAS launch", "near": ["product-faq", "on-call-runbook"],
                                    "useFound": USE_FOUND_LABEL, "nameIt": NAME_IT_LABEL}
    _, question = grounding_question(SAAS)
    labels = [o["label"] for o in question["options"]]  # type: ignore[index, union-attr]
    assert labels == ["product-faq", "on-call-runbook", USE_FOUND_LABEL]


async def test_fu2_the_question_asked_before_any_job_carries_its_typed_card() -> None:
    intent, question = grounding_question(SAAS)
    goal = {"description": "d", "clarifyingQuestions": [question], CLARIFYING_INTENT_KEY: intent,
            GROUNDING_KEY: SAAS.to_json()}
    events = await _finalized(RunEnding(outcome="needs_input", events=(_saved(1, **{GOAL_SLOT: goal}),)))
    [asked] = [d for t, d in events if t == QUESTION_ASKED]
    assert asked["toolData"][GROUNDING_CARD_KEY] == grounding_card(SAAS)
    assert asked["toolData"]["questions"][0]["question"] == question["question"]
    # Another pre-run question (the classic intent check's) carries none.
    other = {"description": "d", "clarifyingQuestions": [{"question": "Which project?", "options": [
        {"label": "Atlas"}, {"label": "Orbit"}]}]}
    [plain] = [d for t, d in await _finalized(RunEnding(outcome="needs_input", events=(_saved(1, **{GOAL_SLOT: other}),)))
               if t == QUESTION_ASKED]
    assert GROUNDING_CARD_KEY not in plain["toolData"]


def _pending_slots() -> tuple[dict[str, Any], str, dict[str, Any]]:
    intent, question = grounding_question(SAAS)
    goal = {"description": "d", PENDING_QUESTION_KEY: {"intent": intent, "question": question},
            GROUNDING_KEY: SAAS.to_json()}
    return {GOAL_SLOT: goal}, intent, question


def test_fu2_a_hand_off_refused_while_the_person_is_asked_is_part_of_asking_not_a_failed_step() -> None:
    slots, intent, question = _pending_slots()
    presenter = _presenter()
    presenter.pre_run(slots)
    hand_off = ToolCall(id="c1", name="code_task", arguments={"goal": "Prepare the report"})
    [presented] = [e for e in presenter.response(AssistantMessage(content=[], tool_calls=[hand_off]))
                   if e.type == "x.pipeshub.tool.call_presented"]
    assert presented.data["hidden"] is True
    # The director's card, asked right after it, carries the typed fields too.
    asked = ToolCall(id="deeproute-ask-2", name=ASK, arguments={"user_intent": intent, "questions": [question]})
    payload = {"user_intent": intent, "questions": [question]}
    result = ToolResult(tool_call_id=asked.id, name=ASK, content=json.dumps(payload))
    [card] = [e for e in presenter._question(asked, result) if e is not None]  # pyright: ignore[reportPrivateUsage]
    assert card.data["toolData"][GROUNDING_CARD_KEY] == grounding_card(SAAS)


def test_fu2_without_a_pending_question_a_hand_off_and_another_card_are_as_before() -> None:
    presenter = _presenter()
    presenter.pre_run({GOAL_SLOT: {"description": "d"}})
    hand_off = ToolCall(id="c1", name="code_task", arguments={"goal": "Prepare the report"})
    [presented] = [e for e in presenter.response(AssistantMessage(content=[], tool_calls=[hand_off]))
                   if e.type == "x.pipeshub.tool.call_presented"]
    assert "hidden" not in presented.data
    slots, _, _ = _pending_slots()
    presenter.pre_run(slots)
    other = {"user_intent": "u", "questions": [{"question": "Which project?", "options": [{"label": "A"}, {"label": "B"}]}]}
    call = ToolCall(id="c2", name=ASK, arguments=other)
    [card] = [e for e in presenter._question(call, ToolResult(tool_call_id="c2", name=ASK, content=json.dumps(other)))  # pyright: ignore[reportPrivateUsage]
              if e is not None]
    assert GROUNDING_CARD_KEY not in card.data["toolData"]
