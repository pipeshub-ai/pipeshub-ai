"""The judge calibration set and its scoring, without a model.

The live run (``run_judge_calibration.py``) only means something if the labels
are well formed and the agreement arithmetic can fail. These check both with a
scripted judge that answers from the labels, or against them.
"""

from __future__ import annotations

import json

import pytest

from app.connectors.sources.demo.harness.answer_judge import (
    AnswerJudge,
    split_sentences,
)
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
        self._by_sentences = {
            "\n".join(f"[{i}] {t}" for i, t in enumerate(split_sentences(c.answer), start=1)): c for c in cases.cases
        }
        self._fixed = fixed
        self._garble = garble or set()

    def complete(self, system: str, user: str) -> str:
        numbered = user.split("<<<ANSWER\n", 1)[1].rsplit("\nANSWER>>>", 1)[0]
        case = self._by_sentences[numbered]
        if case.id in self._garble:
            return "not json"
        ordered = [c for c in case.claims if c.kind == "must_state"] + [c for c in case.claims if c.kind == "must_not_state"]
        claims = []
        for i, claim in enumerate(ordered, start=1):
            verdict = self._fixed or claim.expect[0]
            claims.append({"id": i, "reasoning": "", "verdict": verdict, "evidence_sentence_ids": [] if verdict == "missing" else [1]})
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


LIVE_SELF_CONTRADICTIONS = [
    "Up to $250, but your manager's approval is required. You can spend up to $250 with no approval.",
    "You can spend up to $250 without approval. Above that your manager must approve it, including the $250 purchase.",
]


def test_answers_that_also_contradict_the_fact_are_labelled_not_supported(cases: CalibrationSet) -> None:
    contradicting = [c for c in cases.cases if c.id.startswith("self-contradiction-")]
    assert len(contradicting) >= 10
    for case in contradicting:
        for claim in case.claims:
            assert claim.kind == "must_state" and not claim.should_pass, case.id
    answers = {c.answer.strip() for c in contradicting}
    assert all(a in answers for a in LIVE_SELF_CONTRADICTIONS)


def test_consistent_answers_next_to_them_are_labelled_supported(cases: CalibrationSet) -> None:
    consistent = [c for c in cases.cases if c.id.startswith("consistent-")]
    assert len(consistent) >= 5
    for case in consistent:
        assert all(claim.expect == ["supported"] and claim.should_pass for claim in case.claims), case.id


def test_the_glued_preamble_case_reaches_the_judge_as_two_sentences(cases: CalibrationSet) -> None:
    case = next(c for c in cases.cases if c.id == "consistent-glued-preamble")
    assert len(split_sentences(case.answer)) == 2


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
        {"id": 1, "verdict": "supported", "evidence_sentence_ids": [1]},
        {"id": 2, "verdict": "missing", "evidence_sentence_ids": []},
        {"id": 3, "verdict": "supported", "evidence_sentence_ids": [1]},
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
