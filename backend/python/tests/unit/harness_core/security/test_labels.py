"""LB-1: the label algebra (P2.5a)."""

from __future__ import annotations

import pytest

from harness_core.security.labels import (
    MAX_LABEL_SOURCES,
    SourceLabel,
    join_labels,
    label_of,
)

A = label_of({"r1"})
B = label_of({"r2", "r3"})
UNKNOWN = SourceLabel[str](False, frozenset())
EMPTY = SourceLabel[str](True, frozenset())


def test_join_unions_the_sources() -> None:
    assert A.join(B) == SourceLabel(True, frozenset({"r1", "r2", "r3"}))
    assert join_labels([A, B, EMPTY]) == A.join(B)


@pytest.mark.parametrize("other", [UNKNOWN, None])
def test_anything_unknown_makes_the_join_unknown(other: SourceLabel[str] | None) -> None:
    assert A.join(other) == UNKNOWN
    assert join_labels([A, other, B]) == UNKNOWN
    assert UNKNOWN.join(A) == UNKNOWN


def test_nothing_to_join_is_empty_and_complete() -> None:
    assert join_labels([]) == EMPTY
    assert EMPTY.complete


def test_more_sources_than_the_bound_is_unknown() -> None:
    assert label_of(str(i) for i in range(MAX_LABEL_SOURCES)).complete
    assert label_of(str(i) for i in range(MAX_LABEL_SOURCES + 1)) == UNKNOWN
    half = label_of(str(i) for i in range(MAX_LABEL_SOURCES))
    assert half.join(label_of({"one-more"})) == UNKNOWN


def test_passes_only_when_complete_and_every_source_is_readable() -> None:
    readable = {"r1", "r2"}.__contains__
    assert A.passes(readable)
    assert not A.join(B).passes(readable)
    assert not UNKNOWN.passes(lambda _: True)
    assert EMPTY.passes(lambda _: False)
