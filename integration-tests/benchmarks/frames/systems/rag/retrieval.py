"""Chunk retrieval over the exact Qdrant points PipesHub indexed.

The hybrid query mirrors PipesHub's own (`qdrant/utils.py::search_request_to_qdrant`):
dense + `Qdrant/bm25` sparse prefetches at `limit * 2` each, fused server-side
with RRF, filtered to the benchmark KB's virtual record ids. Dense-only and
sparse-only are the same query with one prefetch. Sentence-level points
(`isBlock=False`) are folded into their parent block so a block never appears
twice in the context.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from benchmarks.frames.llm.client import LLMClient, ResolvedModel

logger = logging.getLogger(__name__)

RetrievalMode = Literal["dense", "sparse", "hybrid"]
SPARSE_MODEL = "Qdrant/bm25"
_VRID_KEY = "metadata.virtualRecordId"


@dataclass(frozen=True)
class Chunk:
    virtual_record_id: str
    block_index: int | None
    text: str
    score: float

    @property
    def key(self) -> tuple[str, int | None]:
        return (self.virtual_record_id, self.block_index)


class QueryEncoder:
    """Dense vectors from the embedding model PipesHub indexed with; sparse
    vectors from the same fastembed BM25 model and `query_embed` call."""

    def __init__(self, llm: LLMClient, embedding: ResolvedModel) -> None:
        self._llm = llm
        self._embedding = embedding
        self._sparse: Any = None
        self._lock = threading.Lock()

    def dense(self, query: str) -> list[float]:
        return self._llm.embed(self._embedding, [query]).vectors[0]

    def sparse(self, query: str) -> tuple[list[int], list[float]]:
        with self._lock:
            if self._sparse is None:
                from fastembed import SparseTextEmbedding

                self._sparse = SparseTextEmbedding(model_name=SPARSE_MODEL)
            embedding = next(iter(self._sparse.query_embed(query)))
        return embedding.indices.tolist(), embedding.values.tolist()


class ChunkIndex:
    def __init__(self, client: Any, collection: str, encoder: QueryEncoder, virtual_record_ids: Sequence[str]) -> None:  # noqa: ANN401
        from qdrant_client import models

        self._client = client
        self._collection = collection
        self._encoder = encoder
        self._scope = models.Filter(must=[
            models.FieldCondition(key=_VRID_KEY, match=models.MatchAny(any=list(virtual_record_ids))),
        ])

    def search(self, query: str, mode: RetrievalMode, limit: int) -> list[Chunk]:
        from qdrant_client import models

        prefetch = []
        if mode in ("dense", "hybrid"):
            prefetch.append(models.Prefetch(query=self._encoder.dense(query), using="dense", limit=limit * 2))
        if mode in ("sparse", "hybrid"):
            indices, values = self._encoder.sparse(query)
            prefetch.append(models.Prefetch(
                query=models.SparseVector(indices=indices, values=values), using="sparse", limit=limit * 2,
            ))
        response = self._client.query_points(
            collection_name=self._collection, prefetch=prefetch, query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=self._scope, limit=limit, with_payload=True,
        )
        return self._fold_sentences(response.points)

    def _fold_sentences(self, points: Sequence[Any]) -> list[Chunk]:  # noqa: ANN401
        ordered: list[tuple[tuple[str, int | None], float, str | None]] = []
        seen: set[tuple[str, int | None]] = set()
        need_block_text: set[tuple[str, int]] = set()
        for point in points:
            meta = (point.payload or {}).get("metadata") or {}
            vrid, block = meta.get("virtualRecordId"), meta.get("blockIndex")
            if not vrid:
                continue
            key = (vrid, block)
            if key in seen:
                continue
            seen.add(key)
            is_sentence = meta.get("isBlock") is False and not meta.get("isBlockGroup") and block is not None
            text = None if is_sentence else (point.payload or {}).get("page_content", "")
            if text is None:
                need_block_text.add((vrid, block))
            ordered.append((key, float(point.score or 0.0), text))
        block_text = self._block_texts(need_block_text)
        return [
            Chunk(key[0], key[1], text if text is not None else block_text.get(key, ""), score)
            for key, score, text in ordered
            if text is not None or key in block_text
        ]

    def _block_texts(self, keys: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
        if not keys:
            return {}
        from qdrant_client import models

        should = [
            models.Filter(must=[
                models.FieldCondition(key=_VRID_KEY, match=models.MatchValue(value=vrid)),
                models.FieldCondition(key="metadata.blockIndex", match=models.MatchValue(value=block)),
                models.FieldCondition(key="metadata.isBlock", match=models.MatchValue(value=True)),
            ])
            for vrid, block in keys
        ]
        points, _ = self._client.scroll(
            collection_name=self._collection, scroll_filter=models.Filter(should=should),
            limit=len(keys) * 2, with_payload=True, with_vectors=False,
        )
        found: dict[tuple[str, int], str] = {}
        for point in points:
            meta = (point.payload or {}).get("metadata") or {}
            found.setdefault((meta.get("virtualRecordId"), meta.get("blockIndex")), (point.payload or {}).get("page_content", ""))
        if len(found) < len(keys):
            logger.debug("no block point for %d sentence hit(s)", len(keys) - len(found))
        return found


def rrf_merge(ranked_lists: Sequence[Sequence[Chunk]], k: int = 60) -> list[Chunk]:
    """Reciprocal-rank fusion across query variants (same k=60 PipesHub uses
    when merging collections)."""
    scores: dict[tuple[str, int | None], float] = {}
    first: dict[tuple[str, int | None], Chunk] = {}
    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked):
            scores[chunk.key] = scores.get(chunk.key, 0.0) + 1.0 / (k + rank + 1)
            first.setdefault(chunk.key, chunk)
    order = sorted(scores, key=lambda key: -scores[key])
    return [first[key] for key in order]


def interleave(ranked_lists: Sequence[Sequence[Chunk]]) -> list[Chunk]:
    """Round-robin merge, so every sub-question keeps its best chunks."""
    merged: list[Chunk] = []
    seen: set[tuple[str, int | None]] = set()
    for depth in range(max((len(r) for r in ranked_lists), default=0)):
        for ranked in ranked_lists:
            if depth < len(ranked) and ranked[depth].key not in seen:
                seen.add(ranked[depth].key)
                merged.append(ranked[depth])
    return merged
