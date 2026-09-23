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
