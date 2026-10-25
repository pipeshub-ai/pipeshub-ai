"""Golden test: fastembed's Qdrant/bm25 output must not change.

Stored sparse vectors in the vector DB were produced by this model. If a
fastembed/tokeniser/stemmer upgrade changes the indices or values for the same
text, existing documents and new queries stop matching until a re-index.

The expected vectors in golden/bm25_golden.json were generated on fastembed
0.8.0 (golden/generate_bm25_golden.py) and verified identical on 0.9.1.

The test needs the ``Qdrant/bm25`` model files (a few KB, from the Hugging Face
Hub or an existing FASTEMBED_CACHE_PATH). Where they cannot be loaded the test
skips, unless REQUIRE_BM25_MODEL=1 is set (use that in CI jobs that have
network or a warm cache) in which case it fails instead.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.services.vector_db.sparse_embeddings import SparseEmbedder

_GOLDEN = json.loads((Path(__file__).parent / "golden" / "bm25_golden.json").read_text(encoding="utf-8"))
# Under the 30 s pytest-timeout, so a dead Hub skips (or fails) here, not by timeout.
_LOAD_TIMEOUT_S = 10
_MSG = "BM25 output changed — stored sparse vectors would need a re-index"


_UNAVAILABLE: list[BaseException] = []


@pytest.fixture(scope="module")
def embedder() -> SparseEmbedder:
    return SparseEmbedder()


async def _ready(embedder: SparseEmbedder) -> None:
    # Remember a failed load: each attempt against an unreachable Hub can take a
    # whole test timeout, so the second test must not try again.
    if _UNAVAILABLE:
        err = _UNAVAILABLE[0]
    else:
        try:
            await asyncio.wait_for(embedder._ensure_initialized(), timeout=_LOAD_TIMEOUT_S)
            return
        except BaseException as e:  # model files unavailable (offline runner, cold cache)
            if isinstance(e, KeyboardInterrupt):
                raise
            _UNAVAILABLE.append(e)
            err = e
    if os.environ.get("REQUIRE_BM25_MODEL") == "1":
        raise err
    pytest.skip(f"Qdrant/bm25 model not available: {type(err).__name__}")


def _as_dict(vec) -> dict:
    return {"indices": [int(i) for i in vec.indices], "values": [float(v) for v in vec.values]}


@pytest.mark.asyncio
async def test_document_vectors_match_golden(embedder: SparseEmbedder) -> None:
    await _ready(embedder)
    got = [_as_dict(v) for v in await embedder.embed_documents(_GOLDEN["docs"])]
    for i, (g, want) in enumerate(zip(got, _GOLDEN["doc_vectors"], strict=True)):
        assert g == want, f"{_MSG} (document {i}: {_GOLDEN['docs'][i][:40]!r})"


@pytest.mark.asyncio
async def test_query_vectors_match_golden(embedder: SparseEmbedder) -> None:
    await _ready(embedder)
    for q, want in zip(_GOLDEN["queries"], _GOLDEN["query_vectors"], strict=True):
        assert _as_dict(await embedder.embed_query(q)) == want, f"{_MSG} (query {q!r})"


def test_golden_corpus_shape() -> None:
    assert len(_GOLDEN["docs"]) >= 30 and len(_GOLDEN["queries"]) >= 10
    assert sum(1 for v in _GOLDEN["doc_vectors"] if v["indices"]) >= 25
