"""SPENDCONT: a parent never throws away its cut-short child's work, and its hand-off carries what came before.

A recorded conversation: the person asked for a file of the PP project's security bugs. The loop
called ``code_task`` itself (the request was routed ``chat_sized``); the Codex child stopped at its budget with an
8.3 KB partial inventory (``truncatedBy: budget``). The parent spent its wrap-up on a second ``code_task``, refused
"A code task already ran for this message", then answered "the requested file isn't ready yet": the partial never
reached the person. On "continue" the model wrote a 2.9k-character ``context`` argument, which ``code_task``'s
schema did not have, so the child's task held only the goal and it searched Jira from scratch.
"""

from __future__ import annotations

from app.harness.assembly.code_task import (
    CODE_TASK_DESCRIPTION,
    CONTEXT_FIELD,
    CodeTaskDelegation,
    code_task_schema,
)
from harness_core.engines.outcome.ledger import ArtifactWritten
from harness_core.engines.outcome.outcome import Delivered, Partial
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.journal.children import (
    STOPPED_EARLY_NOTES,
    ChildRequest,
    child_result_document,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.turn_limit import cut_short_fallback
from harness_core.kernel.durable.delegation import Delegated, DelegationView, Refuse
from harness_core.kernel.durable.ports import TurnResult
from tests.unit.harness.assembly.test_deeproute import _director, _state

PARTIAL = PRESENTER.answer(
    Partial("budget", Delivered(ArtifactWritten("out/PP-security-bugs-for-claude-code.md", 2, 900, "final", "wrap_up"))),
    body="# PP security bugs\n\n- PP-110: personal connector falls back to the whole org",
)
PRIOR = "Candidates from the earlier attempt: PP-367 (Nextcloud profile email), PP-368 (SSRF via server URL)."


def _code_task(goal: str = "Finish the PP security file.", **arguments: object) -> Delegated:
    call = ToolCall(id="c1", name="code_task", arguments={"goal": goal, **arguments})
    return Delegated(invocation_id="t1.0", call=call, turn=1, state="pending")


def test_the_hand_off_s_context_reaches_the_child_s_task() -> None:
    decided = CodeTaskDelegation().decide(_code_task(**{CONTEXT_FIELD: PRIOR}), DelegationView(batch=()))
    assert isinstance(decided, ChildRequest)
    assert decided.goal.startswith("Finish the PP security file.") and PRIOR in decided.goal


def test_without_context_the_goal_is_unchanged() -> None:
    decided = CodeTaskDelegation().decide(_code_task(**{CONTEXT_FIELD: "  "}), DelegationView(batch=()))
    assert isinstance(decided, ChildRequest) and decided.goal == "Finish the PP security file."


def test_the_schema_offers_context_and_says_one_task_per_message() -> None:
    properties = code_task_schema(None).input_schema["properties"]
    assert isinstance(properties, dict) and CONTEXT_FIELD in properties
    assert "once per message" in CODE_TASK_DESCRIPTION


def test_a_second_code_task_is_refused_with_what_to_answer() -> None:
    joined = _code_task()
    joined = Delegated(joined.invocation_id, joined.call, 1, "joined")
    second = Delegated("t2.0", ToolCall(id="c2", name="code_task", arguments={"goal": "again"}), 2, "pending")
    refused = CodeTaskDelegation().decide(second, DelegationView(batch=(second,), earlier=(joined,)))
    assert isinstance(refused, Refuse)
    assert isinstance(refused.content, str) and "result in full" in refused.content and "continue" in refused.content


def _result(output: str, truncated_by: str | None = "budget") -> TurnResult:
    content = child_result_document(outcome="answered", output=output, truncated_by=truncated_by)
    return TurnResult(name="code_task", call_id="c2.0", status="ok", content=content)  # type: ignore[arg-type]


def _after_code_task(**director: object):  # noqa: ANN202
    # The model called code_task itself: no hand-off slot (the recorded route was chat_sized).
    return _director(**director), _state([(1, 9_000)], tool="code_task")


def test_a_cut_short_child_s_partial_is_the_answer_flagged() -> None:
    director, state = _after_code_task()
    plan = director.before(2, state, [_result(PARTIAL)])
    assert plan.synthetic is not None and plan.synthetic.content[0].text == PARTIAL  # type: ignore[union-attr]
    assert plan.cut_short == "budget"


def test_without_routing_the_partial_is_still_presented() -> None:
    director, state = _after_code_task(routing=False)
    plan = director.before(2, state, [_result(PARTIAL)])
    assert plan.synthetic is not None and plan.cut_short == "budget"


def test_a_complete_child_answer_stays_the_model_s_to_present() -> None:
    director, state = _after_code_task()
    plan = director.before(2, state, [_result("Done: PP-security-bugs-for-claude-code.md", None)])
    assert plan.synthetic is None and plan.cut_short is None


def test_a_child_that_wrote_nothing_leaves_the_turn_to_the_model() -> None:
    director, state = _after_code_task()
    plan = director.before(2, state, [_result(cut_short_fallback("budget", ("getJiraIssue",)))])
    assert plan.synthetic is None


def test_the_child_s_note_says_to_present_its_partial_work() -> None:
    note = STOPPED_EARLY_NOTES["budget"]
    assert "give it to the person in full" in note and "continue" in note and "Do not discard it" in note
