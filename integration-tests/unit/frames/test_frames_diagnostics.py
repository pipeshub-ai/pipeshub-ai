"""Per-question diagnosis of agent answers against same-question controls."""

from __future__ import annotations

from benchmarks.harness.models import Prediction, QuestionScore, StreamTrace, SystemFailure, ToolCallTrace
from benchmarks.harness.report.diagnostics import diagnose, render_diagnostics

GOLD = ["u/a", "u/b"]
INDEX = {"ph": "pipeshub", "naive": "pipeshub", "std": "standard", "oracle": "oracle"}


def _score(system: str, *, correct: bool, missing: list[str], qid: str = "1") -> QuestionScore:
    recall = 1 - len(missing) / len(GOLD)
    return QuestionScore(
        system=system, question_id=qid, repeat=0, split="dev", labels=[], gold_count=2,
        correct=correct, context_recall=recall, all_gold_in_context=not missing, missing_gold=missing,
    )


def _pred(system: str = "ph", *, calls: list[ToolCallTrace] | None = None, error: SystemFailure | None = None) -> Prediction:
    return Prediction(
        system=system, question_id="1", repeat=0, answer="" if error else "x [1]", error=error,
        trace=StreamTrace(conversation_id="c1", tool_calls=calls or []),
    )


def _run(ph: QuestionScore, *others: QuestionScore, pred: Prediction | None = None):  # noqa: ANN202
    return diagnose("ph", [ph, *others], {("ph", "1", 0): pred or _pred()}, INDEX, lambda _q: GOLD)[0]


def test_gold_found_by_a_same_index_baseline_blames_the_agent_retrieval() -> None:
    d = _run(_score("ph", correct=False, missing=["u/b"]), _score("naive", correct=True, missing=[]))
    assert d.category == "retrieval_miss_vs_baseline" and d.found_by == {"u/b": ["naive"]}


def test_gold_found_only_on_the_standard_index_blames_indexing() -> None:
    d = _run(_score("ph", correct=False, missing=["u/b"]), _score("naive", correct=False, missing=["u/b"]), _score("std", correct=True, missing=[]))
    assert d.category == "index_miss"


def test_all_gold_in_context_but_wrong_is_a_reasoning_miss() -> None:
    d = _run(_score("ph", correct=False, missing=[]), _score("oracle", correct=True, missing=[]))
    assert d.category == "reasoning_miss" and d.oracle_correct is True


def test_correct_answers_still_report_loop_faults() -> None:
    calls = [ToolCallTrace(tool_call_id=str(i), name="knowledgegraph__search", args_json='{"q": "x"}') for i in range(2)]
    d = _run(_score("ph", correct=True, missing=[]), pred=_pred(calls=calls))
    assert d.category is None and "repeated_identical_tool_call" in d.faults


def test_errors_and_rendering() -> None:
    d = _run(_score("ph", correct=False, missing=GOLD), pred=_pred(error=SystemFailure(kind="timeout", message="t")))
    assert d.category == "run_error" and "error:timeout" in d.faults
    assert "`run_error`" in render_diagnostics("ph", [d])


def test_backend_log_lines_group_by_template_and_attach_to_questions() -> None:
    from datetime import UTC, datetime, timedelta

    from benchmarks.harness.report.backend_logs import group_issues, windows

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    pred = Prediction(system="ph", question_id="4", repeat=0, started_at=t0, latency_ms=10_000)
    lines = [
        (t0 + timedelta(seconds=2), "x - ERROR - [req:abc] fetch failed for record 1a2b3c4d-0000-0000-0000-000000000000"),
        (t0 + timedelta(seconds=3), "x - ERROR - [req:def] fetch failed for record 99999999-0000-0000-0000-000000000000"),
        (t0 + timedelta(seconds=4), "x - INFO - fine"),
        (t0 + timedelta(minutes=5), "x - WARNING - later, nobody in flight"),
    ]
    issues = group_issues(lines, windows([pred]))
    assert issues[0].count == 2 and issues[0].questions == {("ph", "4", 0)}
    assert [i.count for i in issues] == [2, 1] and issues[1].questions == set()


def test_skills_list_is_a_disclosure_tool_not_a_violation() -> None:
    """It enumerates attached skills — no retrieval, no execution — like the
    other meta-tools (`list_toolsets`, `search_tools`)."""
    from benchmarks.harness.guard import ToolCallGuard

    guard = ToolCallGuard()
    assert guard.violations(["skills_list", "knowledgegraph__search"]) == []
    assert guard.violations(["run_code"]) == ["run_code"]
