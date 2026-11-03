"""Deterministic gates shared by taxonomy resolution and named-entity resolution.

A pair that fails a gate is not merged. Named-entity resolution uses all
three. Taxonomy resolution keeps its own model-adjudicated path and does
not call these; they exist so both resolvers share one implementation of
the same rules.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

_DIGIT_RE = re.compile(r"\d")
_WS_RE = re.compile(r"\s+")


class ResolvableKind(Protocol):
    """A partition names resolve inside and never across.

    ``collection`` is the graph collection, ``entity_type`` the vector type,
    and ``partition_key`` the finer bucket (taxonomy level, or named-entity
    kind). Two names with different partition keys are different entities.
    """

    collection: str
    partition_key: str


def digit_guard(left: str, right: str) -> bool:
    """False when the names differ by a digit, so 'Q3 2024' is not 'Q4 2024'."""
    a = _WS_RE.sub(" ", (left or "").casefold()).strip()
    b = _WS_RE.sub(" ", (right or "").casefold()).strip()
    if a == b:
        return True
    # Any bigram present in only one name that contains a digit blocks the merge.
    bigrams_a = {a[i : i + 2] for i in range(len(a) - 1)}
    bigrams_b = {b[i : i + 2] for i in range(len(b) - 1)}
    for gram in bigrams_a.symmetric_difference(bigrams_b):
        if _DIGIT_RE.search(gram):
            return False
    return True


def entropy_allows_fuzzy(name: str) -> bool:
    """Short or low-entropy names are too ambiguous for fuzzy matching."""
    text = _WS_RE.sub(" ", (name or "").casefold()).strip()
    tokens = text.split()
    if len(text) < 6 or len(tokens) < 2:
        return False
    counts = Counter(text)
    total = len(text)
    entropy = -sum((n / total) * math.log2(n / total) for n in counts.values())
    return entropy >= 1.5


def char_ngrams(text: str, n: int = 3) -> Counter:
    folded = _WS_RE.sub(" ", (text or "").casefold()).strip()
    padded = f" {folded} "
    return Counter(padded[i : i + n] for i in range(max(0, len(padded) - n + 1)))


def jaccard(left: str, right: str, n: int = 3) -> float:
    a = char_ngrams(left, n)
    b = char_ngrams(right, n)
    if not a and not b:
        return 1.0
    intersection = sum((a & b).values())
    union = sum((a | b).values())
    if union == 0:
        return 0.0
    return intersection / union


JACCARD_MERGE_THRESHOLD = 0.9


def fuzzy_same(left: str, right: str) -> bool:
    """True when the names may merge without a model call."""
    if not digit_guard(left, right):
        return False
    if not entropy_allows_fuzzy(left) or not entropy_allows_fuzzy(right):
        return False
    return jaccard(left, right) >= JACCARD_MERGE_THRESHOLD


def validate_candidate_id(offered_ids: set[str], chosen: str | None) -> str | None:
    """A model may only pick an id it was offered. Anything else means new."""
    if not chosen or chosen not in offered_ids:
        return None
    return chosen
