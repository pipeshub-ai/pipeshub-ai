"""Tests for ``app.modules.agents.record_escalation.renderer``."""
from __future__ import annotations

from app.modules.agents.record_escalation.models import FetchCandidate, FetchPlan
from app.modules.agents.record_escalation.policy import FULL_READ_SKIP
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
    summary: str = "",
) -> FetchCandidate:
    return FetchCandidate(
        record_id=record_id,
        record_name=record_name,
        topics=topics,
        summary=summary,
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

    def test_summary_rendered_when_present(self) -> None:
        plan = _plan(_candidate(summary="This is a contract between Acme and Globex."))
        result = render_candidate_table(plan)
        assert "Summary: This is a contract between Acme and Globex." in result

    def test_summary_omitted_when_empty(self) -> None:
        plan = _plan(_candidate(summary=""))
        result = render_candidate_table(plan)
        assert "Summary:" not in result

    def test_summary_newlines_do_not_break_the_numbered_list(self) -> None:
        plan = _plan(_candidate(summary="Contract.\n\nParties: Acme\n2. Globex"))
        result = render_candidate_table(plan)
        assert "Summary: Contract. Parties: Acme 2. Globex" in result
        assert "\n2. Globex" not in result

    def test_only_the_first_candidates_carry_a_summary(self) -> None:
        """Eight 600-character summaries on every search result cost ~1.2k tokens."""
        plan = _plan(*(
            _candidate(record_id=f"r{i}", record_name=f"Doc {i}", summary=f"summary {i}")
            for i in range(1, 7)
        ))
        result = render_candidate_table(plan)
        assert [f"summary {i}" in result for i in range(1, 7)] == [True] * 3 + [False] * 3


class TestFullReadPolicy:
    """`read_in_full` (enterprise search under THOROUGH_RETRIEVAL) removes the
    "one visible fact is enough" exits; without it the text is unchanged."""

    _LOW = _plan(_candidate(blocks_held=1, blocks_total=10))
    _HIGH = _plan(_candidate(blocks_held=9, blocks_total=10))
    _ONE_BLOCK = "A fact seen in one block still needs the rest of the record when your answer rests on it"

    def test_default_low_coverage_footer_is_unchanged(self) -> None:
        out = render_candidate_table(self._LOW)
        assert out.endswith(
            "call `knowledgegraph__fetch_record` ONCE with every record_id that is left and answer "
            "from what it returns — unless the answer needs only a single specific fact already "
            "visible in the blocks above."
        )
        assert render_candidate_table(self._LOW, read_in_full=False) == out

    def test_default_high_coverage_footer_and_note_are_unchanged(self) -> None:
        high = render_candidate_table(self._HIGH)
        assert "Records you can read in full if this request needs more than the blocks above:" in high
        assert "Is a specific fact you need already visible in the blocks" in high
        assert render_candidate_table(self._HIGH, read_in_full=False) == high
        note = render_coverage_note(self._LOW)
        assert "anything that needs more than the specific passages shown above" in note
        assert render_coverage_note(self._LOW, read_in_full=False) == note

    def test_low_coverage_footer_drops_the_single_fact_exit(self) -> None:
        out = render_candidate_table(self._LOW, read_in_full=True)
        assert "single specific fact" not in out
        assert f"answer from what it returns. {self._ONE_BLOCK}: the rest can revise, qualify or complete it." in out
        assert out.endswith(f"Skip the call only when {FULL_READ_SKIP}.")
        assert "1 of 10 blocks" in out

    def test_high_coverage_footer_asks_whether_the_record_is_held_in_full(self) -> None:
        out = render_candidate_table(self._HIGH, read_in_full=True)
        assert "already visible in the blocks" not in out
        assert f"then answer, unless {FULL_READ_SKIP}." in out
        assert self._ONE_BLOCK in out
        assert "9 of 10 blocks" in out

    def test_coverage_note_asks_for_full_reads(self) -> None:
        note = render_coverage_note(self._LOW, read_in_full=True)
        assert "read in full the records your answer rests on" in note
        assert "specific passages" not in note
        assert render_coverage_note(self._HIGH, read_in_full=True) == ""

    def test_whole_document_path_is_the_same_either_way(self) -> None:
        assert render_candidate_table(self._LOW, needs_whole_document=True, read_in_full=True) == (
            render_candidate_table(self._LOW, needs_whole_document=True)
        )


def test_every_full_read_text_keeps_the_same_exits() -> None:
    """The mode text, the fetch policy, the candidate list and the fetch tool
    each state when a full read may be skipped; under the full-read policy
    they must all give the same exits, metadata included."""
    from unittest.mock import MagicMock

    from app.agents.agent_loop.answer_rules import READ_RECORDS_IN_FULL_STATE_KEY
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.citations import (
        CitationCollector,
        _FetchFullRecordTool,
    )
    from app.agents.chat_modes.internal_search_prompt import (
        internal_search_instructions,
    )
    from app.modules.agents.record_escalation.policy import policy_text

    assert "metadata alone (status, assignee)" in FULL_READ_SKIP
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com", logger=MagicMock())
    context.tool_state[READ_RECORDS_IN_FULL_STATE_KEY] = True
    texts = {
        "mode text": internal_search_instructions(composition=False, thorough=True, entity_tools=False),
        "fetch policy": policy_text("knowledgegraph__fetch_record", read_in_full=True),
        "low-coverage list": render_candidate_table(_plan(_candidate(blocks_held=1)), read_in_full=True),
        "high-coverage list": render_candidate_table(_plan(_candidate(blocks_held=9)), read_in_full=True),
        "fetch tool": _FetchFullRecordTool(CitationCollector(context), context).description,
    }
    for name, text in texts.items():
        assert FULL_READ_SKIP in text, name
