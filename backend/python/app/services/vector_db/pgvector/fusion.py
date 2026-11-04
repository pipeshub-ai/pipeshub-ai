"""Reciprocal-rank fusion of the dense and lexical legs, in Python, as the Qdrant provider's server-side fusion computes it.

A point's score is the sum over the legs it appears in of ``1 / (1 + rank)`` with ranks counted from 1 (so a one-leg answer scores
``1 / (2 + index)``, what Qdrant answers for a single prefetch). Ties are broken by id, like ``QdrantUtils.stable_top``, so equal
scores come back in the same order every time.
"""

from __future__ import annotations


def rrf(legs: list[list[str]], limit: int) -> list[tuple[str, float]]:
    """``(id, score)`` of the best ``limit`` ids over ranked ``legs`` (each a list of ids, best first)."""
    scores: dict[str, float] = {}
    for leg in legs:
        for index, point_id in enumerate(leg):
            scores[point_id] = scores.get(point_id, 0.0) + 1.0 / (2 + index)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
