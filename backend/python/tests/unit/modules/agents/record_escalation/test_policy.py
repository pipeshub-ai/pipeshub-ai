"""Tests for ``app.modules.agents.record_escalation.policy``."""
from __future__ import annotations

import pytest

from app.modules.agents.record_escalation.models import FetchCandidate, FetchPlan
from app.modules.agents.record_escalation.policy import (
    build_candidates,
    needs_whole_document,
    policy_text,
)


class TestNeedsWholeDocument:
    def test_marker_yes(self) -> None:
        assert needs_whole_document("yes") is True

    def test_marker_no(self) -> None:
        assert needs_whole_document("no") is False

    def test_marker_case_insensitive(self) -> None:
        assert needs_whole_document("YES") is True
        assert needs_whole_document("  Yes  ") is True

    def test_marker_arbitrary_string(self) -> None:
        assert needs_whole_document("maybe") is False

    def test_no_marker_empty_text(self) -> None:
        assert needs_whole_document(None) is False
        assert needs_whole_document(None, "") is False
        assert needs_whole_document(None, "   ") is False

    def test_no_marker_unrelated_text(self) -> None:
        assert needs_whole_document(None, "hello world") is False

    @pytest.mark.parametrize("text", [
        "summarize this document",
        "give me a summary",
        "provide an overview",
        "can you recap what it says",
        "tl;dr of the report",
        "walk me through the contract",
        "review this agreement",
        "audit the policy",
        "compare these two reports",
        "deep dive into the spec",
    ])
    def test_aggregative_operation(self, text: str) -> None:
        assert needs_whole_document(None, text) is True

    @pytest.mark.parametrize("text", [
        "what are the risks",
        "list the key points",
        "find the obligations",
        "what are the deadlines",
        "any red flags",
        "what are the action items",
        "pros and cons of the approach",
        "conclusions from the analysis",
        "recommendations in this report",
    ])
    def test_aggregative_object(self, text: str) -> None:
        assert needs_whole_document(None, text) is True

    def test_exhaustive_scope_with_document_noun(self) -> None:
        assert needs_whole_document(None, "read every page of the document") is True
        assert needs_whole_document(None, "go through all sections") is True
        assert needs_whole_document(None, "the entire report in detail") is True

    def test_weak_operation_with_document_noun(self) -> None:
        assert needs_whole_document(None, "explain this document") is True
        assert needs_whole_document(None, "describe the content") is True

    def test_weak_operation_with_exhaustive_scope(self) -> None:
        assert needs_whole_document(None, "list all of them thoroughly") is True

    def test_weak_operation_alone(self) -> None:
        assert needs_whole_document(None, "explain how to reset my password") is False

    def test_exhaustive_scope_without_document_noun(self) -> None:
        assert needs_whole_document(None, "check every server") is False

    def test_multiple_text_args(self) -> None:
        assert needs_whole_document(None, "can you", "summarize this") is True


class TestBuildCandidates:
    def _make_record(self, record_id: str, **extras) -> dict:
        return {"id": record_id, **extras}

    def test_empty_coverage(self) -> None:
        plan = build_candidates(
            coverage={},
            records_in_relevance_order=[self._make_record("r1")],
            already_fetched_ids=set(),
        )
        assert not plan.has_candidates
        assert ("r1", "not in coverage") in plan.excluded

    def test_already_fetched_excluded(self) -> None:
        plan = build_candidates(
            coverage={"r1": (2, 10)},
            records_in_relevance_order=[self._make_record("r1")],
            already_fetched_ids={"r1"},
        )
        assert not plan.has_candidates
        assert ("r1", "already fetched") in plan.excluded

    def test_full_coverage_excluded(self) -> None:
        plan = build_candidates(
            coverage={"r1": (10, 10)},
            records_in_relevance_order=[self._make_record("r1")],
            already_fetched_ids=set(),
        )
        assert not plan.has_candidates
        assert ("r1", "all blocks already retrieved") in plan.excluded

    def test_partial_coverage_becomes_candidate(self) -> None:
        plan = build_candidates(
            coverage={"r1": (3, 10)},
            records_in_relevance_order=[self._make_record("r1", record_name="Doc A")],
            already_fetched_ids=set(),
        )
        assert plan.has_candidates
        assert len(plan.candidates) == 1
        assert plan.candidates[0].record_id == "r1"
        assert plan.candidates[0].record_name == "Doc A"
        assert plan.candidates[0].blocks_held == 3
        assert plan.candidates[0].blocks_total == 10

    def test_zero_total_becomes_candidate(self) -> None:
        plan = build_candidates(
            coverage={"r1": (2, 0)},
            records_in_relevance_order=[self._make_record("r1")],
            already_fetched_ids=set(),
        )
        assert plan.has_candidates

    def test_max_candidates_enforced(self) -> None:
        records = [self._make_record(f"r{i}") for i in range(20)]
        coverage = {f"r{i}": (1, 10) for i in range(20)}
        plan = build_candidates(
            coverage=coverage,
            records_in_relevance_order=records,
            already_fetched_ids=set(),
            max_candidates=3,
        )
        assert len(plan.candidates) == 3

    def test_duplicate_record_ids_only_first(self) -> None:
        plan = build_candidates(
            coverage={"r1": (1, 5)},
            records_in_relevance_order=[self._make_record("r1"), self._make_record("r1")],
            already_fetched_ids=set(),
        )
        assert len(plan.candidates) == 1

    def test_record_without_id_skipped(self) -> None:
        plan = build_candidates(
            coverage={"r1": (1, 5)},
            records_in_relevance_order=[{"name": "no-id"}, self._make_record("r1")],
            already_fetched_ids=set(),
        )
        assert len(plan.candidates) == 1
        assert plan.candidates[0].record_id == "r1"

    def test_semantic_metadata_captured(self) -> None:
        plan = build_candidates(
            coverage={"r1": (1, 10)},
            records_in_relevance_order=[
                self._make_record("r1", semantic_metadata={
                    "topics": ["engineering", "design"],
                    "summary": "A brief summary.",
                }),
            ],
            already_fetched_ids=set(),
        )
        c = plan.candidates[0]
        assert c.topics == ("engineering", "design")
        assert c.summary == "A brief summary."

    def test_recordName_fallback(self) -> None:
        plan = build_candidates(
            coverage={"r1": (1, 10)},
            records_in_relevance_order=[self._make_record("r1", recordName="Alt Name")],
            already_fetched_ids=set(),
        )
        assert plan.candidates[0].record_name == "Alt Name"

    def test_ordering_follows_input(self) -> None:
        plan = build_candidates(
            coverage={"r1": (1, 10), "r2": (2, 10), "r3": (3, 10)},
            records_in_relevance_order=[
                self._make_record("r3"),
                self._make_record("r1"),
                self._make_record("r2"),
            ],
            already_fetched_ids=set(),
        )
        assert [c.record_id for c in plan.candidates] == ["r3", "r1", "r2"]


class TestPolicyText:
    def test_contains_tool_ref(self) -> None:
        text = policy_text("knowledgegraph__fetch_record")
        assert "knowledgegraph__fetch_record" in text

    def test_contains_section_header(self) -> None:
        text = policy_text("my_tool")
        assert "## Reading Records in Full" in text

    def test_custom_tool_ref(self) -> None:
        text = policy_text("custom_fetch_tool")
        assert "custom_fetch_tool" in text
        assert "knowledgegraph__fetch_record" not in text
