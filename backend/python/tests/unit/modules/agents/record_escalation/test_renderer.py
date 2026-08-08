"""Tests for ``app.modules.agents.record_escalation.renderer``."""
from __future__ import annotations

from app.modules.agents.record_escalation.models import FetchCandidate, FetchPlan
from app.modules.agents.record_escalation.renderer import (
    _coverage_pct,
    _min_coverage_pct,
    render_candidate_table,
    render_coverage_note,
)

_EMPTY_PLAN = FetchPlan(candidates=(), excluded=())


def _candidate(
    record_id: str = "r1",
    record_name: str = "Doc A",
    blocks_held: int = 2,
    blocks_total: int = 10,
    topics: tuple[str, ...] = ("topic1",),
) -> FetchCandidate:
    return FetchCandidate(
        record_id=record_id,
        record_name=record_name,
        topics=topics,
        summary="",
        blocks_held=blocks_held,
        blocks_total=blocks_total,
    )


def _plan(*candidates: FetchCandidate) -> FetchPlan:
    return FetchPlan(candidates=candidates, excluded=())


class TestCoveragePct:
    def test_zero_total(self) -> None:
        assert _coverage_pct(5, 0) is None

    def test_negative_total(self) -> None:
        assert _coverage_pct(5, -1) is None

    def test_normal(self) -> None:
        assert _coverage_pct(25, 100) == 25

    def test_full_coverage(self) -> None:
        assert _coverage_pct(10, 10) == 100

    def test_zero_held(self) -> None:
        assert _coverage_pct(0, 50) == 0

    def test_rounding(self) -> None:
        assert _coverage_pct(1, 3) == 33


class TestMinCoveragePct:
    def test_single_candidate(self) -> None:
        plan = _plan(_candidate(blocks_held=5, blocks_total=10))
        assert _min_coverage_pct(plan) == 50

    def test_picks_lowest(self) -> None:
        plan = _plan(
            _candidate(record_id="r1", blocks_held=5, blocks_total=10),
            _candidate(record_id="r2", blocks_held=1, blocks_total=10),
        )
        assert _min_coverage_pct(plan) == 10

    def test_unknown_total_treated_as_zero(self) -> None:
        plan = _plan(_candidate(blocks_held=5, blocks_total=0))
        assert _min_coverage_pct(plan) == 0


class TestRenderCoverageNote:
    def test_empty_plan(self) -> None:
        assert render_coverage_note(_EMPTY_PLAN) == ""

    def test_high_coverage_no_whole_doc(self) -> None:
        plan = _plan(_candidate(blocks_held=8, blocks_total=10))
        assert render_coverage_note(plan, needs_whole_document=False) == ""

    def test_low_coverage(self) -> None:
        plan = _plan(_candidate(blocks_held=1, blocks_total=10))
        note = render_coverage_note(plan)
        assert "10%" in note
        assert "incomplete" in note

    def test_whole_doc_overrides_high_coverage(self) -> None:
        plan = _plan(_candidate(blocks_held=8, blocks_total=10))
        note = render_coverage_note(plan, needs_whole_document=True)
        assert note != ""
        assert "incomplete" in note

    def test_unknown_total_treated_as_low(self) -> None:
        plan = _plan(_candidate(blocks_held=3, blocks_total=0))
        note = render_coverage_note(plan)
        assert "0%" in note


class TestRenderCandidateTable:
    def test_empty_plan(self) -> None:
        assert render_candidate_table(_EMPTY_PLAN) == ""

    def test_needs_whole_document_cta_before_rows(self) -> None:
        plan = _plan(_candidate(blocks_held=1, blocks_total=10))
        result = render_candidate_table(plan, needs_whole_document=True)
        assert "BEFORE answering" in result
        assert "whole-document content" in result
        assert "r1" in result
        assert "Doc A" in result

    def test_low_coverage_path(self) -> None:
        plan = _plan(_candidate(blocks_held=1, blocks_total=10))
        result = render_candidate_table(plan, needs_whole_document=False)
        assert "Coverage is incomplete" in result
        assert "10%" in result
        assert "r1" in result

    def test_high_coverage_path(self) -> None:
        plan = _plan(_candidate(blocks_held=8, blocks_total=10))
        result = render_candidate_table(plan, needs_whole_document=False)
        assert "Records you can read in full" in result
        assert "is it relevant" in result.lower()

    def test_custom_tool_ref(self) -> None:
        plan = _plan(_candidate(blocks_held=1, blocks_total=10))
        result = render_candidate_table(plan, tool_ref="my_fetch_tool", needs_whole_document=True)
        assert "my_fetch_tool" in result

    def test_unknown_total_shows_unknown(self) -> None:
        plan = _plan(_candidate(blocks_held=2, blocks_total=0))
        result = render_candidate_table(plan)
        assert "document length unknown" in result

    def test_multiple_candidates_rendered(self) -> None:
        plan = _plan(
            _candidate(record_id="r1", record_name="Doc A", blocks_held=1, blocks_total=10, topics=("finance",)),
            _candidate(record_id="r2", record_name="Doc B", blocks_held=3, blocks_total=20, topics=()),
        )
        result = render_candidate_table(plan)
        assert "1. Record ID: r1" in result
        assert "2. Record ID: r2" in result
        assert "Doc A" in result
        assert "Doc B" in result
        assert "finance" in result

    def test_unnamed_record(self) -> None:
        plan = _plan(_candidate(record_name=""))
        result = render_candidate_table(plan)
        assert "(unnamed)" in result

    def test_no_topics_shows_dash(self) -> None:
        plan = _plan(_candidate(topics=()))
        result = render_candidate_table(plan)
        assert "Topics: —" in result

    def test_known_pct_in_output(self) -> None:
        plan = _plan(_candidate(blocks_held=3, blocks_total=10))
        result = render_candidate_table(plan)
        assert "you have 3 of 10 blocks (30%)" in result
