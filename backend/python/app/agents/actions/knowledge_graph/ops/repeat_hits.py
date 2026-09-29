"""Records that keep surfacing in searches without being read.

A model looking for one fact in a long record tends to rephrase its search
again and again, getting a few more blocks of the same record each time. Once
a record has come up in enough searches of one request and was never fetched,
the search result says so and gives the fetch call that reads the part where
its hits cluster.

State lives in the request's ``tool_state``, which every agent of the request
shares, so the count covers searches made by a delegate as well.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.fetch import (
    FETCH_RECORD_GRANTED_KEY,
    FETCH_RECORD_TOOL_NAME,
)
from app.modules.retrieval.context.neighbours import NEIGHBOUR_KEY

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

# Two searches landing on one record is ordinary; a third means the model is
# circling a record it has not read.
REPEAT_HIT_SEARCHES = 3
# Width of the window whose hits locate where to start reading.
_CLUSTER_SPAN_BLOCKS = 20
# The note is a pointer, not a list: more than two would crowd the result.
_MAX_NOTED_RECORDS = 2

SEARCH_HIT_HISTORY_KEY = "search_hit_history"
READ_RECORD_IDS_KEY = "read_record_ids"


@dataclass
class RecordHits:
    searches: int = 0
    blocks: set[int] = field(default_factory=set)


def observe_search(
    state: dict[str, Any], units: Iterable[Mapping[str, Any]], records: Mapping[str, Any],
) -> list[str]:
    """Count this search for every record whose hits it shows; returns their
    record ids in result order. Neighbours are context, not hits."""
    history: dict[str, RecordHits] = state.setdefault(SEARCH_HIT_HISTORY_KEY, {})
    surfaced: dict[str, set[int]] = {}
    for unit in units:
        if unit.get(NEIGHBOUR_KEY):
            continue
        record = records.get(str(unit.get("virtual_record_id") or "")) or {}
        record_id = record.get("id")
        if not record_id:
            continue
        blocks = surfaced.setdefault(str(record_id), set())
        index = unit.get("block_index")
        if isinstance(index, int):
            blocks.add(index)
    for record_id, blocks in surfaced.items():
        hits = history.setdefault(record_id, RecordHits())
        hits.searches += 1
        hits.blocks |= blocks
    return list(surfaced)


def mark_read(state: dict[str, Any], record_ids: Iterable[str]) -> None:
    state.setdefault(READ_RECORD_IDS_KEY, set()).update(str(rid) for rid in record_ids)


def cluster_start(blocks: Iterable[int]) -> int:
    """First block of the ``_CLUSTER_SPAN_BLOCKS`` window holding the most hits
    (the earliest such window on a tie); 0 when there are none."""
    ordered = sorted(set(blocks))
    best_start, best_count, end = 0, 0, 0
    for start, first in enumerate(ordered):
        end = max(end, start)
        while end + 1 < len(ordered) and ordered[end + 1] - first < _CLUSTER_SPAN_BLOCKS:
            end += 1
        if end - start + 1 > best_count:
            best_start, best_count = first, end - start + 1
    return best_start


def repeat_hit_note(
    state: dict[str, Any],
    surfaced: list[str],
    *,
    record_id_shortener: RecordIdShortener | None = None,
) -> str:
    """The note for records in ``surfaced`` seen in ``REPEAT_HIT_SEARCHES`` or
    more searches and never read, or "" -- also when this request cannot
    call the fetch tool."""
    if not state.get(FETCH_RECORD_GRANTED_KEY):
        return ""
    history: dict[str, RecordHits] = state.get(SEARCH_HIT_HISTORY_KEY) or {}
    read: set[str] = state.get(READ_RECORD_IDS_KEY) or set()
    repeated = sorted(
        (
            rid for rid in surfaced
            if rid not in read and history.get(rid, RecordHits()).searches >= REPEAT_HIT_SEARCHES
        ),
        key=lambda rid: -history[rid].searches,
    )[:_MAX_NOTED_RECORDS]
    lines = []
    for record_id in repeated:
        hits = history[record_id]
        start = cluster_start(hits.blocks)
        label = (
            record_id_shortener.shorten_if_known(record_id)
            if record_id_shortener is not None else record_id
        )
        lines.append(
            f"Note: record {label} has come up in {hits.searches} searches in this "
            f"conversation turn and has not been read. Its matches cluster from block "
            f"{start}; {FETCH_RECORD_TOOL_NAME} with record_ids=[\"{label}\"] and "
            f"start_block={start} reads that part in order, which another rephrased "
            "search is unlikely to do."
        )
        logger.info(
            "repeat_hit_note: record_id=%s searches=%d start_block=%d reason=repeat_unread",
            record_id, hits.searches, start,
        )
    return "\n".join(lines) + "\n\n" if lines else ""


__all__ = [
    "FETCH_RECORD_GRANTED_KEY",
    "READ_RECORD_IDS_KEY",
    "REPEAT_HIT_SEARCHES",
    "SEARCH_HIT_HISTORY_KEY",
    "RecordHits",
    "cluster_start",
    "mark_read",
    "observe_search",
    "repeat_hit_note",
]
