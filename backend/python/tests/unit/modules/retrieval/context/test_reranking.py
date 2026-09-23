"""Reranking units: the reranker decides the order, and never breaks search."""

from __future__ import annotations

import asyncio

import pytest

from app.models.blocks import BlockType, GroupType
from app.modules.reranker.interface import IReranker, RerankerError, RerankHit
from app.modules.retrieval.context import reranking
from app.modules.retrieval.context.ranking import RERANK_SCORE_KEY, RelevanceRanker
from app.modules.retrieval.context.reranking import (
    RERANK_MAX_DOCUMENT_CHARS,
    RERANK_MAX_DOCUMENTS,
    RERANK_TOP_N,
    RerankingRanker,
    ranker_for,
    ranking_text,
)

_RECORDS = {"a": {"record_name": "Alpha handbook"}, "b": {"record_name": "Beta notes"}}


def _text(vrid: str, index: int, score: float, content: str = "") -> dict:
    return {
        "virtual_record_id": vrid,
        "block_index": index,
        "block_type": BlockType.TEXT.value,
        "score": score,
        "content": content or f"{vrid}{index}",
    }


def _keys(units: list[dict]) -> list[tuple[str, int]]:
    return [(u["virtual_record_id"], u["block_index"]) for u in units]


class _ScriptedReranker(IReranker):
    """Scores by a fixed text → score table, and records what it was asked."""

    def __init__(
        self,
        scores: dict[str, float] | None = None,
        *,
        hits: list[RerankHit] | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._scores = scores or {}
        self._hits = hits
        self._error = error
        self._delay = delay
        self.calls: list[tuple[str, list[str], int | None]] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    async def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[RerankHit]:
        self.calls.append((query, list(documents), top_n))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        if self._hits is not None:
            return self._hits
        hits = [
            RerankHit(i, score)
            for i, doc in enumerate(documents)
            for text, score in self._scores.items()
            if doc.endswith(text)
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_n] if top_n else hits


async def _rank(reranker: IReranker, units: list[dict], limit: int | None = None) -> list[dict]:
    return await RerankingRanker(reranker).rank(units, query="q", records=_RECORDS, limit=limit)


class TestRerankingRanker:
    async def test_the_reranker_score_alone_decides_the_order(self) -> None:
        units = [_text("a", 0, 0.9), _text("b", 1, 0.5), _text("a", 2, 0.1)]
        reranker = _ScriptedReranker({"a0": 0.2, "b1": 0.3, "a2": 0.8})

        ranked = await _rank(reranker, units)

        assert _keys(ranked) == [("a", 2), ("b", 1), ("a", 0)]
        assert [u[RERANK_SCORE_KEY] for u in ranked] == [0.8, 0.3, 0.2]

    async def test_keeps_the_top_n_or_fewer_when_the_caller_asks(self) -> None:
        units = [_text("a", i, 1 - i / 100) for i in range(30)]
        reranker = _ScriptedReranker({f"a{i}": i / 100 for i in range(30)})

        assert len(await _rank(reranker, units)) == RERANK_TOP_N
        assert len(await _rank(reranker, units, limit=50)) == RERANK_TOP_N
        assert _keys(await _rank(reranker, units, limit=2)) == [("a", 29), ("a", 28)]
        assert reranker.calls[-1][2] == 2

    async def test_sends_only_the_best_retrieval_candidates(self) -> None:
        units = [_text("a", i, i / 1000) for i in range(RERANK_MAX_DOCUMENTS + 10)]
        reranker = _ScriptedReranker({})
        reranker._hits = [RerankHit(0, 1.0)]

        ranked = await _rank(reranker, units)

        _, documents, _ = reranker.calls[0]
        assert len(documents) == RERANK_MAX_DOCUMENTS
        # Highest retrieval score is sent first, so hit 0 is the best retrieved unit.
        assert _keys(ranked) == [("a", RERANK_MAX_DOCUMENTS + 9)]

    @pytest.mark.parametrize("failure", [
        _ScriptedReranker(error=RerankerError("HTTP 500")),
        _ScriptedReranker(error=ValueError("bad response")),
        _ScriptedReranker(hits=[]),
        _ScriptedReranker(hits=[RerankHit(99, 1.0)]),
    ], ids=["reranker-error", "unexpected-error", "no-hits", "only-bad-indices"])
    async def test_falls_back_to_retrieval_order(self, failure) -> None:
        units = [_text("a", 0, 0.1), _text("b", 1, 0.9), _text("a", 2, 0.5)]

        ranked = await _rank(failure, units, limit=2)

        assert _keys(ranked) == [("b", 1), ("a", 2)]
        assert all(RERANK_SCORE_KEY not in u for u in ranked)

    async def test_a_slow_reranker_is_abandoned(self, monkeypatch) -> None:
        monkeypatch.setattr(reranking, "RERANK_TIMEOUT_SECONDS", 0.01)
        units = [_text("a", 0, 0.1), _text("b", 1, 0.9)]

        ranked = await _rank(_ScriptedReranker({"a0": 1.0}, delay=1.0), units)

        assert _keys(ranked) == [("b", 1), ("a", 0)]

    async def test_duplicate_hits_are_counted_once(self) -> None:
        units = [_text("a", 0, 0.1), _text("b", 1, 0.9)]
        # Hit indices refer to the candidates, which are in retrieval-score order.
        reranker = _ScriptedReranker(hits=[RerankHit(1, 0.9), RerankHit(1, 0.9), RerankHit(0, 0.2)])

        assert _keys(await _rank(reranker, units)) == [("a", 0), ("b", 1)]

    async def test_no_units_makes_no_call(self) -> None:
        reranker = _ScriptedReranker({})
        assert await _rank(reranker, []) == []
        assert reranker.calls == []


class TestRankingText:
    def test_leads_with_the_record_name(self) -> None:
        assert ranking_text(_text("a", 0, 0.5, "Refunds take 5 days."), _RECORDS) == (
            "Alpha handbook\nRefunds take 5 days."
        )

    def test_a_table_reads_as_its_summary_and_rows(self) -> None:
        rows = [
            {"block_type": BlockType.TABLE_ROW.value, "content": "Q1 revenue: 10"},
            {"block_type": BlockType.TABLE_ROW.value, "content": "Q2 revenue: 12"},
        ]
        table = {"virtual_record_id": "b", "block_type": GroupType.TABLE.value, "content": ("Revenue by quarter", rows)}

        assert ranking_text(table, _RECORDS) == "Beta notes\nRevenue by quarter\nQ1 revenue: 10\nQ2 revenue: 12"

    def test_an_image_contributes_its_description_never_its_bytes(self) -> None:
        described = {"virtual_record_id": "a", "block_type": BlockType.IMAGE.value, "content": "A bar chart"}
        assert ranking_text(described, _RECORDS) == "Alpha handbook\nA bar chart"

        image = {
            "virtual_record_id": "a",
            "block_type": BlockType.IMAGE.value,
            "content": "data:image/png;base64," + "iVBORw0KGgo" * 50,
        }
        assert ranking_text(image, _RECORDS) == "Alpha handbook"

    def test_is_truncated_to_what_a_reranker_reads(self) -> None:
        unit = _text("a", 0, 0.5, "x" * (RERANK_MAX_DOCUMENT_CHARS * 2))
        assert len(ranking_text(unit, _RECORDS)) == RERANK_MAX_DOCUMENT_CHARS

    def test_code_carries_its_qualified_name(self) -> None:
        unit = {**_text("a", 0, 0.5, "return x"), "qualified_name": "billing.refund"}
        assert ranking_text(unit, {}) == "billing.refund\nreturn x"


def test_no_reranker_means_retrieval_order() -> None:
    assert type(ranker_for(None)) is RelevanceRanker
    assert isinstance(ranker_for(_ScriptedReranker()), RerankingRanker)
