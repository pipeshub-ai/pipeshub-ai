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


@pytest.fixture
def server_url():  # noqa: ANN201
    import threading
    from http.server import ThreadingHTTPServer

    from benchmarks.harness.systems.rag.rerank_server import make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_FakeReranker()))  # type: ignore[arg-type]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_remote_reranker_orders_chunks_as_the_served_model_scores_them(server_url: str) -> None:
    from benchmarks.harness.systems.rag.rerank import RemoteReranker
    from benchmarks.harness.systems.rag.retrieval import Chunk

    chunks = [Chunk("r", i, text, 0.0) for i, text in enumerate(["a", "abcdef", "abcd"])]

    ranked = RemoteReranker(server_url, "fake-cross-encoder").rerank("q", chunks, top_k=2)

    assert [c.block_index for c in ranked] == [1, 2]


def test_a_server_serving_another_model_is_refused(server_url: str) -> None:
    from benchmarks.harness.systems.rag.rerank import RemoteReranker

    with pytest.raises(ValueError, match="serves 'fake-cross-encoder'"):
        RemoteReranker(server_url, "BAAI/bge-reranker-v2-m3")
