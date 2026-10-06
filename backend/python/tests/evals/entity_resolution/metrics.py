"""Clustering scores for entity resolution.

A mention is one extracted name in one record. ``gold`` and ``predicted`` map
each mention to a cluster label: the gold entity it means, and the graph node
the resolver linked it to. Labels are compared only for equality, so the two
label spaces need not match.

- Pairwise: precision and recall over the pairs of mentions put in one
  cluster. Sensitive to large clusters, which contribute quadratically.
- B-cubed (Bagga and Baldwin, 1998): per-mention precision and recall of the
  mention's predicted cluster against its gold cluster, averaged over
  mentions, so every mention weighs the same.

Over-merging (two entities on one node) lowers precision; splitting (one
entity on several nodes) lowers recall.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping


@dataclass(frozen=True)
class Scores:
    precision: float
    recall: float

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0

    def as_dict(self) -> dict[str, float]:
        return {"precision": round(self.precision, 4), "recall": round(self.recall, 4), "f1": round(self.f1, 4)}


def _check(gold: Mapping[Hashable, Hashable], predicted: Mapping[Hashable, Hashable]) -> None:
    if set(gold) != set(predicted):
        missing = set(gold) ^ set(predicted)
        raise ValueError(f"gold and predicted must label the same mentions; differ on {sorted(map(str, missing))[:5]}")


def pairwise(gold: Mapping[Hashable, Hashable], predicted: Mapping[Hashable, Hashable]) -> Scores:
    _check(gold, predicted)
    together_gold = together_predicted = together_both = 0
    for a, b in combinations(sorted(gold, key=str), 2):
        same_gold = gold[a] == gold[b]
        same_predicted = predicted[a] == predicted[b]
        together_gold += same_gold
        together_predicted += same_predicted
        together_both += same_gold and same_predicted
    # No pairs to get wrong is perfect, not undefined.
    precision = together_both / together_predicted if together_predicted else 1.0
    recall = together_both / together_gold if together_gold else 1.0
    return Scores(precision, recall)


def bcubed(gold: Mapping[Hashable, Hashable], predicted: Mapping[Hashable, Hashable]) -> Scores:
    _check(gold, predicted)
    if not gold:
        return Scores(1.0, 1.0)
    gold_members: dict[Hashable, set[Hashable]] = defaultdict(set)
    predicted_members: dict[Hashable, set[Hashable]] = defaultdict(set)
    for mention in gold:
        gold_members[gold[mention]].add(mention)
        predicted_members[predicted[mention]].add(mention)
    precision = recall = 0.0
    for mention in gold:
        same_gold = gold_members[gold[mention]]
        same_predicted = predicted_members[predicted[mention]]
        overlap = len(same_gold & same_predicted)
        precision += overlap / len(same_predicted)
        recall += overlap / len(same_gold)
    return Scores(precision / len(gold), recall / len(gold))
