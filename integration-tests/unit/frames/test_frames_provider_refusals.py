"""Content-safety refusals are told apart from transient failures and reported."""

from __future__ import annotations

import pytest

from benchmarks.harness.config import StatsConfig
from benchmarks.harness.models import QuestionScore, SystemFailure
from benchmarks.harness.report.markdown import render_report
from benchmarks.harness.report.summary import summarize


@pytest.mark.parametrize(("message", "refused"), [
    ("litellm.BadRequestError: litellm.ContentPolicyViolationError: The response was filtered", True),
    ("The prompt triggered Azure OpenAI's content management policy", True),
    ("Run failed: content_filter", True),
    ("litellm.APIError: AzureException APIError - The server had an error while processing your request", False),
    ("timed out", False),
])
def test_refusals_are_told_apart_from_transient_failures(message: str, refused: bool) -> None:
    assert SystemFailure(kind="llm", message=message).provider_refusal is refused


def _score(system: str, qid: str, *, refused: bool = False) -> QuestionScore:
    return QuestionScore(
        system=system, question_id=qid, repeat=0, split="dev", labels=(), gold_count=2,
        correct=not refused, error_kind="llm" if refused else None, provider_refusal=refused,
    )


def test_each_systems_refused_questions_are_listed_on_the_board() -> None:
    scores = [
        _score("naive_rag", "10", refused=True), _score("naive_rag", "2", refused=True), _score("naive_rag", "3"),
        _score("pipeshub", "10"), _score("pipeshub", "2"), _score("pipeshub", "3"),
    ]

    summary = summarize("run", scores, [], lambda _q: [], StatsConfig(bootstrap_samples=200), 1)

    board = {s.system: s for s in summary.systems}
    assert board["naive_rag"].provider_refusal_questions == ["2", "10"]
    assert board["pipeshub"].provider_refusal_questions == []
    report = render_report(summary)
    assert "### Provider refusals" in report and "**naive_rag** (2): 2, 10" in report
