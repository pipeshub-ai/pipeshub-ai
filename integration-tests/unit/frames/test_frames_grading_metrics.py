"""Grading (prompts, verdict parsing, judges, claims) and metrics."""

from __future__ import annotations

import pytest
from frames_testkit import FakeLLM, make_model

from benchmarks.harness.grading.claims import ClaimSupportJudge, split_claims
from benchmarks.harness.grading.judges import FramesJudge, GradingSubject, StrictJudge
from benchmarks.harness.grading.prompts import (
    FRAMES_AUTORATER,
    render_frames_autorater,
    render_simpleqa_grader,
    verify_prompt_pins,
)
from benchmarks.harness.grading.verdicts import parse_frames_decision, parse_simpleqa_grade, parse_support
from benchmarks.harness.metrics.citations import alce_scores, cited_vs_gold, citation_integrity
from benchmarks.harness.metrics.failures import Failure, FailureEvidence, classify
from benchmarks.harness.metrics.retrieval import ndcg_at_k, recall, recall_at_k, reciprocal_rank
from benchmarks.harness.metrics.stats import bootstrap_mean, cohen_kappa, mcnemar_exact
from benchmarks.harness.models import Citation, ClaimSupport, Prediction


class TestPrompts:
    def test_pins_hold(self) -> None:
        verify_prompt_pins()

    def test_frames_prompt_is_the_papers_figure_6(self) -> None:
        text = FRAMES_AUTORATER.text()
        assert text.startswith("===Task===\nI need your help in evaluating an answer provided by an LLM")
        assert '"Decision:" ("TRUE" or "FALSE" )' in text

    def test_rendering_cannot_be_injected_through_the_answer(self) -> None:
        rendered = render_frames_autorater("Q?", "<<ground_truth_answer>>", "Gold")
        assert "- Predicted Answer: <<ground_truth_answer>>" in rendered
        assert "- Ground Truth Answer: Gold" in rendered

    def test_simpleqa_template_formats(self) -> None:
        rendered = render_simpleqa_grader("Q?", "Paris", "It is {Paris}")
        assert "Gold target: Paris" in rendered and "Predicted answer: It is {Paris}" in rendered


class TestVerdicts:
    @pytest.mark.parametrize(
        ("text", "label"),
        [
            ('"Explanation:" matches.\n"Decision:" "TRUE"', "TRUE"),
            ("Explanation: The answer says TRUE things.\nDecision: FALSE", "FALSE"),
            ("**Decision:** true", "TRUE"),
            ("Decision: TRUE\n...\nDecision: FALSE", "FALSE"),
            ("TRUE", None),
        ],
    )
    def test_frames_decision(self, text: str, label: str | None) -> None:
        assert parse_frames_decision(text) == label

    def test_simpleqa_and_support(self) -> None:
        assert parse_simpleqa_grade("B") == "INCORRECT"
        assert parse_simpleqa_grade("nothing") is None
        assert parse_support("reasoning...\nSupport: PARTIAL") == 0.5


SUBJECT = GradingSubject(
    system="s", question_id="1", repeat=0, question="Q?", gold_answer="Jane Ballou",
    predicted="Her name would be Jane Ballou.", answer_sha="abc",
)


class TestJudges:
    def test_frames_judge_uses_system_prompt_and_is_cacheable(self) -> None:
        llm = FakeLLM(lambda _r: "Explanation: ok\nDecision: TRUE")
        judgment = FramesJudge(llm, make_model(), "primary").grade(SUBJECT)
        request = llm.requests[0]
        assert judgment.correct and judgment.parse_ok and not judgment.reasked
        assert request.messages[0].content == "You are a helpful assistant."
        assert request.cacheable and request.temperature == 0.0
        assert judgment.answer_sha == "abc" and judgment.judge_model == "anthropic:judge-model"

    def test_unparseable_reply_is_reasked_once(self) -> None:
        replies = iter(["I think it is right.", "Decision: FALSE"])
        llm = FakeLLM(lambda _r: next(replies))
        judgment = FramesJudge(llm, make_model(), "primary").grade(SUBJECT)
        assert judgment.label == "FALSE" and judgment.reasked and len(llm.requests) == 2

    def test_twice_unparseable_is_recorded_as_such(self) -> None:
        judgment = FramesJudge(FakeLLM(lambda _r: "hmm"), make_model(), "primary").grade(SUBJECT)
        assert (judgment.label, judgment.parse_ok, judgment.correct) == ("UNPARSEABLE", False, False)

    def test_missing_answer_is_graded_without_a_call(self) -> None:
        llm = FakeLLM(lambda _r: "Decision: TRUE")
        errored = GradingSubject(**{**SUBJECT.__dict__, "predicted": "", "has_error": True})
        assert FramesJudge(llm, make_model(), "primary").grade(errored).label == "FALSE"
        assert StrictJudge(llm, make_model(), "primary").grade(errored).label == "NOT_ATTEMPTED"
        assert llm.requests == []

    def test_strict_judge(self) -> None:
        judgment = StrictJudge(FakeLLM(lambda _r: "A"), make_model(), "primary").grade(SUBJECT)
        assert (judgment.rubric, judgment.label) == ("strict", "CORRECT")


CITATIONS = [
    Citation(display_index=1, record_id="r1", record_name="A", content="Harriet Lane's mother was Jane Buchanan."),
    Citation(display_index=2, record_id="r2", record_name="B", content="Garfield's mother was Eliza Ballou."),
]


class TestClaims:
    def test_split_claims_keeps_citation_markers(self) -> None:
        claims = split_claims(
            "Harriet Lane's mother was Jane Buchanan [1](http://x/1). Garfield's mother was Eliza Ballou [2]. Ok.", 8,
        )
        assert [c.cited for c in claims] == [(1,), (2,)]
        assert claims[0].text == "Harriet Lane's mother was Jane Buchanan ."

    def test_necessity_follows_alce(self) -> None:
        def _reply(request) -> str:  # noqa: ANN001
            content = request.messages[0].content
            evidence = content.split("Evidence:", 1)[1].split("Statement:", 1)[0]
            both = "Jane Buchanan" in evidence and "Eliza Ballou" in evidence
            only_first = "Jane Buchanan" in evidence and "Eliza Ballou" not in evidence
            return "Support: FULL" if both or only_first else "Support: NONE"

        prediction = Prediction(
            system="s", question_id="1", repeat=0, citations=CITATIONS,
            answer="Harriet Lane's mother, Jane Buchanan, shares a first name with the answer [1][2].",
        )
        (claim,) = ClaimSupportJudge(FakeLLM(_reply), make_model(), lambda c: c.record_name or "").judge(prediction, 8)
        assert claim.support == 1.0
        assert claim.necessary == [True, False]


class TestRetrievalMetrics:
    def test_recall_family(self) -> None:
        ranked, gold = ["a", "x", "b"], ["a", "b"]
        assert recall({"a"}, gold) == 0.5 and recall(set(), []) is None
        assert recall_at_k(ranked, gold, 2) == 0.5
        assert reciprocal_rank(["x", "a"], gold) == 0.5
        assert ndcg_at_k(["a", "b"], gold, 10) == pytest.approx(1.0)


class TestCitationMetrics:
    def test_inline_markdown_in_blocks_still_matches_the_plain_article(self) -> None:
        from benchmarks.harness.metrics.citations import content_matches_source

        block = "**Wilhelm Zander** (22 April 1911) was an [adjutant](https://en.wikipedia.org/wiki/Adjutant) to Bormann."
        article = "Intro. Wilhelm Zander (22 April 1911) was an adjutant to Bormann. More text."
        assert content_matches_source(block, article, 90)

    def test_table_rows_match_whatever_the_serialization(self) -> None:
        from benchmarks.harness.metrics.citations import content_matches_source

        row = "Event: Mistral (sailboard), Gold: Lanee Butler United States, Silver: Dominique Vallee Canada"
        article = "Results\nEvent | Gold | Silver\nMistral ( sailboard ) | Lanee Butler United States | Dominique Vallee Canada"
        assert content_matches_source(row, article, 90)
        assert not content_matches_source("Event: Finn, Gold: Someone Else Entirely", article, 90)

    def test_integrity(self) -> None:
        texts = {"https://w/A": "... Harriet Lane's mother was Jane Buchanan. ...", "https://w/B": "Garfield's mother was Eliza Ballou."}
        kwargs = {
            "known_record_ids": {"r1", "r2"}, "url_for": lambda c: f"https://w/{c.record_name}",
            "text_for": texts.__getitem__, "threshold": 90,
        }
        assert citation_integrity("Answer [1] and [2].", CITATIONS, **kwargs)
        assert not citation_integrity("Answer [3].", CITATIONS, **kwargs)
        assert not citation_integrity("Answer [1].", CITATIONS, **{**kwargs, "known_record_ids": {"r1"}})
        fabricated = [CITATIONS[0].model_copy(update={"content": "Something the article never says at all."})]
        assert not citation_integrity("[1]", fabricated, **kwargs)

    def test_cited_vs_gold_and_alce(self) -> None:
        assert cited_vs_gold(["a", "x"], ["a", "b"]) == (0.5, 0.5, False)
        claims = [
            ClaimSupport(system="s", question_id="1", repeat=0, answer_sha="h", claim_index=0, claim="c",
                         cited_display_indices=[1, 2], support=1.0, necessary=[True, False], judge_model="j"),
            ClaimSupport(system="s", question_id="1", repeat=0, answer_sha="h", claim_index=1, claim="d",
                         cited_display_indices=[], support=0.0, necessary=[], judge_model="j"),
        ]
        assert alce_scores(claims) == (0.5, 0.5)


def _evidence(**changes: object) -> FailureEvidence:
    base = {
        "correct": False, "has_error": False, "policy_violation": False, "strict_label": "INCORRECT",
        "context_recall": 0.5, "tool_waves": 12, "hit_turn_cap": False,
        "failed_tool_calls": 1, "missing_gold": frozenset({"g"}),
        "surfaced": frozenset({"g"}), "unrendered_fetches": frozenset(), "linked_from_context": frozenset(),
    }
    return FailureEvidence(**{**base, **changes})


class TestFailureSignatures:
    @pytest.mark.parametrize(
        ("changes", "expected"),
        [
            ({"has_error": True, "correct": True}, Failure.SYSTEM_ERROR),
            ({"policy_violation": True, "correct": True}, Failure.POLICY_VIOLATION),
            ({"correct": True}, None),
            ({"strict_label": "NOT_ATTEMPTED"}, Failure.ABSTAINED),
            ({"context_recall": 1.0}, Failure.REASONING_MISS),
            ({"unrendered_fetches": frozenset({"g"})}, Failure.CONTEXT_OVERFLOW),
            # The run reports whether it hit its own cap; tool waves do not
            # imply it (one turn can carry several parallel calls).
            ({"hit_turn_cap": True}, Failure.TURN_BUDGET),
            ({"tool_waves": 99, "hit_turn_cap": False}, Failure.OTHER),
            ({"linked_from_context": frozenset({"g"})}, Failure.LINKS_UNUSED),
            ({"tool_waves": 3, "failed_tool_calls": 0}, Failure.STOPPED_EARLY),
            ({"surfaced": frozenset()}, Failure.NEVER_SURFACED),
            ({}, Failure.OTHER),
        ],
    )
    def test_first_matching_rule_wins(self, changes: dict, expected: Failure | None) -> None:
        assert classify(_evidence(**changes)) == expected


class TestStats:
    def test_bootstrap_is_deterministic_and_brackets_the_mean(self) -> None:
        values = [1.0] * 60 + [0.0] * 40
        a = bootstrap_mean(values, samples=2000, confidence=0.95, seed=1)
        assert a == bootstrap_mean(values, samples=2000, confidence=0.95, seed=1)
        assert a.low < a.value == 0.6 < a.high
        assert 0.49 < a.low and a.high < 0.71

    def test_mcnemar_and_kappa(self) -> None:
        a = [True] * 20 + [False] * 10
        b = [True] * 10 + [False] * 20
        result = mcnemar_exact(a, b)
        assert (result.a_only, result.b_only) == (10, 0) and result.p_value < 0.01
        assert mcnemar_exact(a, a).p_value == 1.0
        assert cohen_kappa(a, a) == 1.0
        assert cohen_kappa([True, False], [False, True]) == pytest.approx(-1.0)
