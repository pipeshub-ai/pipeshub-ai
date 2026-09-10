"""Unit tests for the search-hit ranking helpers (no live services).

Collapsing hits to documents is the one piece of logic that decides what every
ranking assertion means, so it is pinned here rather than trusted.
"""

from __future__ import annotations

import pytest

from retrieval.ranking import NOT_IN_CORPUS, describe, ranked_slugs

pytestmark = pytest.mark.unit

CORPUS = {"v-exp": "expenses", "v-srv": "servers", "v-brd": "birds"}


def _slug_of(virtual_id):
    if not virtual_id:
        return "<no virtual id>"
    return CORPUS.get(virtual_id, NOT_IN_CORPUS)


def _hit(virtual_id, content="text", score=0.5):
    return {"virtual_record_id": virtual_id, "content": content, "score": score}


def test_order_follows_the_hits() -> None:
    hits = [_hit("v-srv"), _hit("v-exp"), _hit("v-brd")]
    assert ranked_slugs(hits, _slug_of) == ["servers", "expenses", "birds"]


def test_one_document_cannot_occupy_several_places() -> None:
    """A chatty document returns many blocks; that is one document, not four.

    Without this, a single document filling the top of the result list would
    push every other document out of a "top three" assertion and make the test
    read as a ranking failure.
    """
    hits = [_hit("v-srv"), _hit("v-srv"), _hit("v-srv"), _hit("v-exp")]
    assert ranked_slugs(hits, _slug_of) == ["servers", "expenses"]


def test_a_document_keeps_its_best_position() -> None:
    """The first appearance wins, because that is the rank it achieved."""
    hits = [_hit("v-exp"), _hit("v-srv"), _hit("v-exp")]
    assert ranked_slugs(hits, _slug_of)[0] == "expenses"


def test_hits_from_outside_the_corpus_are_labelled_not_absorbed() -> None:
    """Other suites leave records in the same tenant.

    A stray hit has to be visible as foreign rather than silently counted as
    one of ours, which would make a wrong answer look right.
    """
    hits = [_hit("v-other"), _hit("v-exp")]
    assert ranked_slugs(hits, _slug_of) == [NOT_IN_CORPUS, "expenses"]


def test_a_hit_with_no_virtual_id_is_visible() -> None:
    assert ranked_slugs([_hit(None)], _slug_of) == ["<no virtual id>"]


def test_no_hits_is_an_empty_ranking() -> None:
    assert ranked_slugs([], _slug_of) == []


def test_the_failure_report_names_the_document_behind_each_hit() -> None:
    text = describe([_hit("v-srv", "runbook zarquon7731", 0.91)], _slug_of)
    assert "servers" in text and "0.91" in text


def test_the_failure_report_says_so_when_nothing_came_back() -> None:
    assert "no hits" in describe([], _slug_of)
