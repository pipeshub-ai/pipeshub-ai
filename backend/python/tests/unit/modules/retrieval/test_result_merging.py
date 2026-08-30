"""Reducing several collections' ranked lists to one top-K.

The property that matters: a merge must not invent an ordering the provider's
scores cannot support. Every provider PipesHub ships fuses ranks internally, so
their scores measure position, not similarity — and sorting positions across
collections lets a tiny irrelevant collection's best hit outrank a huge
relevant one's second-best.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.modules.retrieval.result_merging import (
    CollectionResults,
    ReciprocalRankFusionMerger,
    ScoreOrderedMerger,
    merge_collection_results,
    merger_for,
    result_identity,
)
from app.services.vector_db.models import ScoreSemantics


@dataclass
class FakeResult:
    """Enough of SearchResult for the merger: an id, a score, a payload."""

    id: str
    score: float = 0.0
    vrid: str | None = None
    block_id: str | None = None
    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        metadata: dict[str, Any] = {}
        if self.vrid:
            metadata["virtualRecordId"] = self.vrid
        if self.block_id:
            metadata["blockId"] = self.block_id
        self.payload = {"metadata": metadata}


def _collection(name: str, *results: FakeResult) -> CollectionResults:
    return CollectionResults(collection_name=name, results=list(results))


def _ids(results) -> list[str]:
    return [r.id for r in results]


class TestResultIdentity:
    def test_two_chunks_of_one_document_are_distinct(self):
        """Collapsing on virtualRecordId alone would keep one chunk per
        document and quietly gut recall."""
        a = FakeResult("p1", vrid="vr-1", block_id="b1")
        b = FakeResult("p2", vrid="vr-1", block_id="b2")
        assert result_identity(a) != result_identity(b)

    def test_the_same_block_in_two_collections_is_one_hit(self):
        """Point ids are fresh uuid4s per write, so the ids differ even though
        it is the same block."""
        a = FakeResult("uuid-a", vrid="vr-1", block_id="b1")
        b = FakeResult("uuid-b", vrid="vr-1", block_id="b1")
        assert result_identity(a) == result_identity(b)

    def test_a_hit_missing_block_id_falls_back_to_its_point_id(self):
        a = FakeResult("p1", vrid="vr-1")
        b = FakeResult("p2", vrid="vr-1")
        assert result_identity(a) != result_identity(b)

    def test_a_hit_with_no_metadata_is_not_collapsed(self):
        a = FakeResult("p1")
        b = FakeResult("p2")
        assert result_identity(a) != result_identity(b)


class TestScoreOrderedMerger:
    """Valid only when the provider returns comparable similarities."""

    def test_orders_by_score_across_collections(self):
        merged = ScoreOrderedMerger().merge(
            [
                _collection("a", FakeResult("a1", 0.9), FakeResult("a2", 0.4)),
                _collection("b", FakeResult("b1", 0.7), FakeResult("b2", 0.5)),
            ],
            limit=10,
        )
        assert _ids(merged) == ["a1", "b1", "b2", "a2"]

    def test_keeps_the_higher_scoring_copy_of_a_duplicate(self):
        merged = ScoreOrderedMerger().merge(
            [
                _collection("a", FakeResult("low", 0.3, vrid="v", block_id="b")),
                _collection("b", FakeResult("high", 0.9, vrid="v", block_id="b")),
            ],
            limit=10,
        )
        assert _ids(merged) == ["high"]

    def test_truncates_to_limit(self):
        # Interleaved, distinct scores so the expected order is unambiguous.
        merged = ScoreOrderedMerger().merge(
            [
                _collection("a", FakeResult("a0", 0.90), FakeResult("a1", 0.70)),
                _collection("b", FakeResult("b0", 0.80), FakeResult("b1", 0.60)),
            ],
            limit=3,
        )
        assert _ids(merged) == ["a0", "b0", "a1"]


class TestReciprocalRankFusionMerger:
    """The correct merge when scores encode rank, not similarity."""

    def test_interleaves_by_rank_not_by_score(self):
        """The bug this exists to prevent: collection B's scores are an order
        of magnitude larger, but they are RRF artifacts of a shorter list, so
        they say nothing about relevance. Rank 1 from each must come first."""
        merged = ReciprocalRankFusionMerger().merge(
            [
                _collection("a", FakeResult("a1", 0.016), FakeResult("a2", 0.016)),
                _collection("b", FakeResult("b1", 0.99), FakeResult("b2", 0.98)),
            ],
            limit=4,
        )
        assert _ids(merged)[:2] == ["a1", "b1"]
        assert set(_ids(merged)[2:]) == {"a2", "b2"}

    def test_a_tiny_collections_best_hit_does_not_bury_a_large_ones_second(self):
        """Both rank 1s tie, then both rank 2s — no collection dominates by
        virtue of being small."""
        merged = ReciprocalRankFusionMerger().merge(
            [
                _collection("big", *(FakeResult(f"big{i}") for i in range(5))),
                _collection("tiny", FakeResult("tiny0")),
            ],
            limit=6,
        )
        assert set(_ids(merged)[:2]) == {"big0", "tiny0"}
        assert _ids(merged)[2:] == ["big1", "big2", "big3", "big4"]

    def test_a_duplicate_takes_its_best_rank_not_the_sum(self):
        """Summing would promote a hit for being deduplicated across two
        connectors, over a better unique match."""
        merged = ReciprocalRankFusionMerger().merge(
            [
                _collection(
                    "a",
                    FakeResult("unique-top"),
                    FakeResult("dup-a", vrid="v", block_id="b"),
                ),
                _collection("b", FakeResult("dup-b", vrid="v", block_id="b")),
            ],
            limit=5,
        )
        # The duplicate's best rank is 1 (in collection b), tying with
        # unique-top; it must not out-rank it by summing both appearances.
        assert len(merged) == 2
        assert _ids(merged)[0] == "unique-top"

    def test_preserves_provider_order_within_one_collection(self):
        merged = ReciprocalRankFusionMerger().merge(
            [_collection("a", FakeResult("first"), FakeResult("second"))],
            limit=5,
        )
        assert _ids(merged) == ["first", "second"]

    def test_truncates_to_limit(self):
        merged = ReciprocalRankFusionMerger().merge(
            [
                _collection("a", *(FakeResult(f"a{i}") for i in range(4))),
                _collection("b", *(FakeResult(f"b{i}") for i in range(4))),
            ],
            limit=3,
        )
        assert len(merged) == 3

    def test_is_deterministic_across_runs(self):
        collections = [
            _collection("a", FakeResult("a1"), FakeResult("a2")),
            _collection("b", FakeResult("b1"), FakeResult("b2")),
        ]
        runs = {tuple(_ids(ReciprocalRankFusionMerger().merge(collections, 4))) for _ in range(5)}
        assert len(runs) == 1

    def test_rejects_a_non_positive_rank_constant(self):
        with pytest.raises(ValueError):
            ReciprocalRankFusionMerger(rank_constant=0)


class TestMergerSelection:
    def test_rank_fused_scores_get_the_fusion_merger(self):
        assert isinstance(
            merger_for(ScoreSemantics.RANK_FUSED), ReciprocalRankFusionMerger
        )

    def test_similarity_scores_get_the_score_merger(self):
        assert isinstance(merger_for(ScoreSemantics.SIMILARITY), ScoreOrderedMerger)


class TestSingleCollectionShortCircuit:
    """`single` deployments must be untouched by any of this."""

    def test_one_collection_keeps_the_providers_own_order(self):
        merger = ReciprocalRankFusionMerger()
        results = [FakeResult("p1", 0.1), FakeResult("p2", 0.9), FakeResult("p3", 0.5)]
        merged = merge_collection_results(
            [_collection("records", *results)], limit=10, merger=merger
        )
        # Not re-sorted by score: the provider already ranked these, and a
        # merge that reordered them would change single-collection behaviour.
        assert _ids(merged) == ["p1", "p2", "p3"]

    def test_one_collection_still_respects_limit(self):
        merged = merge_collection_results(
            [_collection("records", FakeResult("a"), FakeResult("b"))],
            limit=1,
            merger=ReciprocalRankFusionMerger(),
        )
        assert _ids(merged) == ["a"]

    def test_collections_that_returned_nothing_are_ignored(self):
        """One empty collection must not make this look like a multi-collection
        merge and reorder the one that answered."""
        merged = merge_collection_results(
            [
                _collection("empty"),
                _collection("records", FakeResult("p1", 0.1), FakeResult("p2", 0.9)),
            ],
            limit=10,
            merger=ReciprocalRankFusionMerger(),
        )
        assert _ids(merged) == ["p1", "p2"]

    def test_nothing_anywhere_is_an_empty_result(self):
        assert (
            merge_collection_results(
                [_collection("a"), _collection("b")],
                limit=10,
                merger=ReciprocalRankFusionMerger(),
            )
            == []
        )

    def test_no_collections_at_all_is_an_empty_result(self):
        assert merge_collection_results([], limit=10, merger=ScoreOrderedMerger()) == []
