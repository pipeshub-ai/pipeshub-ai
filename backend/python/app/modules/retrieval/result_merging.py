"""Merge per-collection search results into one ranked list.

A search under a multi-collection strategy asks each collection for the full
``limit`` — splitting it would cost recall, since nothing knows in advance
which collection holds the best matches — and then has N ranked lists to
reduce to one top-K.

The whole difficulty is that *how* they may be combined depends on what the
scores mean, and that is a property of the provider, not of the strategy. See
``ScoreSemantics``: every provider PipesHub ships fuses ranks internally, so
their scores are positions rather than similarities and sorting them across
collections silently compares a tiny collection's rank 1 against a huge one's.

So the merge is a strategy of its own, chosen from the provider's declared
semantics and applied identically no matter which collection strategy produced
the lists. A single-collection search bypasses all of it — there is nothing to
merge, and the provider's own ordering is already correct.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.vector_db.models import ScoreSemantics

# Standard RRF damping constant. 60 is the value from the original Cormack et
# al. paper and what Qdrant, OpenSearch, and Redis all default to internally —
# matching it keeps a cross-collection fusion on the same footing as the
# per-collection fusion the providers already did.
DEFAULT_RRF_RANK_CONSTANT = 60


@dataclass(frozen=True)
class CollectionResults:
    """One collection's ranked answer to one query.

    ``results`` is in the provider's own order, best first. Nothing here
    re-sorts it: that order *is* the rank information a fusion merge needs, and
    a merger that sorted first would destroy the only signal it can trust.
    """

    collection_name: str
    results: Sequence[Any]


def result_identity(result: Any) -> tuple:
    """What makes two hits the same hit.

    A search returns *chunks*, not documents: one record legitimately
    contributes several blocks and the caller reassembles them. So identity is
    ``(virtualRecordId, blockId)`` — the same block found in two collections,
    which the deduplication matrix allows by design.

    Point ids cannot serve: they are freshly minted uuid4s per write, so the
    same block indexed under two connectors carries two different ids. And
    ``virtualRecordId`` alone must not serve either — collapsing on it would
    keep one chunk per document and quietly gut recall.

    A hit missing either half falls back to its point id, so it is passed
    through rather than collapsed against unrelated hits.
    """
    payload = getattr(result, "payload", None) or {}
    metadata = payload.get("metadata") or {}
    vrid = metadata.get("virtualRecordId")
    block_id = metadata.get("blockId")
    if vrid and block_id:
        return ("block", vrid, block_id)
    return ("point", getattr(result, "id", None))


class ResultMerger(ABC):
    """Reduces several collections' ranked lists to one top-K."""

    @abstractmethod
    def merge(self, per_collection: Sequence[CollectionResults], limit: int) -> list:
        """Return at most ``limit`` results, best first."""


class ScoreOrderedMerger(ResultMerger):
    """Sort by score. Correct only when scores are comparable across collections.

    Keeps the highest-scoring copy of a duplicated hit, which under
    ``SIMILARITY`` is the same value either way — the tie-break exists so the
    result is deterministic, not because the copies differ.
    """

    def merge(self, per_collection: Sequence[CollectionResults], limit: int) -> list:
        best: dict[tuple, Any] = {}
        for collection in per_collection:
            for result in collection.results:
                key = result_identity(result)
                incumbent = best.get(key)
                if incumbent is None or _score(result) > _score(incumbent):
                    best[key] = result
        ranked = sorted(best.values(), key=_score, reverse=True)
        return ranked[:limit] if limit else ranked


class ReciprocalRankFusionMerger(ResultMerger):
    """Fuse the rank lists, ignoring the score magnitudes.

    The right merge when the providers already fused internally: their scores
    encode position, so this re-fuses positions rather than pretending the
    numbers are commensurable.

    A hit appearing in several collections takes its **best** rank rather than
    the sum of its contributions. Summing is the textbook formulation and is
    right when several *retrievers* independently vouch for one document — but
    here the lists are disjoint shards of one corpus, and a hit appears twice
    only because deduplication indexed the same content under two connectors.
    Rewarding it for that would promote duplicates over better unique matches.
    """

    def __init__(self, rank_constant: int = DEFAULT_RRF_RANK_CONSTANT) -> None:
        if rank_constant <= 0:
            raise ValueError("RRF rank constant must be positive")
        self._k = rank_constant

    def merge(self, per_collection: Sequence[CollectionResults], limit: int) -> list:
        best: dict[tuple, Any] = {}
        fused: dict[tuple, float] = {}
        # Ties are broken by first appearance so the output is stable across
        # runs; dict preserves insertion order and collections arrive in a
        # deterministic order from the fan-out.
        for collection in per_collection:
            for rank, result in enumerate(collection.results):
                key = result_identity(result)
                contribution = 1.0 / (self._k + rank + 1)
                if key not in fused or contribution > fused[key]:
                    fused[key] = contribution
                    best[key] = result
        ranked = sorted(best, key=lambda key: fused[key], reverse=True)
        merged = [best[key] for key in ranked]
        return merged[:limit] if limit else merged


def merger_for(
    semantics: ScoreSemantics, rank_constant: int = DEFAULT_RRF_RANK_CONSTANT
) -> ResultMerger:
    """The merge a provider's score semantics permit."""
    if semantics is ScoreSemantics.SIMILARITY:
        return ScoreOrderedMerger()
    return ReciprocalRankFusionMerger(rank_constant)


def merge_collection_results(
    per_collection: Iterable[CollectionResults],
    limit: int,
    merger: ResultMerger,
) -> list:
    """Merge, short-circuiting the single-collection case.

    One collection needs no merge: the provider's ordering is already the
    answer, and running a fusion over it would only re-derive the same order
    while risking a behaviour change on the deployments that never asked for
    multi-collection search.
    """
    collections = [c for c in per_collection if c.results]
    if not collections:
        return []
    if len(collections) == 1:
        results = list(collections[0].results)
        return results[:limit] if limit else results
    return merger.merge(collections, limit)


def _score(result: Any) -> float:
    value = getattr(result, "score", None)
    return float(value) if value is not None else 0.0
