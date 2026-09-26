"""The shared reranker's HTTP shapes, against a fake cross-encoder."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from benchmarks.harness.systems.rag.rerank_server import rerank_response


class _FakeReranker:
    model_name = "fake-cross-encoder"

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        # Longer text, higher logit; negative logits included.
        return [len(t) - 3.0 for t in texts]


def test_jina_shape_orders_by_score_and_keeps_every_score_positive() -> None:
    body = {"model": "x", "query": "q", "documents": ["a", "abcdef", {"text": "abcd"}], "top_n": 3}

    out = rerank_response(_FakeReranker(), "/v1/rerank", body)  # type: ignore[arg-type]

    assert [r["index"] for r in out["results"]] == [1, 2, 0]
    assert all(0 < r["relevance_score"] < 1 for r in out["results"]), "a zero threshold must drop nothing"


def test_jina_shape_honours_top_n() -> None:
    out = rerank_response(_FakeReranker(), "/v1/rerank", {"query": "q", "documents": ["a", "bb", "ccc"], "top_n": 1})  # type: ignore[arg-type]

    assert [r["index"] for r in out["results"]] == [2]


def test_tei_shape() -> None:
    out = rerank_response(_FakeReranker(), "/rerank", {"query": "q", "texts": ["abcdef", "a"]})  # type: ignore[arg-type]

    assert [r["index"] for r in out] == [0, 1]
    assert out[0]["score"] > out[1]["score"]


def test_bad_requests_are_rejected() -> None:
    with pytest.raises(ValueError):
        rerank_response(_FakeReranker(), "/v1/rerank", {"documents": ["a"]})  # type: ignore[arg-type]
    with pytest.raises(LookupError):
        rerank_response(_FakeReranker(), "/elsewhere", {"query": "q"})  # type: ignore[arg-type]
