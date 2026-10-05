"""The k-way merge orders, dedupes, and reports exactly the rows it emitted.

The boundary assertions are the point of this file. A merge that emits the
right rows but reports the wrong edge produces a *correct-looking* page whose
successor silently skips a row — nothing about page length, ordering or totals
reveals it, and it only ever shows up as a record a user cannot find.
"""

import pytest

from app.connectors.sources.localKB.handlers import kh_merge
from app.connectors.sources.localKB.handlers.kh_merge import (
    MergeError,
    PartitionFeed,
    SortKey,
    key_for,
    merge_pages,
)


def row(row_id: str, sort_key, *, null_rank: int = 0, internal: bool = False) -> dict:
    return {
        "id": row_id,
        "sortKey": sort_key,
        "nullRank": null_rank,
        "parentIsInternal": internal,
    }


def feed(partition_id: str, *rows: dict, kind: str = "GROUP") -> PartitionFeed:
    return PartitionFeed(
        partition_id=partition_id, partition_kind=kind, rows=iter(rows)
    )


def ids(result) -> list[str]:
    return [r["id"] for r in result.rows]


def test_ascending_interleaves_partitions() -> None:
    result = merge_pages(
        [feed("A", row("a1", "apple"), row("a2", "cherry")),
         feed("B", row("b1", "banana"), row("b2", "date"))],
        limit=4,
    )
    assert ids(result) == ["a1", "b1", "a2", "b2"]


def test_descending_interleaves_partitions() -> None:
    """A descending sort merges descending, across partitions as within one."""
    result = merge_pages(
        [feed("A", row("a1", "cherry"), row("a2", "apple")),
         feed("B", row("b1", "date"), row("b2", "banana"))],
        limit=4,
        descending=True,
    )
    assert ids(result) == ["b1", "a1", "b2", "a2"]


def test_null_rank_sorts_last_in_both_directions() -> None:
    """Nulls keep their place when the sort flips, as the query's ORDER BY does.

    The null row is **alone in its partition**, so it competes as a head
    against another partition's rows and the comparator actually decides.
    Trailing it behind a non-null row in the same feed would let the iterator's
    own order carry the assertion, and the test would pass even with null
    ordering inverted.
    """
    for descending in (False, True):
        ordered_b = (
            [row("b2", "cherry"), row("b1", "banana")]
            if descending
            else [row("b1", "banana"), row("b2", "cherry")]
        )
        result = merge_pages(
            [feed("A", row("a-null", None, null_rank=1)), feed("B", *ordered_b)],
            limit=3,
            descending=descending,
        )
        assert ids(result)[-1] == "a-null", f"descending={descending}: {ids(result)}"


def test_ties_break_on_id_ascending_in_both_directions() -> None:
    """Equal sort values, across partitions and within one. `prev` only
    returns the page the user came from if ties never flip."""
    for descending in (False, True):
        result = merge_pages(
            [feed("A", row("a-first", "same")), feed("B", row("b-second", "same"))],
            limit=2,
            descending=descending,
        )
        assert ids(result) == ["a-first", "b-second"], f"descending={descending}"


def test_the_page_edges_are_rows_emitted_not_rows_buffered() -> None:
    """The lookahead trap: a buffered row must not become
    the resume point.

    Both a2 and b2 are pulled into the heap here but not emitted. Reporting
    either as the last row would start the next page after it, dropping it.
    """
    result = merge_pages(
        [feed("A", row("a1", "apple"), row("a2", "cherry")),
         feed("B", row("b1", "banana"), row("b2", "date"))],
        limit=2,
    )
    assert ids(result) == ["a1", "b1"]
    assert result.first.last_id == "a1"
    assert result.last.last_id == "b1"


def test_the_edges_carry_the_querys_own_comparator_output() -> None:
    """The query and the merge share one collation because the merge reads
    the comparator fields the query emitted, rather than recomputing them."""
    result = merge_pages([feed("A", row("a1", "apple", null_rank=0))], limit=1)
    assert (result.last.sort_key, result.last.null_rank) == ("apple", 0)
    assert result.last.as_after() == {"nullRank": 0, "sortKey": "apple", "id": "a1"}


def test_an_empty_page_has_no_edges() -> None:
    result = merge_pages([feed("A")], limit=5)
    assert result.rows == [] and result.first is None and result.last is None


def test_exhausted_partitions_are_reported() -> None:
    """One partition runs out early and must be recorded as finished."""
    result = merge_pages(
        [feed("A", row("a1", "apple")), feed("B", row("b1", "banana"))], limit=10
    )
    assert result.exhausted == {"A", "B"}


def test_a_partition_with_rows_left_is_not_exhausted() -> None:
    result = merge_pages(
        [feed("A", row("a1", "apple")), feed("B", row("b1", "zulu"), row("b2", "zz"))],
        limit=1,
    )
    assert ids(result) == ["a1"]
    assert "B" not in result.exhausted


def test_a_duplicate_is_emitted_once_and_the_real_parent_wins() -> None:
    """Shared with Me must not displace the file's own folder."""
    result = merge_pages(
        [feed("A", row("rec-x", "file.txt", internal=True)),
         feed("B", row("rec-x", "file.txt", internal=False))],
        limit=5,
    )
    assert ids(result) == ["rec-x"]
    assert result.rows[0]["parentIsInternal"] is False
    assert result.dropped == 1


def test_a_dropped_duplicate_still_leaves_a_full_page() -> None:
    result = merge_pages(
        [feed("A", row("rec-x", "apple", internal=True), row("a2", "cherry")),
         feed("B", row("rec-x", "apple"), row("b2", "banana"))],
        limit=2,
    )
    assert ids(result) == ["rec-x", "b2"], "the drop should have been refilled"


def test_repeated_ids_from_one_partition_eventually_raise(monkeypatch) -> None:
    """A partition echoing one id forever is a bug, not a page to keep refilling."""
    monkeypatch.setattr(kh_merge, "_MAX_CONSECUTIVE_DROPS", 1)
    with pytest.raises(MergeError, match="consecutive duplicate"):
        merge_pages(
            [feed("A", row("x", "a"), row("x", "b"), row("x", "c"), row("x", "d"))],
            limit=10,
        )


def test_mixed_types_in_one_sort_field_raise() -> None:
    with pytest.raises(MergeError, match="mixed types"):
        merge_pages(
            [feed("A", row("a1", "apple")), feed("B", row("b1", 7))], limit=2
        )


@pytest.mark.parametrize("missing", ["sortKey", "nullRank", "id"])
def test_a_row_without_the_comparator_fields_raises(missing: str) -> None:
    broken = row("a1", "apple")
    broken.pop(missing)
    with pytest.raises(MergeError, match="missing"):
        merge_pages([feed("A", broken)], limit=1)


def test_a_non_positive_limit_raises() -> None:
    with pytest.raises(MergeError, match="limit must be positive"):
        merge_pages([feed("A", row("a1", "apple"))], limit=0)


def test_no_feeds_yields_an_empty_page() -> None:
    result = merge_pages([], limit=10)
    assert result.rows == [] and result.last is None


# --------------------------------------------------------------- reverse merge

def test_a_reverse_merge_takes_the_rows_nearest_the_boundary_in_page_order() -> None:
    """Feeds arrive nearest the boundary first; the page comes back in page order."""
    result = merge_pages(
        [feed("A", row("a2", "cherry"), row("a1", "apple")),
         feed("B", row("b2", "date"), row("b1", "banana"))],
        limit=3,
        reverse=True,
    )
    assert ids(result) == ["b1", "a2", "b2"]
    assert (result.first.last_id, result.last.last_id) == ("b1", "b2")


def test_a_reverse_merge_meets_nulls_first_and_ties_from_the_end() -> None:
    """Going back from the end, the null bucket comes first, and between tied rows
    the higher id is nearer the boundary — the mirror of the forward order."""
    nulls = merge_pages(
        [feed("A", row("a-null", None, null_rank=1)), feed("B", row("b1", "banana"))],
        limit=1,
        reverse=True,
    )
    assert ids(nulls) == ["a-null"]

    ties = merge_pages(
        [feed("A", row("a-first", "same")), feed("B", row("b-second", "same"))],
        limit=1,
        reverse=True,
    )
    assert ids(ties) == ["b-second"]


@pytest.mark.parametrize("descending", [False, True])
def test_a_previous_page_is_exactly_the_forward_page_before_it(descending: bool) -> None:
    """Page 2 forward, then back from its first row, gives page 1."""
    values = ["apple", "banana", "cherry", "date", "elder", "fig", "grape", "honey"]
    forward_order = sorted(values, reverse=descending)
    partition_a = [row(f"id-{v}", v) for v in forward_order if values.index(v) % 2 == 0]
    partition_b = [row(f"id-{v}", v) for v in forward_order if values.index(v) % 2 == 1]
    partition_b.append(row("id-none", None, null_rank=1))

    page_1 = merge_pages([feed("A", *partition_a), feed("B", *partition_b)],
                         limit=3, descending=descending)
    boundary = page_1.last

    def after(rows):
        return [r for r in rows if key_for(r, descending) > key_for(
            {"id": boundary.last_id, "sortKey": boundary.sort_key, "nullRank": boundary.null_rank},
            descending)]

    page_2 = merge_pages([feed("A", *after(partition_a)), feed("B", *after(partition_b))],
                         limit=3, descending=descending)
    start = page_2.first

    def before_nearest_first(rows):
        edge = key_for({"id": start.last_id, "sortKey": start.sort_key, "nullRank": start.null_rank},
                       descending)
        return list(reversed([r for r in rows if key_for(r, descending) < edge]))

    back = merge_pages([feed("A", *before_nearest_first(partition_a)),
                        feed("B", *before_nearest_first(partition_b))],
                       limit=3, descending=descending, reverse=True)
    assert ids(back) == ids(page_1)


def test_sort_key_orders_null_rank_ahead_of_value() -> None:
    assert SortKey(0, "zulu", "z") < SortKey(1, "alpha", "a")
    assert SortKey(0, "zulu", "z", descending=True) < SortKey(1, "alpha", "a", True)


def test_key_for_reads_the_querys_fields() -> None:
    """The comparator is built from the query's fields."""
    key = key_for(row("a1", "apple", null_rank=0), descending=True)
    assert (key.null_rank, key.value, key.row_id, key.descending) == (0, "apple", "a1", True)


def test_strings_order_by_utf16_unit_as_cypher_does() -> None:
    """Neo4j sorts a page with Java's String.compareTo: an astral character (a
    surrogate pair, from U+D800) sorts before U+E000..U+FFFF. Python's code-point
    order is the reverse, and a merge on it would disagree with the pages it
    resumes."""
    fullwidth, emoji, ext_b = "ｚfull", "\U0001f600emoji", "\U00020000extb"
    assert SortKey(0, emoji, "a") < SortKey(0, ext_b, "b") < SortKey(0, fullwidth, "c")
    assert SortKey(0, fullwidth, "c", True) < SortKey(0, ext_b, "b", True)
    page = merge_pages([feed("p1", row("c", fullwidth)), feed("p2", row("a", emoji), row("b", ext_b))],
                       limit=3, descending=False)
    assert ids(page) == ["a", "b", "c"]
