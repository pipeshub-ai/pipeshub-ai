"""
Unit tests for the record_escalation kernel (pure: no I/O, no LLM).

Tests pin the behaviours that protect against wrong-triggering:
- analyze_coverage handles the three entry shapes and skips fragments.
- A fully-retrieved record (held == total) is never a candidate.
- A pinpoint query (bit false) produces zero candidates.
- already_fetched records are excluded.
- build_candidates respects relevance ordering.
- render_candidate_table produces the expected format.
- needs_whole_document respects the marker and the regex fallback.
"""

from __future__ import annotations

import pytest

from app.modules.agents.record_escalation.coverage import analyze_coverage
from app.modules.agents.record_escalation.models import FetchPlan, FetchVerdict
from app.modules.agents.record_escalation.policy import (
    build_candidates,
    needs_whole_document,
    policy_text,
)
from app.modules.agents.record_escalation.renderer import render_candidate_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_record(record_id: str, n_blocks: int, has_fragments: bool = False) -> dict:
    """Return a minimal virtual_record dict with n_blocks renderable blocks."""
    blocks = []
    for i in range(n_blocks):
        blocks.append({"index": i, "type": "TEXT", "data": f"block {i}"})
    if has_fragments:
        # Fragment block — should NOT count toward total.
        blocks.append({"index": n_blocks, "type": "TEXT", "data": "frag", "parent_block_index": 0})
    return {
        "id": record_id,
        "record_name": f"Record {record_id[:4]}",
        "block_containers": {"blocks": blocks, "block_groups": []},
        "semantic_metadata": {"topics": ["topic-a"], "summary": "short summary"},
    }


def _make_entry(vrid: str, record_id: str, block_index: int | None = 0) -> dict:
    return {"virtual_record_id": vrid, "block_index": block_index, "content": "text"}


# ---------------------------------------------------------------------------
# analyze_coverage
# ---------------------------------------------------------------------------


class TestAnalyzeCoverage:
    def test_basic_partial(self):
        record_id = "rec-1"
        vrid = "vrid-1"
        record = _make_record(record_id, n_blocks=10)
        vr_map = {vrid: record}
        results = [_make_entry(vrid, record_id, i) for i in range(3)]
        cov = analyze_coverage(results, vr_map)
        assert record_id in cov
        held, total = cov[record_id]
        assert held == 3
        assert total == 10

    def test_all_blocks_retrieved(self):
        record_id = "rec-full"
        vrid = "vrid-full"
        record = _make_record(record_id, n_blocks=4)
        vr_map = {vrid: record}
        results = [_make_entry(vrid, record_id, i) for i in range(4)]
        cov = analyze_coverage(results, vr_map)
        held, total = cov[record_id]
        assert held == total == 4

    def test_fragment_blocks_not_counted_in_total(self):
        record_id = "rec-frag"
        vrid = "vrid-frag"
        record = _make_record(record_id, n_blocks=3, has_fragments=True)
        vr_map = {vrid: record}
        results = [_make_entry(vrid, record_id, i) for i in range(3)]
        cov = analyze_coverage(results, vr_map)
        held, total = cov[record_id]
        # Fragment block at index 3 must not inflate total
        assert total == 3
        assert held == 3

    def test_none_block_index_summary_hit(self):
        """Record-summary hits (block_index=None) still register the record_id."""
        record_id = "rec-sum"
        vrid = "vrid-sum"
        record = _make_record(record_id, n_blocks=5)
        vr_map = {vrid: record}
        results = [_make_entry(vrid, record_id, block_index=None)]
        cov = analyze_coverage(results, vr_map)
        assert record_id in cov
        held, total = cov[record_id]
        assert held == 0  # summary hit contributes no block index
        assert total == 5

    def test_missing_vrid_skipped(self):
        cov = analyze_coverage([{"block_index": 0}], {})
        assert cov == {}

    def test_score_key_not_required(self):
        """Entries without 'score' must not crash."""
        record_id = "rec-noscore"
        vrid = "vrid-noscore"
        record = _make_record(record_id, n_blocks=2)
        vr_map = {vrid: record}
        entry = {"virtual_record_id": vrid, "block_index": 0, "content": "x"}
        assert "score" not in entry
        cov = analyze_coverage([entry], vr_map)
        assert record_id in cov

    def test_group_children_counted_in_total(self):
        """Regression: retrieval flattens a table/list hit with `block_index`
        pointing at a CHILD row, so children must count toward the total too.
        Counting them only in the numerator let `held` exceed `total`, which
        `build_candidates` read as "all blocks already retrieved" and dropped
        — a 30-block spreadsheet the model held 3 rows of produced no
        candidate at all."""
        record_id = "rec-table"
        vrid = "vrid-table"
        blocks: list[dict] = [
            {"index": 0, "type": "TEXT", "data": "intro"},
            {"index": 1, "type": "TABLE", "data": {}},
        ]
        blocks += [
            {"index": i, "type": "TEXT", "data": f"row {i}", "parent_index": 1}
            for i in range(2, 22)
        ]
        record = {
            "id": record_id,
            "record_name": "Financials.xlsx",
            "block_containers": {"blocks": blocks, "block_groups": []},
            "semantic_metadata": {"topics": ["revenue"], "summary": "s"},
        }
        vr_map = {vrid: record}
        # Retrieval matched three table rows — child block indices.
        results = [_make_entry(vrid, record_id, i) for i in (5, 6, 7)]

        held, total = analyze_coverage(results, vr_map)[record_id]
        assert held == 3
        assert total == 22
        assert held < total

        plan = build_candidates(
            coverage={record_id: (held, total)},
            records_in_relevance_order=[record],
            already_fetched_ids=set(),
        )
        assert plan.has_candidates, "table-heavy record must still be offered for fetch"


# ---------------------------------------------------------------------------
# build_candidates
# ---------------------------------------------------------------------------


class TestBuildCandidates:
    def _base_cov(self) -> dict:
        return {
            "rec-a": (3, 10),  # partial
            "rec-b": (5, 5),  # fully retrieved
            "rec-c": (1, 8),  # partial
        }

    def _records(self) -> list[dict]:
        return [
            {"id": "rec-a", "record_name": "Report A", "semantic_metadata": {"topics": ["t1"]}},
            {"id": "rec-b", "record_name": "Report B", "semantic_metadata": {}},
            {"id": "rec-c", "record_name": "Report C", "semantic_metadata": {"topics": ["t2"]}},
        ]

    def test_fully_retrieved_excluded(self):
        plan = build_candidates(
            coverage=self._base_cov(),
            records_in_relevance_order=self._records(),
            already_fetched_ids=set(),
        )
        ids = [c.record_id for c in plan.candidates]
        assert "rec-b" not in ids
        assert "rec-a" in ids and "rec-c" in ids

    def test_already_fetched_excluded(self):
        plan = build_candidates(
            coverage=self._base_cov(),
            records_in_relevance_order=self._records(),
            already_fetched_ids={"rec-a"},
        )
        ids = [c.record_id for c in plan.candidates]
        assert "rec-a" not in ids

    def test_relevance_ordering_preserved(self):
        plan = build_candidates(
            coverage=self._base_cov(),
            records_in_relevance_order=self._records(),
            already_fetched_ids=set(),
        )
        ids = [c.record_id for c in plan.candidates]
        assert ids == ["rec-a", "rec-c"]

    def test_max_candidates_cap(self):
        records = [{"id": f"rec-{i}", "record_name": f"R{i}", "semantic_metadata": {}} for i in range(20)]
        cov = {f"rec-{i}": (1, 5) for i in range(20)}
        plan = build_candidates(
            coverage=cov,
            records_in_relevance_order=records,
            already_fetched_ids=set(),
            max_candidates=5,
        )
        assert len(plan.candidates) == 5

    def test_no_candidates_when_all_fetched(self):
        plan = build_candidates(
            coverage={"rec-a": (3, 10)},
            records_in_relevance_order=[{"id": "rec-a", "record_name": "R", "semantic_metadata": {}}],
            already_fetched_ids={"rec-a"},
        )
        assert not plan.has_candidates

    def test_exclusion_log_populated(self):
        plan = build_candidates(
            coverage=self._base_cov(),
            records_in_relevance_order=self._records(),
            already_fetched_ids=set(),
        )
        excl_ids = {r for r, _ in plan.excluded}
        assert "rec-b" in excl_ids


# ---------------------------------------------------------------------------
# needs_whole_document
# ---------------------------------------------------------------------------


class TestNeedsWholeDocument:
    def test_marker_yes(self):
        assert needs_whole_document("yes") is True

    def test_marker_no(self):
        assert needs_whole_document("no") is False

    def test_marker_case_insensitive(self):
        assert needs_whole_document("YES") is True
        assert needs_whole_document("No") is False

    def test_explicit_marker_wins_over_text(self):
        """An explicit model judgment is a real judgment — the fallback must
        not second-guess it in either direction."""
        assert needs_whole_document("no", "summarize the entire document") is False
        assert needs_whole_document("yes", "who is the CEO?") is True

    def test_marker_none_empty_text(self):
        assert needs_whole_document(None) is False
        assert needs_whole_document(None, "") is False

    # -- Regression: the shapes an earlier scenario-matching version missed. --
    # These are the reported failures: an enumeration keyed on "full/entire +
    # noun" never matched a bare "summarize the document".
    @pytest.mark.parametrize(
        "query",
        [
            "Summarize the document",
            "summarize document",
            "Summarise this",
            "summarize the entire document",
            "Find risks in the document",
            "What are the risks here?",
            "Any red flags in this agreement?",
            "Give me an overview",
            "tldr",
            "Review the contract",
            "Assess this policy",
            "Compare the Q3 vs Q4 report",
            "compare Q3 report with Q4 report",
            "What are the key obligations?",
            "What are the main takeaways?",
            "List all sections",
            "walk me through the deck",
            "read it end-to-end",
            "What are the gaps in this proposal?",
            "any action items in the minutes?",
        ],
    )
    def test_whole_document_shapes_trigger(self, query):
        assert needs_whole_document(None, query) is True, query

    @pytest.mark.parametrize(
        "query",
        [
            "When was the disaster recovery test performed?",
            "Who is the CEO?",
            "What is my vacation balance?",
            "Is the Acme deal closed?",
            "What is the wifi password",
            "When does my subscription renew",
            "what is the SLA response time",
        ],
    )
    def test_pinpoint_lookups_do_not_trigger(self, query):
        """Locatable facts are what retrieval already found — these must stay
        False so the feature leaves the majority of traffic alone."""
        assert needs_whole_document(None, query) is False, query

    def test_aggregative_operation_alone_is_enough(self):
        """Whole-scope operations need no document noun: you cannot summarize
        part of something and call it a summary."""
        assert needs_whole_document(None, "summarize") is True
        assert needs_whole_document(None, "give me a breakdown") is True

    def test_generic_verb_needs_document_scope(self):
        """Generic verbs are not document-scoped on their own."""
        assert needs_whole_document(None, "explain how to reset my password") is False
        assert needs_whole_document(None, "explain this document") is True


# ---------------------------------------------------------------------------
# render_candidate_table
# ---------------------------------------------------------------------------


class TestRenderCandidateTable:
    def _simple_plan(self) -> FetchPlan:
        from app.modules.agents.record_escalation.models import FetchCandidate

        return FetchPlan(
            candidates=(
                FetchCandidate(
                    record_id="abc-123",
                    record_name="Q3 Report",
                    topics=("revenue", "ARR"),
                    summary="Q3 results",
                    blocks_held=4,
                    blocks_total=87,
                ),
            ),
            excluded=(),
        )

    def test_empty_plan_returns_empty(self):
        plan = FetchPlan(candidates=(), excluded=())
        assert render_candidate_table(plan) == ""

    def test_contains_record_id(self):
        out = render_candidate_table(self._simple_plan())
        assert "abc-123" in out

    def test_contains_blocks_held_total(self):
        """The counts are the load-bearing part — without them the model
        cannot tell whether reading further would add anything."""
        out = render_candidate_table(self._simple_plan())
        assert "4 of 87 blocks" in out

    def test_contains_topics(self):
        out = render_candidate_table(self._simple_plan())
        assert "revenue" in out

    def test_contains_coverage_percentage(self):
        """The percentage makes the coverage gap viscerally obvious — 5%
        is far more actionable than '4 of 87'."""
        out = render_candidate_table(self._simple_plan())
        assert "(5%)" in out

    def test_footer_names_the_read_step(self):
        """Low-coverage footer names the fetch tool and drops the old
        'and coverage is low' hedge — incomplete coverage alone is enough
        to cue a fetch when the answer needs more than held blocks."""
        out = render_candidate_table(self._simple_plan())
        assert "call `knowledgegraph__fetch_record` ONCE" in out
        assert "Coverage is incomplete" in out
        assert "and coverage is low" not in out

    def test_custom_tool_ref(self):
        out = render_candidate_table(self._simple_plan(), tool_ref="internal_exploration_agent")
        assert "internal_exploration_agent" in out

    def test_header_is_emphatic_when_bit_set(self):
        """The CTA leads BEFORE the record rows for a whole-document
        request — the model must see the instruction before the data, not
        after it."""
        out = render_candidate_table(self._simple_plan(), needs_whole_document=True)
        assert "this question needs whole-document content" in out
        assert out.index("BEFORE answering") < out.index("Record ID: abc-123")

    def test_whole_document_cta_is_imperative(self):
        out = render_candidate_table(self._simple_plan(), needs_whole_document=True)
        assert "BEFORE answering" in out
        assert "Call `knowledgegraph__fetch_record` ONCE" in out

    def test_whole_document_states_coverage_gap(self):
        """Concrete penalty framing: the model should see WHY skipping the
        fetch is costly, not just that it's instructed to fetch."""
        out = render_candidate_table(self._simple_plan(), needs_whole_document=True)
        assert "(5%)" in out
        assert "will miss content the question requires" in out

    def test_header_low_coverage_when_bit_unset_and_sparse(self):
        """For a 5%-coverage candidate (well below 30%), the incomplete-
        coverage header fires even without the needs_whole_document flag."""
        out = render_candidate_table(self._simple_plan(), needs_whole_document=False)
        assert "this request needs whole-document content" not in out
        assert "incomplete" in out.lower()
        assert "4 of 87 blocks" in out

    def _high_coverage_plan(self) -> "FetchPlan":
        from app.modules.agents.record_escalation.models import FetchCandidate

        return FetchPlan(
            candidates=(
                FetchCandidate(
                    record_id="def-456",
                    record_name="Short Doc",
                    topics=("overview",),
                    summary="brief",
                    blocks_held=35,
                    blocks_total=40,
                ),
            ),
            excluded=(),
        )

    def test_unknown_total_is_never_shown_as_full_coverage(self):
        """Regression: `analyze_coverage` reports total 0 for a record whose
        block structure it could not read. Dividing by that fell back to
        "(100%)", telling the model it already held the whole record — the one
        reading that guarantees no fetch."""
        from app.modules.agents.record_escalation.models import FetchCandidate

        plan = FetchPlan(
            candidates=(
                FetchCandidate(
                    record_id="rec-unknown",
                    record_name="Ticket-123",
                    topics=(),
                    summary="",
                    blocks_held=2,
                    blocks_total=0,
                ),
            ),
            excluded=(),
        )
        out = render_candidate_table(plan)
        assert "(100%)" not in out
        assert "of 0 blocks" not in out
        assert "document length unknown" in out
        # Unknown coverage must land on the incomplete path, not the optional one.
        assert "Coverage is incomplete" in out

    def test_header_is_neutral_when_coverage_above_threshold(self):
        """At 88% coverage (above 30%), the neutral header fires when the
        needs_whole_document flag is unset."""
        out = render_candidate_table(self._high_coverage_plan(), needs_whole_document=False)
        assert "this request needs whole-document content" not in out
        assert "if this request needs more than the blocks above" in out
        assert "BEFORE answering" not in out
        assert "and coverage is low" not in out


# ---------------------------------------------------------------------------
# policy_text
# ---------------------------------------------------------------------------


class TestPolicyText:
    """policy_text() states the two-step decision test (relevance, then
    sufficiency) inline so smaller models do not need to follow a
    cross-reference to the function-calling schema. Fetch-when-incomplete
    leads; skip-fetch is the narrow escape hatch."""

    def test_contains_tool_ref(self):
        text = policy_text("dynamic_fetch_full_record")
        assert "dynamic_fetch_full_record" in text

    def test_names_the_metadata_only_tools(self):
        text = policy_text("dynamic_fetch_full_record")
        assert "lookup_record" in text
        assert "navigate" in text
        assert "list_files" in text

    def test_states_ranked_fragments_framing(self):
        text = policy_text("dynamic_fetch_full_record")
        assert "ranked fragments" in text

    def test_states_one_call_id_batching(self):
        text = policy_text("dynamic_fetch_full_record")
        assert "ONE call" in text

    def test_states_whole_document_scope(self):
        """The policy should describe the whole-document fetch use case."""
        text = policy_text("dynamic_fetch_full_record")
        assert "whole-document" in text

    def test_states_whole_document_examples(self):
        """The inline test should name concrete whole-document scenarios."""
        text = policy_text("dynamic_fetch_full_record")
        assert "summary" in text

    def test_leads_with_fetch_when_incomplete(self):
        """Fetch is step 2's default action; skip is a subordinate 'UNLESS'
        clause inside that same step, not a co-equal numbered step — a
        co-equal step gave the model an escape hatch with equal billing."""
        text = policy_text("dynamic_fetch_full_record")
        assert "call `dynamic_fetch_full_record`" in text
        assert "UNLESS the exact fact needed" in text
        assert "3. Skip the fetch" not in text
        assert "Do NOT call" not in text

    def test_is_reasonable_length(self):
        """Regression guard: policy text is capped to stay below a reasonable ceiling."""
        assert len(policy_text("dynamic_fetch_full_record")) < 2000


class TestPolicyTextCoversTheZeroContentPath:
    """A record reached by lookup/navigate/list_files arrives as an ID plus
    metadata with no blocks, so the passage-is-enough branch ("a status —
    answer now") describes an option the model does not have. That
    zero-content branch is now stated once, in `tool_ref`'s own schema
    (`_FETCH_FULL_RECORD_DESCRIPTION`) — see `test_citations.py` /
    `hooks/citations.py` for the branch-level assertions; this module only
    needs to point at the tools involved."""

    def test_names_the_tools_that_return_no_content(self):
        text = policy_text("dynamic_fetch_full_record")
        assert "lookup_record" in text
        assert "navigate" in text
        assert "list_files" in text
