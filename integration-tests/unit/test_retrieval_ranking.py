"""Unit tests for the search-hit ranking helpers (no live services).

Collapsing hits to documents is the one piece of logic that decides what every
ranking assertion means, so it is pinned here rather than trusted.
"""

from __future__ import annotations

import pytest

from retrieval.ranking import (
    NOT_IN_CORPUS,
    NO_VIRTUAL_ID,
    describe,
    ranked_slugs,
    top,
    virtual_id_of,
)

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
    assert ranked_slugs([_hit(None)], _slug_of) == [NO_VIRTUAL_ID]


def test_two_different_foreign_documents_keep_two_positions() -> None:
    """They share a label but are not the same document.

    Folding on the label would turn two foreign hits into one position, so a
    corpus document ranked third would appear second and a "top two" assertion
    would pass on a result that failed.
    """
    hits = [_hit("v-foreign-a"), _hit("v-foreign-b"), _hit("v-exp")]
    ranked = ranked_slugs(hits, _slug_of)

    assert ranked == [NOT_IN_CORPUS, NOT_IN_CORPUS, "expenses"]
    assert "expenses" not in ranked[:2], (
        "The corpus document ranked third and must not appear in the top two."
    )


def test_repeated_hits_on_one_foreign_document_still_collapse() -> None:
    """Same id, same document — the dedup rule applies to foreign hits too."""
    hits = [_hit("v-foreign-a"), _hit("v-foreign-a"), _hit("v-exp")]
    assert ranked_slugs(hits, _slug_of) == [NOT_IN_CORPUS, "expenses"]


def test_untraceable_hits_each_keep_a_position() -> None:
    """Nothing says two hits with no id came from the same document.

    Giving each its own slot makes a ranking assertion stricter rather than
    more forgiving, which is the safe direction for a test.
    """
    hits = [_hit(None), _hit(None), _hit("v-exp")]
    assert ranked_slugs(hits, _slug_of) == [NO_VIRTUAL_ID, NO_VIRTUAL_ID, "expenses"]


def test_a_raw_vector_store_hit_is_still_traceable() -> None:
    """Unflattened hits keep the id at metadata.virtualRecordId.

    Reading only the top-level field would label these untraceable and drop
    them out of every ranking.
    """
    raw = {"metadata": {"virtualRecordId": "v-exp"}, "content": "text"}
    assert virtual_id_of(raw) == "v-exp"
    assert ranked_slugs([raw], _slug_of) == ["expenses"]


def test_the_flattened_field_wins_when_both_are_present() -> None:
    hit = {"virtual_record_id": "v-exp", "metadata": {"virtualRecordId": "v-srv"}}
    assert virtual_id_of(hit) == "v-exp"


def test_a_malformed_metadata_value_does_not_crash() -> None:
    assert virtual_id_of({"metadata": "not a dict"}) is None


def test_top_of_an_empty_ranking_is_a_marker_not_an_error() -> None:
    """Failure messages index the top result; on no results that used to raise
    IndexError and replace the real problem — search returned nothing."""
    assert top([]) == "<no results>"
    assert top(["expenses"]) == "expenses"


def test_no_hits_is_an_empty_ranking() -> None:
    assert ranked_slugs([], _slug_of) == []


def test_the_failure_report_names_the_document_behind_each_hit() -> None:
    text = describe([_hit("v-srv", "runbook zarquon7731", 0.91)], _slug_of)
    assert "servers" in text and "0.91" in text


def test_the_failure_report_says_so_when_nothing_came_back() -> None:
    assert "no hits" in describe([], _slug_of)
