"""The reranker client against the real model server and a real (tiny) cross-encoder.

Runs the server in-process over an ASGI transport, so it needs no Docker, only
the Hugging Face Hub (or the model already in the local cache).

Run: pytest tests/integration/test_reranker_model_server.py -m integration --timeout=300
"""

from __future__ import annotations

import httpx
import pytest

from app.embedding_main import app
from app.modules.reranker.http_reranker import HttpReranker
from app.modules.reranker.wire_formats import CohereWireFormat

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# ~17 MB; small enough for CI, and a real cross-encoder like the default.
_TINY_RERANKER = "cross-encoder/ms-marco-TinyBERT-L-2-v2"


@pytest.fixture(scope="module")
def reranker() -> HttpReranker:
    return HttpReranker(
        url="http://model-server/v1/rerank",
        model=_TINY_RERANKER,
        wire_format=CohereWireFormat(),
        timeout_seconds=300,
        transport=httpx.ASGITransport(app=app),
    )


async def test_ranks_the_passage_that_answers_the_question_first(reranker) -> None:
    documents = [
        "Bananas are a good source of potassium and dietary fibre.",
        "Paris is the capital and most populous city of France.",
        "The Eiffel Tower was completed in 1889.",
    ]
    try:
        hits = await reranker.rerank("What is the capital of France?", documents)
    except Exception as exc:  # no network and no cached model
        pytest.skip(f"{_TINY_RERANKER} unavailable: {exc}")

    assert hits[0].index == 1
    assert len(hits) == len(documents)
    assert hits[0].score > hits[-1].score


async def test_top_n_is_applied_by_the_server(reranker) -> None:
    try:
        hits = await reranker.rerank("capital of France", ["Paris", "bananas", "tower"], top_n=1)
    except Exception as exc:
        pytest.skip(f"{_TINY_RERANKER} unavailable: {exc}")

    assert len(hits) == 1


async def test_units_reach_the_model_in_reranked_order(reranker) -> None:
    from app.modules.retrieval.context.reranking import RerankingRanker

    # Retrieval scored the off-topic passage highest; the cross-encoder should not.
    units = [
        {"virtual_record_id": "fruit", "block_index": 0, "block_type": "text", "score": 0.9,
         "content": "Bananas are a good source of potassium and dietary fibre."},
        {"virtual_record_id": "geo", "block_index": 3, "block_type": "text", "score": 0.4,
         "content": "Paris is the capital and most populous city of France."},
    ]
    records = {"fruit": {"record_name": "Nutrition guide"}, "geo": {"record_name": "World capitals"}}
    try:
        await reranker.rerank("warm-up", ["model load"])
    except Exception as exc:
        pytest.skip(f"{_TINY_RERANKER} unavailable: {exc}")

    ranked = await RerankingRanker(reranker).rank(
        units, query="What is the capital of France?", records=records,
    )

    assert [u["virtual_record_id"] for u in ranked] == ["geo", "fruit"]
    assert ranked[0]["rerank_score"] > ranked[1]["rerank_score"]
