"""The judge calibration set and its scoring, without a model.

The live run (``run_judge_calibration.py``) only means something if the labels
are well formed and the agreement arithmetic can fail. These check both with a
scripted judge that answers from the labels, or against them.
"""

from __future__ import annotations

import json

import pytest

from app.connectors.sources.demo.harness.answer_judge import AnswerJudge
from tests.evals.run_judge_calibration import (
    DEFAULT_THRESHOLD,
    CalibrationCase,
    CalibrationClaim,
    CalibrationSet,
    calibrate,
    load_cases,
    render_summary,
)


class LabelClient:
    """Replies with each claim's first labelled verdict, or always with ``fixed``."""

    def __init__(self, cases: CalibrationSet, fixed: str | None = None, garble: set[str] | None = None) -> None:
        self._by_answer = {c.answer: c for c in cases.cases}
        self._fixed = fixed
        self._garble = garble or set()

    def complete(self, system: str, user: str) -> str:
        answer = user.split("<<<ANSWER\n", 1)[1].rsplit("\nANSWER>>>", 1)[0]
        case = self._by_answer[answer]
        if case.id in self._garble:
            return "not json"
        ordered = [c for c in case.claims if c.kind == "must_state"] + [c for c in case.claims if c.kind == "must_not_state"]
        claims = []
        for i, claim in enumerate(ordered, start=1):
            verdict = self._fixed or claim.expect[0]
            claims.append({"id": i, "reasoning": "", "verdict": verdict, "quote": "" if verdict == "missing" else " ".join(answer.split()[:3])})
        return json.dumps({"claims": claims})


@pytest.fixture(scope="module")
def cases() -> CalibrationSet:
    return load_cases()


def test_the_set_is_big_enough_to_mean_something(cases: CalibrationSet) -> None:
    assert len(cases.cases) >= 40
    kinds = {c.kind for case in cases.cases for c in case.claims}
    assert kinds == {"must_state", "must_not_state"}
    # Both sides of the line: a set of only failing answers can't catch a judge that fails everything.
    outcomes = {c.should_pass for case in cases.cases for c in case.claims}
    assert outcomes == {True, False}


def test_a_judge_that_matches_the_labels_agrees_fully(cases: CalibrationSet) -> None:
    report = calibrate(AnswerJudge(LabelClient(cases)), cases)
    assert report.agreement == 1.0 and report.passed and not report.judge_errors


@pytest.mark.parametrize("fixed", ["supported", "missing"])
def test_a_judge_that_always_says_the_same_thing_fails(cases: CalibrationSet, fixed: str) -> None:
    report = calibrate(AnswerJudge(LabelClient(cases, fixed=fixed)), cases)
    assert report.agreement < DEFAULT_THRESHOLD and not report.passed
    assert "FAILED" in render_summary(report, "cost")


def test_a_judge_error_counts_against_agreement(cases: CalibrationSet) -> None:
    garbled = {c.id for c in cases.cases[:3]}
    report = calibrate(AnswerJudge(LabelClient(cases, garble=garbled)), cases)
    assert len(report.judge_errors) == 3
    wrong = {c.case_id for c in report.claims if not c.agrees}
    assert wrong == garbled


@pytest.mark.parametrize("claim", [
    {"state": "x", "not_state": "y", "expect": "supported"},
    {"expect": "supported"},
    {"state": " ", "expect": "missing"},
    {"state": "x", "expect": ["supported", "missing"]},
    {"state": "x", "expect": "probably"},
    {"state": "x", "expect": []},
])
def test_a_badly_labelled_claim_is_rejected(claim: dict) -> None:
    with pytest.raises(ValueError):
        CalibrationClaim.model_validate(claim)


def test_a_repeated_claim_keeps_its_own_label() -> None:
    same = "A purchase of up to and including $250 needs no approval."
    case = CalibrationCase.model_validate({
        "id": "repeated",
        "answer": "Purchases up to $250 need no approval.",
        "claims": [
            {"state": same, "expect": "supported"},
            {"not_state": same, "expect": "supported"},
            {"state": same, "expect": "missing"},
        ],
    })
    # The judge reports must-state claims first: supported, missing, then the must-not-state one.
    reply = json.dumps({"claims": [
        {"id": 1, "verdict": "supported", "quote": "up to $250"},
        {"id": 2, "verdict": "missing", "quote": ""},
        {"id": 3, "verdict": "supported", "quote": "up to $250"},
    ]})

    class OneReply:
        def complete(self, system: str, user: str) -> str:
            return reply

    report = calibrate(AnswerJudge(OneReply()), CalibrationSet(cases=[case]))
    assert [(c.kind, c.verdict, c.agrees) for c in report.claims] == [
        ("must_state", "supported", True),
        ("must_state", "missing", True),
        ("must_not_state", "supported", True),
    ]
