"""The clustering scores, on cases small enough to work out by hand."""
from __future__ import annotations

import pytest

from tests.evals.entity_resolution.metrics import Scores, bcubed, pairwise

GOLD = {"a1": "A", "a2": "A", "a3": "A", "b1": "B", "b2": "B"}


def test_a_perfect_clustering_scores_one() -> None:
    predicted = {"a1": 1, "a2": 1, "a3": 1, "b1": 2, "b2": 2}
    assert pairwise(GOLD, predicted) == Scores(1.0, 1.0)
    assert bcubed(GOLD, predicted) == Scores(1.0, 1.0)


def test_splitting_costs_recall_only() -> None:
    predicted = {"a1": 1, "a2": 1, "a3": 3, "b1": 2, "b2": 2}
    pairs = pairwise(GOLD, predicted)
    # Gold pairs: 3 (A) + 1 (B); predicted pairs: 1 (a1-a2) + 1 (b1-b2), both right.
    assert pairs.precision == 1.0 and pairs.recall == pytest.approx(2 / 4)
    b3 = bcubed(GOLD, predicted)
    # a1, a2 recall 2/3; a3 recall 1/3; b1, b2 recall 1.
    assert b3.precision == 1.0 and b3.recall == pytest.approx((2 / 3 + 2 / 3 + 1 / 3 + 1 + 1) / 5)


def test_over_merging_costs_precision_only() -> None:
    predicted = dict.fromkeys(GOLD, "one")
    pairs = pairwise(GOLD, predicted)
    assert pairs.recall == 1.0 and pairs.precision == pytest.approx(4 / 10)
    b3 = bcubed(GOLD, predicted)
    assert b3.recall == 1.0 and b3.precision == pytest.approx((3 * 3 / 5 + 2 * 2 / 5) / 5)


def test_all_singletons_have_no_pairs_to_get_wrong() -> None:
    gold = {"x": 1, "y": 2}
    predicted = {"x": "p", "y": "q"}
    assert pairwise(gold, predicted) == Scores(1.0, 1.0)
    assert bcubed(gold, predicted) == Scores(1.0, 1.0)


def test_f1_is_the_harmonic_mean() -> None:
    assert Scores(0.5, 1.0).f1 == pytest.approx(2 / 3)
    assert Scores(0.0, 0.0).f1 == 0.0


def test_mismatched_mentions_are_refused() -> None:
    with pytest.raises(ValueError):
        pairwise({"a": 1}, {"b": 1})
