"""`ask_act_scorer.score_decision` — pure, offline scoring of an
`IntentRouteDecision` against an `AskActEvalQuery`. No model calls; every
fixture here is a hand-built `IntentRouteDecision`."""

from __future__ import annotations

from app.agents.actions.internal_tools.intrim_tools import (
    AskUserQuestionItemInput,
    AskUserQuestionOptionInput,
)
from app.agents.agent_loop.evals.ask_act_queries import AskActEvalQuery
from app.agents.agent_loop.evals.ask_act_scorer import AskActEvalReport, score_decision
from app.agents.agent_loop.intent import IntentRouteDecision


def _query(**overrides: object) -> AskActEvalQuery:
    defaults: dict[str, object] = dict(id="q1", query="do the thing", expect_ask=True)
    defaults.update(overrides)
    return AskActEvalQuery(**defaults)  # type: ignore[arg-type]


def _clarifying_question() -> AskUserQuestionItemInput:
    return AskUserQuestionItemInput(
        question="Which one?",
        options=[
            AskUserQuestionOptionInput(label="A"),
            AskUserQuestionOptionInput(label="B"),
            AskUserQuestionOptionInput(label="C"),
        ],
    )


def _decision(*, asked: bool, severity: str | None = None) -> IntentRouteDecision:
    return IntentRouteDecision(
        rewritten_query="x",
        clarifying_questions=[_clarifying_question()] if asked else [],
        clarification_severity=severity,
    )


class TestScoreDecision:
    def test_expected_ask_and_model_asked_passes(self) -> None:
        score = score_decision(_decision(asked=True, severity="moderate"), _query(expect_ask=True))
        assert score.passed is True
        assert score.asked is True
        assert score.severity == "moderate"

    def test_expected_ask_but_model_acted_fails(self) -> None:
        score = score_decision(_decision(asked=False), _query(expect_ask=True))
        assert score.passed is False
        assert "expected the model to ask" in score.message

    def test_expected_act_and_model_acted_passes(self) -> None:
        score = score_decision(_decision(asked=False), _query(expect_ask=False))
        assert score.passed is True
        assert score.asked is False

    def test_expected_act_but_model_asked_fails(self) -> None:
        score = score_decision(_decision(asked=True, severity="fatal"), _query(expect_ask=False))
        assert score.passed is False
        assert "expected the model to act" in score.message

    def test_query_id_carried_through(self) -> None:
        score = score_decision(_decision(asked=True), _query(id="my_query_id", expect_ask=True))
        assert score.query_id == "my_query_id"


class TestAskActEvalReport:
    def test_aggregates_pass_and_fail(self) -> None:
        report = AskActEvalReport(scores=[
            score_decision(_decision(asked=True), _query(id="a", expect_ask=True)),
            score_decision(_decision(asked=False), _query(id="b", expect_ask=True)),
        ])
        assert report.pass_count == 1
        assert report.fail_count == 1
        assert report.pass_rate == 0.5

    def test_empty_scores_has_zero_pass_rate_not_a_crash(self) -> None:
        report = AskActEvalReport(scores=[])
        assert report.pass_rate == 0.0

    def test_render_text_includes_every_query_and_status(self) -> None:
        report = AskActEvalReport(scores=[
            score_decision(_decision(asked=True), _query(id="q1", expect_ask=True)),
            score_decision(_decision(asked=True), _query(id="q2", expect_ask=False)),
        ])
        text = report.render_text()
        assert "q1" in text
        assert "q2" in text
        assert "PASS" in text
        assert "FAIL" in text
