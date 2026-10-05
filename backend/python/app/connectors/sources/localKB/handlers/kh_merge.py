"""Merge per-partition rows into one ordered page.

Each partition is sorted by the query itself; this combines them without
re-sorting and without loading more than one row per partition at a time.

`SortKey` orders by the ``nullRank`` and ``sortKey`` the query emitted, then by
id, so the query and the merge share one comparator.

The page's last row is where every partition resumes going forward, and its
first row where they resume going back (``kh_cursor``). Those are rows the
merge *emitted*, never one it only buffered, which is why this is not
`heapq.merge`: that reads one row ahead per iterator, and resuming after a row
that was read but not emitted skips it.

A record reachable by two hierarchy parents (a Drive file in both its folder
and Shared with Me) arrives once per partition with an identical sort key, so
the copies are adjacent. The real hierarchy parent wins over the internal one,
both copies are consumed, and one more row is pulled so the page stays full.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from app.utils.kh_cursor import Boundary

# This many duplicates dropped in a row means the partitions overlap wholesale;
# raising beats spinning through an unbounded result set.
_MAX_CONSECUTIVE_DROPS = 1000


class MergeError(RuntimeError):
    """The merge cannot produce a correct page."""


def sort_order(value: Any) -> Any:  # noqa: ANN401
    """A sort value or id as the merge orders it: a string by UTF-16 code unit,
    as Cypher's ORDER BY does (Java's ``String.compareTo``). Python's own order
    is by code point, which puts U+E000..U+FFFF (fullwidth forms, private use)
    after the astral planes (emoji, CJK extension B), where Neo4j puts them
    before; a page would then disagree with the merge that resumes it."""
    return value.encode("utf-16-be", "surrogatepass") if isinstance(value, str) else value


@dataclass(frozen=True)
class SortKey:
    """The query's own comparator output for one row.

    ``null_rank`` sorts ahead of the value in **both** directions, as the
    query's ORDER BY does: nulls keep their place when the sort flips. The id
    tiebreak is always ascending too, so a descending page and its reverse walk
    agree on the order of tied rows.
    """

    null_rank: int
    value: Any
    row_id: str
    descending: bool = False

    def __lt__(self, other: "SortKey") -> bool:
        if self.null_rank != other.null_rank:
            return self.null_rank < other.null_rank
        if self.value != other.value:
            try:
                less = sort_order(self.value) < sort_order(other.value)
            except TypeError as exc:
                raise MergeError(
                    f"cannot order {self.value!r} against {other.value!r}; the "
                    f"query emitted mixed types for one sort field"
                ) from exc
            return not less if self.descending else less
        return sort_order(self.row_id) < sort_order(other.row_id)


@dataclass(frozen=True)
class _Backward:
    """A `SortKey` walked from the end: the row nearest the boundary pops first."""

    key: SortKey

    def __lt__(self, other: "_Backward") -> bool:
        return other.key < self.key


def key_for(row: dict, descending: bool) -> SortKey:
    try:
        return SortKey(
            null_rank=row["nullRank"],
            value=row["sortKey"],
            row_id=row["id"],
            descending=descending,
        )
    except KeyError as exc:
        raise MergeError(
            f"row is missing {exc.args[0]!r}; the query must return its own "
            f"sortKey and nullRank so the merge uses the same comparator"
        ) from exc


@dataclass
class PartitionFeed:
    """One partition's rows, in the order the merge walks them.

    Forward, that is page order. Backward (``reverse=True``), nearest the
    boundary first — the reverse of page order.
    """

    partition_id: str
    partition_kind: str
    rows: Iterator[dict]


@dataclass
class MergeResult:
    rows: list[dict] = field(default_factory=list)
    first: Boundary | None = None
    last: Boundary | None = None
    exhausted: set[str] = field(default_factory=set)
    dropped: int = 0


def merge_pages(
    feeds: Sequence[PartitionFeed],
    *,
    limit: int,
    descending: bool = False,
    reverse: bool = False,
) -> MergeResult:
    """Take the next `limit` rows across all partitions, returned in page order.

    ``reverse`` takes the `limit` rows nearest the boundary going backwards, for
    a previous page. ``exhausted`` names the feeds that ran dry while merging.
    """
    if limit <= 0:
        raise MergeError(f"limit must be positive, got {limit}")

    result = MergeResult()
    heap: list[tuple[Any, int]] = []
    head: dict[int, dict] = {}

    def pull(index: int) -> None:
        row = next(feeds[index].rows, None)
        if row is None:
            result.exhausted.add(feeds[index].partition_id)
            return
        head[index] = row
        key = key_for(row, descending)
        heapq.heappush(heap, (_Backward(key) if reverse else key, index))

    for index in range(len(feeds)):
        pull(index)

    emitted: set[str] = set()
    consecutive_drops = 0

    while heap and len(result.rows) < limit:
        _, index = heapq.heappop(heap)
        row = head.pop(index)

        # Copies of one row arrive adjacent, because their sort keys are equal.
        group = [(index, row)]
        while heap and head[heap[0][1]]["id"] == row["id"]:
            _, other = heapq.heappop(heap)
            group.append((other, head.pop(other)))

        if row["id"] in emitted:
            result.dropped += 1
            consecutive_drops += 1
            if consecutive_drops > _MAX_CONSECUTIVE_DROPS:
                raise MergeError(
                    f"dropped {consecutive_drops} consecutive duplicate rows; "
                    f"the partitions are probably overlapping wholesale"
                )
        else:
            result.rows.append(_winner(group))
            emitted.add(row["id"])
            consecutive_drops = 0
            result.dropped += len(group) - 1

        for member_index, _ in group:
            pull(member_index)

    if reverse:
        result.rows.reverse()
    if result.rows:
        result.first = Boundary.of(result.rows[0])
        result.last = Boundary.of(result.rows[-1])
    return result


def _winner(group: list[tuple[int, dict]]) -> dict:
    """A real hierarchy parent beats an internal container such as Shared with Me.

    Keyed on ``parentIsInternal`` rather than on that group, so any internal
    group a connector writes is placed the same way.
    """
    for _, row in group:
        if not row.get("parentIsInternal"):
            return row
    return group[0][1]
