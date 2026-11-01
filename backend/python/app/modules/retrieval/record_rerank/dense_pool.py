"""RECORD_RERANK_DENSE_POOL: fill the reranker's pool past the fused list with the dense leg's own records.

On a corpus with many records about one topic, the record a question describes can rank 30-60 by dense similarity and
still fall out of the fused list, because the keyword leg ranks look-alikes above it. The reranker can only pick what
its pool holds, so the records a dense-only request ranks highly, and the fused list lacks, are appended behind the
fused ones, scored below every fused point. They are candidates only: a record the reranker does not place in its top
is dropped again before the record cut, so the search shows it only when the reranker picked it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.env_utils import env_bool, env_int

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from app.modules.retrieval.record_rerank.pipeline import ItemView

RECORD_RERANK_DENSE_POOL_ENV_VAR = "RECORD_RERANK_DENSE_POOL"
RECORD_RERANK_DENSE_POOL_SIZE_ENV_VAR = "RECORD_RERANK_DENSE_POOL_SIZE"
DEFAULT_DENSE_POOL_SIZE = 200
MAX_DENSE_POOL_SIZE = 500
# Points asked of the dense request per pool record: a record's points cluster.
DENSE_POINTS_PER_RECORD = 3
# The share of the pool kept for the records of the user's question behind an agent's search.
ANCHOR_POOL_SHARE = 0.4


def dense_pool_size() -> int:
    """Records the pool is filled to; 0 while RECORD_RERANK_DENSE_POOL is off (it is unless "true")."""
    if not env_bool(RECORD_RERANK_DENSE_POOL_ENV_VAR, False):
        return 0
    size = env_int(RECORD_RERANK_DENSE_POOL_SIZE_ENV_VAR, DEFAULT_DENSE_POOL_SIZE, lo=2, hi=MAX_DENSE_POOL_SIZE)
    return DEFAULT_DENSE_POOL_SIZE if size is None else size


def anchor_pool_records(size: int) -> int:
    """Records of a pool of ``size`` kept for the user's question behind an agent's search."""
    return max(1, int(size * ANCHOR_POOL_SHARE))


def with_dense_records(
    fused: Sequence[Any], dense: Sequence[Any], view: ItemView, size: int,
) -> tuple[list[Any], set[str]]:
    """``fused`` followed by the points of the records ``dense`` ranks that ``fused`` lacks, until the two hold
    ``size`` records, and the records added. The added points get scores below the lowest fused score, in the dense
    order."""
    seen = {view.record_of(item) for item in fused}
    seen.discard(None)
    added: list[Any] = []
    new_records: list[str] = []
    for item in dense:
        record = view.record_of(item)
        if not record or record in seen and record not in new_records:
            continue
        if record not in new_records:
            if len(seen) >= size:
                continue
            seen.add(record)
            new_records.append(record)
        added.append(item)
    if not added:
        return list(fused), set()
    floor = min((view.score_of(item) for item in fused), default=1.0)
    floor = floor if floor > 0 else 1.0
    step = floor / (len(added) + 1)
    scored = [view.with_score(item, floor - step * (i + 1)) for i, item in enumerate(added)]
    return [*fused, *scored], set(new_records)


def fill_pool(
    fused: Sequence[Any], dense: Sequence[Any], anchor: Sequence[Any], view: ItemView, size: int,
) -> tuple[list[Any], set[str]]:
    """``fused`` filled to ``size`` records with the query's dense records, then the anchor's, and the records
    added. With anchor points, the query's records leave the anchor's share of the pool free for them."""
    query_size = size - anchor_pool_records(size) if anchor else size
    items, added = with_dense_records(fused, dense, view, query_size)
    if anchor:
        items, anchor_added = with_dense_records(items, anchor, view, size)
        added |= anchor_added
    return items, added


def without_unpicked(items: Sequence[Any], view: ItemView, added: Collection[str], placed: Collection[str]) -> list[Any]:
    """``items`` without the points of the ``added`` records the reranker did not place."""
    dropped = set(added) - set(placed)
    if not dropped:
        return list(items)
    return [item for item in items if view.record_of(item) not in dropped]
