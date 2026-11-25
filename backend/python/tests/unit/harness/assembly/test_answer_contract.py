"""ANSWER-CONTRACT-1 (ADR-RUN-03): the answer contract holds on the job path.

Found in a manual test: "Prepare a risk report for 'SAAS launch'" went to a Codex job, which searched the knowledge base
seven times, found nothing about the subject and wrote a full report from a product FAQ and an on-call runbook. The
chat asked nothing, said nothing about not finding it, and showed a "Medium" its parent model picked. These tests pin
the structure that replaces it: the typed grounding signal, the question before any job starts, the job's question
asked in the chat, the typed summary a job hands back, the plain not-found line and the confidence it allows.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.agents.chat_modes.prefetch import PrefetchResult
from app.harness.assembly.code_task import _result_frame  # pyright: ignore[reportPrivateUsage]
from app.harness.assembly.deep_route import JOB_ASK_INTENT, job_question
from app.harness.assembly.grounding_gate import (
    GroundingGate,
    can_ask,
    hand_off_request,
    may_ask,
)
from app.harness.assembly.task_spec import TaskSpec, request_of
from app.harness.engines.child_result import QUESTION_KEY, asked_question, job_grounding
from app.harness.grounding import (
    GROUNDING_KEY,
    JOB_GROUNDING_SLOT,
    USE_FOUND_LABEL,
    Grounding,
    Hit,
    assess,
    capped,
    combined,
    confidence_for,
    grounding_answer,
    grounding_question,
    hits_from_state,
    is_knowledge_search,
    named_subject,
    not_found_line,
    request_subject,
)
from app.harness.presentation import _grounded, run_grounding  # pyright: ignore[reportPrivateUsage]
from app.utils.chat_helpers import CitationRefMapper
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import TurnResult
from harness_core.logs import HarnessLog

from .test_deeproute import _director, _state  # pyright: ignore[reportPrivateUsage]

LOG = HarnessLog(logging.getLogger("test.answer_contract"), {})
REQUEST = 'Prepare a risk report for "SAAS launch"'
# What the job's searches showed: a product FAQ and a generic runbook, neither about the launch.
FAQ = {"content": "PipesHub Orbit is a SaaS product. Support hours 08:00-18:00 CET.", "score": 0.71,
       "metadata": {"recordName": "product-faq", "recordId": "r1"}}
RUNBOOK = {"content": "Severity 1: open the incident channel, page the incident commander.", "score": 0.52,
           "metadata": {"recordName": "on-call-runbook", "recordId": "r2"}}
LAUNCH = {"content": "SAAS launch plan: go-live on 1 December, finance review due Friday.", "score": 0.8,
          "metadata": {"recordName": "saas-launch-plan", "recordId": "r3"}}


# ---- the signal: named subject, assessment, confidence ----


@pytest.mark.parametrize(
    ("request_text", "subject"),
    [
        (REQUEST, "SAAS launch"),
        ("Prepare a risk report for 'SAAS launch'", "SAAS launch"),
        ("Prepare a risk report for “SAAS launch”", "SAAS launch"),
        ("Prepare a risk report for SAAS launch", "SAAS"),
        ("What is the status of Project Atlas?", "Project Atlas"),
        ("Summarize the Q3 PDF", None),
        ("Write a summary of our last meeting", None),
        ("It's the team's plan", None),
        # A scripted turn's directives (the e2e lane's model) and their JSON name nothing.
        ('tool code_task {"goal": "x"}\nsay done\nReport on "Orbit pricing"', "Orbit pricing"),
        ('tool code_task {"goal": "x"}\nsay done', None),
    ],
)
def test_ac1_the_subject_a_request_names(request_text: str, subject: str | None) -> None:
    assert named_subject(request_text) == subject


def test_ac1_recorded_hits_do_not_hold_the_subject_and_near_matches_are_named() -> None:
    grounding = assess("SAAS launch", hits_from_state({"final_results": [FAQ, RUNBOOK]}), searches=7)
    assert (grounding.status, grounding.searches, grounding.hits, grounding.relevant) == ("not_found", 7, 2, 0)
    # Each holds one of its words ("SaaS", "launch"... the runbook has neither but "Severity"), most first.
    assert grounding.near == ("product-faq",)
    found = assess("SAAS launch", hits_from_state({"final_results": [FAQ, LAUNCH]}), searches=1)
    assert (found.status, found.relevant, found.near, found.sources) == ("found", 1, (), ("saas-launch-plan",))


def test_ac1_nothing_searched_or_a_failed_search_says_nothing_is_missing() -> None:
    assert assess(None, [Hit("a", "a")], searches=3).status == "unchecked"
    assert assess("SAAS launch", [], searches=0).status == "unchecked"
    assert assess("SAAS launch", [], searches=1, failed=True).status == "failed"
    assert assess("SAAS launch", [], searches=1).status == "not_found"


def test_ac1_the_typed_summary_round_trips_and_rejects_what_it_cannot_read() -> None:
    grounding = assess("SAAS launch", hits_from_state({"final_results": [FAQ]}), searches=2)
    assert Grounding.from_json(grounding.to_json()) == grounding
    assert Grounding.from_json({"status": "maybe"}) is None
    assert Grounding.from_json("found") is None


@pytest.mark.parametrize(
    ("grounding", "cited", "level"),
    [
        (Grounding("not_found", "x", 7, 2), 2, "Low"),
        (Grounding("found", "x", 1, 3, relevant=2), 3, "High"),
        (Grounding("found", "x", 1, 3, relevant=1), 3, "Medium"),
        (Grounding("found", "x", 1, 3, relevant=2), 0, "Low"),
        (Grounding("failed", "x", 1), 0, "Low"),
        (Grounding("unchecked", searches=4), 0, "Low"),
        (Grounding("unchecked", searches=4), 1, "Medium"),
        (Grounding("unchecked", searches=4), 3, "High"),
        # Nothing to judge from: no value at all, never a default.
        (Grounding("unchecked"), 0, None),
        (None, 5, None),
    ],
)
def test_ac3_confidence_is_computed_from_grounding(grounding: Grounding | None, cited: int, level: str | None) -> None:
    assert confidence_for(grounding, cited=cited) == level


def test_ac3_a_model_s_rating_never_claims_more_than_grounding_allows() -> None:
    assert capped("Very High", "Low") == "Low"
    assert capped("Low", "High") == "Low"
    assert capped(None, "Medium") == "Medium"
    assert capped("High", None) == "High"


def test_ac2_the_not_found_line_is_plain_and_names_the_subject() -> None:
    assert not_found_line(Grounding("found", "x")) is None
    none = not_found_line(Grounding("not_found", "SAAS launch", 3, 0))
    assert none is not None and none.startswith("**I couldn't find anything about “SAAS launch” in your knowledge")
    tangential = not_found_line(Grounding("not_found", "SAAS launch", 7, 2))
    assert tangential is not None and "2 related documents that do not mention it" in tangential


def test_ac1_the_question_offers_the_near_matches_then_going_on_and_its_answer_reads_back() -> None:
    grounding = Grounding("not_found", "SAAS launch", 7, 2, near=("product-faq",))
    intent, question = grounding_question(grounding)
    assert "“SAAS launch”" in intent
    assert [o["label"] for o in question["options"]] == ["product-faq", USE_FOUND_LABEL]  # type: ignore[index, union-attr]
    nothing_near = grounding_question(Grounding("not_found", "SAAS launch", 3, 0))[1]
    assert len(nothing_near["options"]) == 2  # type: ignore[arg-type]  # the card needs two; the app adds a third
    reply = f'User selections:\n1. "{question["question"]}" → {USE_FOUND_LABEL}'
    answer = grounding_answer(reply)
    assert answer is not None and (answer.subject, answer.choice) == ("SAAS launch", "use_found")
    picked = grounding_answer(f'User selections:\n1. "{question["question"]}" → product-faq')
    assert picked is not None and (picked.choice, picked.value) == ("picked", "product-faq")
    assert grounding_answer('User selections:\n1. "Which project?" → Atlas') is None
    # The request a hand-off carries after the answer is still about what the person chose, not the card's words.
    assert request_subject(f"{REQUEST}\n\n{reply}") == "SAAS launch"
    assert request_subject(f'{REQUEST}\n\nUser selections:\n1. "{question["question"]}" → Orbit launch') == (
        "Orbit launch"
    )


def test_ac2_the_job_s_grounding_wins_unless_it_checked_nothing_and_a_choice_to_go_on_stays() -> None:
    before = Grounding("not_found", "SAAS launch", 1, 0, proceeded=True)
    job = Grounding("not_found", "SAAS launch", 7, 2)
    assert combined(before, job) == Grounding("not_found", "SAAS launch", 7, 2, proceeded=True)
    assert combined(before, Grounding("unchecked")) == before
    assert combined(None, Grounding("found", "SAAS launch", 3, 1, 1)).status == "found"  # type: ignore[union-attr]
    assert combined(Grounding("unchecked"), None) is None


def test_ac2_knowledge_searches_by_tool_id() -> None:
    assert is_knowledge_search("knowledgegraph@org/knowledgegraph__search")
    assert is_knowledge_search("retrieval@org/retrieval__search_internal_knowledge")
    assert not is_knowledge_search("knowledgegraph@org/knowledgegraph__list_files")
    assert not is_knowledge_search("dynamic@org/dynamic__web_search")


# ---- D1: ground before delegating ----


def _run(query: str = REQUEST, **overrides: Any) -> Any:  # noqa: ANN401
    values = {"query": query, "previous_conversations": (), "unattended": False, "sub_agent": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def _context(**state: Any) -> Any:  # noqa: ANN401
    return SimpleNamespace(
        has_ui_client=True, org_id="org", user_id="u", user_info={"userId": "u"}, tool_state=dict(state), query=""
    )


def _chat(knowledge: bool = True) -> Any:  # noqa: ANN401
    return SimpleNamespace(
        policy=SimpleNamespace(has_knowledge=knowledge),
        setup=SimpleNamespace(query_info={"filters": {"apps": ["kb"]}}, blob_store=None),
        retrieval_service=object(),
        graph_provider=None,
    )


def _prefetch(*blocks: dict[str, Any], failure: str | None = None) -> PrefetchResult:
    return PrefetchResult(
        formatted_context="", final_results=list(blocks), virtual_record_id_to_result={}, tool_records=[],
        citation_ref_mapper=CitationRefMapper(), is_empty=not blocks, failure_notice=failure,
    )


async def test_ac1_not_found_asks_before_any_job_with_the_search_the_chat_already_ran() -> None:
    gate = GroundingGate(_run(), _context(), _chat(), LOG)
    with patch("app.agents.chat_modes.prefetch.prefetch_retrieval") as probe:
        checked = await gate.check(_prefetch(FAQ, RUNBOOK), delegating=True)
    probe.assert_not_called()
    assert checked is not None and checked.grounding.not_found and checked.question is not None
    assert checked.question["question"].startswith("I couldn't find anything about “SAAS launch”")  # type: ignore[union-attr]


async def test_ac1_a_subject_that_exists_starts_the_job_without_a_question() -> None:
    checked = await GroundingGate(_run(), _context(), _chat(), LOG).check(_prefetch(FAQ, LAUNCH), delegating=True)
    assert checked is not None and checked.grounding.status == "found" and checked.question is None


async def test_ac1_with_no_prefetch_one_cheap_search_for_the_subject_alone() -> None:
    calls: list[dict[str, Any]] = []

    async def probe(**kwargs: Any) -> PrefetchResult:  # noqa: ANN401
        calls.append(kwargs)
        return _prefetch()

    with patch("app.agents.chat_modes.prefetch.prefetch_retrieval", probe):
        checked = await GroundingGate(_run(), _context(), _chat(), LOG).check(None, delegating=True)
    assert [(c["query"], c["filters"], c["force"]) for c in calls] == [("SAAS launch", {"apps": ["kb"]}, True)]
    assert checked is not None and checked.grounding.not_found and checked.grounding.hits == 0
    assert checked.question is not None


async def test_ac1_a_failed_search_or_no_knowledge_never_says_not_found() -> None:
    failed = await GroundingGate(_run(), _context(), _chat(), LOG).check(
        _prefetch(failure="The search could not run."), delegating=True
    )
    assert failed is not None and failed.question is None

    async def broken(**_: Any) -> PrefetchResult:  # noqa: ANN401
        raise RuntimeError("down")

    with patch("app.agents.chat_modes.prefetch.prefetch_retrieval", broken):
        raised = await GroundingGate(_run(), _context(), _chat(), LOG).check(None, delegating=True)
    assert raised is not None and raised.question is None and raised.grounding.status == "unchecked"
    agent = await GroundingGate(_run(), _context(), None, LOG).check(None, delegating=True)
    assert agent is not None and agent.question is None


async def test_ac1_nothing_is_checked_when_the_run_does_not_delegate() -> None:
    assert await GroundingGate(_run(), _context(), _chat(), LOG).check(_prefetch(FAQ), delegating=False) is None


@pytest.mark.parametrize(
    ("label", "run", "ui"),
    [
        ("a schedule's run", _run(unattended=True), True),
        ("a sub-agent", _run(sub_agent=object()), True),
        ("no client to show a card", _run(), False),
        ("it answers a card", _run('User selections:\n1. "Which?" → A'), True),
    ],
)
def test_ac1_a_turn_that_may_not_ask_does_not(label: str, run: Any, ui: bool) -> None:  # noqa: ANN401
    assert not may_ask(run, has_ui_client=ui), label


def test_ac1_a_request_that_used_its_cards_asks_no_more() -> None:
    card = {"role": "bot_response", "content": "", "tool_results": [{"tool_name": "ask_user_question", "status": "success"}]}
    history = (
        {"role": "user_query", "content": REQUEST},
        card,
        {"role": "user_query", "content": 'User selections:\n1. "Which?" → A'},
        card,
    )
    assert can_ask(_run(previous_conversations=()), has_ui_client=True)
    assert not can_ask(_run(previous_conversations=history), has_ui_client=True)


async def test_ac1_use_what_you_can_find_goes_on_with_the_not_found_fact_kept() -> None:
    _, question = grounding_question(Grounding("not_found", "SAAS launch", 1, 2, near=("product-faq",)))
    reply = f'User selections:\n1. "{question["question"]}" → {USE_FOUND_LABEL}'
    history = ({"role": "user_query", "content": REQUEST}, {"role": "bot_response", "content": question["question"]})
    run = _run(reply, previous_conversations=history)
    checked = await GroundingGate(run, _context(), _chat(), LOG).check(_prefetch(FAQ, RUNBOOK), delegating=False)
    assert checked is not None and checked.question is None
    assert (checked.grounding.status, checked.grounding.proceeded) == ("not_found", True)
    # The job is handed the request the card asked about, with the person's answer after it.
    assert hand_off_request(run) == f"{REQUEST}\n\n{reply}"
    assert hand_off_request(_run()) == REQUEST


# ---- D2: the job asks through the chat, and hands back a typed summary ----


def _event(type_: str, seq: int, **data: Any) -> JournalEvent:  # noqa: ANN401
    return JournalEvent(
        org_id="org", session_id="s", stream_id="child", seq=seq, run_id="child", type=type_, critical=False,
        ts="2026-11-11T12:00:00Z", writer="worker", data=data,
    )


def test_ac2_the_job_s_summary_is_built_from_its_own_calls_and_citation_state() -> None:
    task = TaskSpec.compose(request=REQUEST, goal="Prepare a grounded risk report", context="").render()
    assert request_of(task) == REQUEST
    context = SimpleNamespace(query=task, tool_state={"final_results": [FAQ, RUNBOOK]})
    events = [
        *(_event("tool.intent", i, toolId="knowledgegraph@org/knowledgegraph__search") for i in range(1, 8)),
        _event("tool.intent", 8, toolId="knowledgegraph@org/knowledgegraph__list_files"),
    ]
    grounding = job_grounding(context, events)
    assert (grounding.status, grounding.subject, grounding.searches, grounding.hits) == ("not_found", "SAAS launch", 7, 2)


def test_ac2_the_question_the_job_asked_is_read_from_its_card() -> None:
    card = {"questions": [{"question": "Which launch do you mean?", "options": [{"id": "a", "label": "Orbit GA"}]}]}
    events = [_event("x.pipeshub.question.asked", 3, callId="ask.1", status="success", toolData=card)]
    assert asked_question(events) == {"question": "Which launch do you mean?", "options": ["Orbit GA"]}
    assert asked_question([]) is None


def test_ac2_a_job_s_question_is_asked_with_the_run_s_own_card() -> None:
    asked = TurnResult(
        name="code_task", call_id="c1.0", status="ok",
        content={
            "success": True, "output": "Which launch do you mean?", "needs_input": "…",
            QUESTION_KEY: {"question": "Which launch do you mean?", "options": ["Orbit GA", "SAAS beta"]},
            GROUNDING_KEY: Grounding("not_found", "SAAS launch", 3, 0).to_json(),
        },
    )
    state = _state([(1, 9_000)], tool="code_task")
    plan = _director(ask_tool="internaltools__ask_user_question").before(2, state, [asked])
    assert plan.synthetic is not None
    (call,) = plan.synthetic.tool_calls or []
    assert isinstance(call, ToolCall) and call.name == "internaltools__ask_user_question"
    assert call.arguments["user_intent"] == JOB_ASK_INTENT
    (question,) = call.arguments["questions"]
    assert [o["label"] for o in question["options"]] == ["Orbit GA", "SAAS beta"]
    assert plan.slots[JOB_GROUNDING_SLOT]["status"] == "not_found"
    # A turn that may not ask (no card tool, or its cards used) leaves the job's answer to be presented, as before.
    assert _director().before(2, state, [asked]).synthetic is None


def test_ac2_a_question_with_too_few_options_still_makes_a_card() -> None:
    bare = job_question("Which quarter?", [])
    assert [o["label"] for o in bare["options"]] == ["Type your answer", "Go on with your best judgement"]  # type: ignore[index, union-attr]
    one = job_question("Which quarter?", ["Q3"])
    assert [o["label"] for o in one["options"]] == ["Q3", "Go on with your best judgement"]  # type: ignore[index, union-attr]
    many = job_question("Which?", [f"o{i}" for i in range(7)])
    assert len(many["options"]) == 4  # type: ignore[arg-type]


def test_ac2_every_job_result_records_its_grounding_for_the_answer() -> None:
    answer = TurnResult(
        name="code_task", call_id="c1.0", status="ok",
        content={"success": True, "output": "Report.", GROUNDING_KEY: Grounding("found", "x", 2, 3, 2).to_json()},
    )
    plan = _director().before(2, _state([(1, 9_000)], tool="code_task"), [answer])
    assert plan.slots[JOB_GROUNDING_SLOT]["status"] == "found"


def test_ac2_the_job_s_step_says_what_it_did_not_find() -> None:
    call = ToolCall(id="c1", name="code_task", arguments={})
    body = {"success": True, "output": "x", GROUNDING_KEY: Grounding("not_found", "SAAS launch", 7, 2).to_json()}
    frame = _result_frame(call, body, "x", files=1, workspace=None)
    assert frame.data["resultSummary"] == (
        "The coding engine finished the task and saved 1 file. It found nothing about “SAAS launch” in your knowledge."
    )
    found = {**body, GROUNDING_KEY: Grounding("found", "SAAS launch", 1, 1, 1).to_json()}
    assert _result_frame(call, found, "x", files=0, workspace=None).data["resultSummary"] == (
        "The coding engine finished the task."
    )


# ---- D2/D3: the answer leads with the line and carries the confidence ----


def _saved(seq: int, **slots: Any) -> JournalEvent:  # noqa: ANN401
    return _event("loop.state_saved", seq, slots=slots)


def test_ac3_the_answer_leads_with_the_not_found_line_and_a_low_confidence() -> None:
    goal = {"description": "d", GROUNDING_KEY: Grounding("not_found", "SAAS launch", 1, 2, proceeded=True).to_json()}
    events = [
        _saved(1, **{"pre_run.goal": goal}),
        _saved(5, **{JOB_GROUNDING_SLOT: Grounding("not_found", "SAAS launch", 7, 2).to_json()}),
    ]
    grounding = run_grounding(events)
    assert grounding is not None and (grounding.searches, grounding.proceeded) == (7, True)
    data: dict[str, Any] = {"answer": "SAAS launch — preliminary risk report", "partTexts": ["I'll review…",
                            "SAAS launch — preliminary risk report"], "citations": [{}, {}], "confidence": "Medium"}
    _grounded(data, grounding)
    assert data["answer"].startswith("**I couldn't find anything about “SAAS launch” in your knowledge.**")
    assert data["answer"].endswith("SAAS launch — preliminary risk report")
    assert data["partTexts"] == ["I'll review…", data["answer"]]
    assert data["confidence"] == "Low"


def test_ac3_a_found_subject_keeps_the_answer_and_bounds_the_model_s_rating() -> None:
    data: dict[str, Any] = {"answer": "Report.", "partTexts": ["Report."], "citations": [{}], "confidence": "Very High"}
    _grounded(data, Grounding("found", "SAAS launch", 2, 3, relevant=1))
    assert (data["answer"], data["confidence"]) == ("Report.", "Medium")
    untouched: dict[str, Any] = {"answer": "Hi.", "partTexts": ["Hi."], "citations": [], "confidence": "High"}
    _grounded(untouched, run_grounding([_saved(1, **{"pre_run.goal": {"description": "d"}})]))
    assert untouched == {"answer": "Hi.", "partTexts": ["Hi."], "citations": [], "confidence": "High"}


# ---- D1 for a hand-off the model makes itself ----


async def test_ac1_a_turn_that_may_hand_off_keeps_the_question_from_the_chat_s_own_search_only() -> None:
    gate = GroundingGate(_run(), _context(), _chat(), LOG)
    with patch("app.agents.chat_modes.prefetch.prefetch_retrieval") as probe:
        kept = await gate.check(_prefetch(FAQ, RUNBOOK), delegating=False, may_delegate=True)
        # No search of its own for a turn that stays a chat reply: it pays nothing.
        unprobed = await gate.check(None, delegating=False, may_delegate=True)
    probe.assert_not_called()
    assert kept is not None and kept.pending() is not None
    assert unprobed is not None and unprobed.grounding.status == "unchecked" and unprobed.pending() is None
    # An empty upfront search is a search that found nothing.
    empty = await gate.check(_prefetch(), delegating=False, may_delegate=True)
    assert empty is not None and empty.grounding.not_found and empty.grounding.hits == 0


def test_ac1_the_model_s_own_hand_off_is_refused_until_the_person_is_asked_then_the_director_asks() -> None:
    from app.harness.assembly.code_task import (
        NOT_FOUND_REFUSAL,
        PENDING_QUESTION_KEY,
        CodeTaskDelegation,
    )
    from harness_core.kernel.durable.delegation import Delegated, DelegationView, Refuse

    intent, question = grounding_question(Grounding("not_found", "SAAS launch", 1, 2, near=("product-faq",)))
    pending = {"intent": intent, "question": question}
    slots = {"pre_run.goal": {"description": "d", PENDING_QUESTION_KEY: pending}}
    call = Delegated("i1", ToolCall(id="c1", name="code_task", arguments={"goal": "Prepare the report"}), 1, "pending")
    decided = CodeTaskDelegation(request=REQUEST).decide(call, DelegationView(batch=(call,), slots=slots))
    assert isinstance(decided, Refuse) and decided.content == NOT_FOUND_REFUSAL
    # Without a pending question the hand-off goes ahead.
    assert not isinstance(
        CodeTaskDelegation(request=REQUEST).decide(call, DelegationView(batch=(call,), slots={})), Refuse
    )
    refused = TurnResult(name="code_task", call_id="c1", status="error", content=decided.content)
    state = _state([(1, 9_000)], tool="code_task")
    state = state.model_copy(update={"slots": {**state.slots, "pre_run.goal": slots["pre_run.goal"]}})
    plan = _director(ask_tool="internaltools__ask_user_question").before(2, state, [refused])
    assert plan.synthetic is not None
    (asked,) = plan.synthetic.tool_calls or []
    assert asked.arguments == {"user_intent": intent, "questions": [question]}


def test_ac1_after_the_answer_the_director_hands_off_at_once_without_asking_the_model() -> None:
    from app.harness.assembly.deep_route import (
        GROUNDED_SIGNAL,
        HANDOFF_EVENT,
        HANDOFF_SLOT,
        Handoff,
        RouteDecision,
    )

    route = RouteDecision("engine", "long_report", ("long_output", GROUNDED_SIGNAL))
    plan = _director().before(1, _state([], route=route), [])
    assert plan.harness == () and plan.synthetic is not None
    (call,) = plan.synthetic.tool_calls or []
    assert (call.name, call.id) == ("code_task", "deeproute-1")
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(1, "upfront", "long_report", taken=1)
    assert [(e.type, e.data) for e in plan.events] == [(HANDOFF_EVENT, {"trigger": "upfront", "reason": "long_report"})]


async def test_ac1_an_answer_is_marked_so_the_goal_step_hands_off() -> None:
    _, question = grounding_question(Grounding("not_found", "SAAS launch", 1, 0))
    reply = f'User selections:\n1. "{question["question"]}" → {USE_FOUND_LABEL}'
    checked = await GroundingGate(_run(reply), _context(), _chat(), LOG).check(_prefetch(), delegating=False)
    assert checked is not None and checked.answered and checked.question is None


def test_ac2_the_answer_to_a_job_s_question_is_known_and_goes_back_to_a_job() -> None:
    from app.harness.assembly.grounding_gate import answers_job_question
    from app.harness.grounding import JOB_ASK_INTENT, card_answers
    from harness_core.gateway.asking import question_key

    reply = 'User selections:\n1. "Which launch do you mean?" → Orbit GA'
    assert card_answers(reply) == {question_key("Which launch do you mean?"): "Orbit GA"}
    asked = {
        "role": "bot_response", "content": "",
        "tool_results": [{
            "tool_name": "internaltools__ask_user_question", "status": "success",
            "args": {"user_intent": JOB_ASK_INTENT, "questions": [{"question": "Which launch do you mean?"}]},
        }],
    }
    history = ({"role": "user_query", "content": "check the launch dates"}, asked)
    assert answers_job_question(_run(reply, previous_conversations=history))
    # The model's own question, or a reply to another card, is not one.
    own = {**asked, "tool_results": [{**asked["tool_results"][0], "args": {"user_intent": "I need to know"}}]}
    assert not answers_job_question(_run(reply, previous_conversations=(history[0], own)))
    assert not answers_job_question(_run('User selections:\n1. "Which quarter?" → Q3', previous_conversations=history))
    assert not answers_job_question(_run("check the launch dates", previous_conversations=history))


async def test_ac1_an_agent_run_searches_with_its_own_knowledge_tool_and_leaves_no_trace() -> None:
    """The default chat is the assistant agent: no prefetch. Its own search tool is called once for the subject, within its
    scope; what the search wrote into the citation state is put back, as the model never saw it."""
    from harness_core.tools.base import ToolOutput

    context = _context(final_results=[], records_map={"kept": 1})
    calls: list[dict[str, Any]] = []

    class Search:
        async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
            calls.append(kwargs)
            context.tool_state["final_results"] = [FAQ, RUNBOOK]
            context.tool_state["records_map"]["r1"] = FAQ
            return ToolOutput(success=True, data="…")

    registry = SimpleNamespace(has=lambda name: name == "knowledgegraph__search", resolve_by_name=lambda _: Search())
    gate = GroundingGate(_run(), context, None, LOG, registry=registry, offered=["knowledgegraph__search"])  # type: ignore[arg-type]
    checked = await gate.check(None, delegating=False, may_delegate=True, probe=True)
    assert calls == [{"query": "SAAS launch"}]
    assert checked is not None and checked.grounding.not_found and checked.grounding.hits == 2
    assert checked.question is not None
    assert context.tool_state == {"final_results": [], "records_map": {"kept": 1}}
    # Not granted a search tool, or told not to probe: nothing is searched.
    assert (await GroundingGate(_run(), context, None, LOG, registry=registry).check(None, delegating=True)) is not None
    assert len(calls) == 1


def test_ac2_a_job_that_read_the_web_never_says_not_found_in_the_knowledge() -> None:
    task = TaskSpec.compose(request=REQUEST, goal="", context="").render()
    context = SimpleNamespace(query=task, tool_state={"final_results": [FAQ]})
    events = [
        _event("tool.intent", 1, toolId="knowledgegraph@org/knowledgegraph__search"),
        _event("tool.intent", 2, toolId="dynamic@org/dynamic__web_search"),
    ]
    grounding = job_grounding(context, events)
    assert (grounding.status, grounding.subject, grounding.searches) == ("unchecked", "SAAS launch", 1)
    assert not_found_line(grounding) is None


def test_ac1_research_is_not_asked_about_up_front() -> None:
    from app.harness.assembly.deep_route import classify
    from app.harness.assembly.grounding_gate import RESEARCH_SIGNALS

    assert RESEARCH_SIGNALS & set(classify('Do a deep research on how "Onyx" compares with us and write a report').signals)
    assert not RESEARCH_SIGNALS & set(classify(REQUEST).signals)
