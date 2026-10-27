"""What a set of per-document vector counts is allowed to conclude.

These distributions only occur when indexing has already gone wrong, which is
the worst moment to discover the explanation is wrong too.
"""

from __future__ import annotations

import pytest

from helper.vector_counts import describe_divergence

pytestmark = pytest.mark.unit


def test_counts_that_agree_have_nothing_to_explain() -> None:
    assert describe_divergence({"a": 32, "b": 32, "c": 32}) is None
    assert describe_divergence({"a": 32}) is None
    assert describe_divergence({}) is None


def test_a_clear_majority_names_the_documents_short_of_it() -> None:
    """The failure that matters most: content that exists but cannot be found."""
    out = describe_divergence({"a": 32, "b": 32, "c": 32, "d": 31})

    assert out is not None
    assert "most hold 32" in out
    assert "cannot be found" in out
    assert "'d': 31" in out
    assert "indexed more than once" not in out


def test_a_clear_majority_names_the_documents_above_it() -> None:
    out = describe_divergence({"a": 31, "b": 31, "c": 31, "d": 32})

    assert out is not None
    assert "indexed more than once" in out
    assert "cannot be found" not in out


def test_a_tie_refuses_to_pick_a_side() -> None:
    """Eight documents splitting four-four is an ordinary shape of this failure.

    `Counter.most_common` breaks that tie by first-seen order, which here is
    upload order, so a verdict drawn from it would name whichever count the
    earliest-uploaded document happened to have -- the opposite bug half the
    time, stated with the same confidence.
    """
    split = {"a": 31, "b": 31, "c": 31, "d": 31, "e": 32, "f": 32, "g": 32, "h": 32}

    out = describe_divergence(split)

    assert out is not None
    assert "cannot be found" not in out
    assert "indexed more than once" not in out
    assert "most hold" not in out
    assert "31 vectors" in out and "32 vectors" in out


def test_a_tie_reads_the_same_whichever_order_the_documents_arrived_in() -> None:
    """The point of refusing: order must not change the conclusion."""
    first = describe_divergence({"a": 31, "b": 31, "c": 32, "d": 32})
    reversed_order = describe_divergence({"d": 32, "c": 32, "b": 31, "a": 31})

    assert first == reversed_order


def test_three_way_ties_are_also_refused() -> None:
    assert "most hold" not in (describe_divergence({"a": 30, "b": 31, "c": 32}) or "")
