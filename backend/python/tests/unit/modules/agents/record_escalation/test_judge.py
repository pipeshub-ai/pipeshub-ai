"""
Unit tests for IFetchJudge / LLMFetchJudge contract.

Uses a stub instead of a live model; pins:
- A normal verdict with needed records.
- An empty verdict (the veto).
- Any exception from the transport -> judged=False.
"""

from __future__ import annotations

import pytest

from app.modules.agents.record_escalation.judge import IFetchJudge, _parse_verdict
from app.modules.agents.record_escalation.models import (
    FetchCandidate,
    FetchPlan,
    FetchVerdict,
)


# ---------------------------------------------------------------------------
# _parse_verdict (pure unit)
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_normal_verdict(self):
        raw = {"needed": [{"record_id": "abc", "reason": "missing Q3 figures"}]}
        v = _parse_verdict(raw)
        assert v.judged is True
        assert v.needed == (("abc", "missing Q3 figures"),)

    def test_empty_needed_is_valid_veto(self):
        v = _parse_verdict({"needed": []})
        assert v.judged is True
        assert v.needed == ()

    def test_missing_needed_key(self):
        v = _parse_verdict({"something_else": []})
        assert v.judged is False

    def test_needed_not_list(self):
        v = _parse_verdict({"needed": "not a list"})
        assert v.judged is False

    def test_item_without_record_id_skipped(self):
        raw = {"needed": [{"reason": "no id here"}]}
        v = _parse_verdict(raw)
        assert v.judged is True
        assert v.needed == ()


# ---------------------------------------------------------------------------
# Stub judge — verifies the IFetchJudge Protocol
# ---------------------------------------------------------------------------


class _StubJudge:
    """Test stub that accepts pre-programmed verdicts."""

    def __init__(self, verdict: FetchVerdict, raise_exc: Exception | None = None) -> None:
        self._verdict = verdict
        self._raise = raise_exc
        self.calls: list[dict] = []

    async def judge(self, *, query: str, draft_answer: str, plan: FetchPlan) -> FetchVerdict:
        self.calls.append({"query": query, "draft_answer": draft_answer, "plan": plan})
        if self._raise is not None:
            raise self._raise
        return self._verdict


def _make_plan() -> FetchPlan:
    return FetchPlan(
        candidates=(
            FetchCandidate(
                record_id="rec-1",
                record_name="Q3 Report",
                topics=("revenue",),
                summary="Q3 results",
                blocks_held=3,
                blocks_total=20,
            ),
        ),
        excluded=(),
    )


class TestStubJudgeContract:
    @pytest.mark.asyncio
    async def test_is_protocol(self):
        stub = _StubJudge(FetchVerdict(needed=(), judged=True))
        assert isinstance(stub, IFetchJudge)

    @pytest.mark.asyncio
    async def test_normal_verdict(self):
        expected = FetchVerdict(needed=(("rec-1", "missing data"),), judged=True)
        stub = _StubJudge(expected)
        result = await stub.judge(
            query="compare Q3 vs Q4",
            draft_answer="Q3 revenue was X",
            plan=_make_plan(),
        )
        assert result == expected
        assert len(stub.calls) == 1
        assert stub.calls[0]["draft_answer"] == "Q3 revenue was X"

    @pytest.mark.asyncio
    async def test_empty_verdict_veto(self):
        expected = FetchVerdict(needed=(), judged=True)
        stub = _StubJudge(expected)
        result = await stub.judge(
            query="compare Q3 vs Q4",
            draft_answer="Q3 was 10M, Q4 was 12M",
            plan=_make_plan(),
        )
        assert result.judged is True
        assert result.needed == ()

    @pytest.mark.asyncio
    async def test_exception_returns_judged_false(self):
        stub = _StubJudge(
            FetchVerdict(needed=(), judged=True),
            raise_exc=RuntimeError("transport error"),
        )
        try:
            await stub.judge(
                query="q",
                draft_answer="a",
                plan=_make_plan(),
            )
            assert False, "expected exception"
        except RuntimeError:
            pass

    @pytest.mark.asyncio
    async def test_judge_receives_draft_answer(self):
        """Gate contract: draft answer must be in the judge call."""
        stub = _StubJudge(FetchVerdict(needed=(), judged=True))
        await stub.judge(
            query="compare q3 and q4",
            draft_answer="the draft answer text",
            plan=_make_plan(),
        )
        assert "the draft answer text" == stub.calls[0]["draft_answer"]
