"""Unread top-ranked candidates (``ANSWER_CANDIDATE_READING``).

A search shows its records best match first, so the records at the top of a search's results are the ones the
system ranked closest to what the agent asked for. When the agent drafts an answer from other records and never opens
one of those top records, the draft is held once and the agent reads them before the answer is final.

This module keeps the per-request search ranks and picks the candidates; ``hooks/candidate_reading.py`` sends the
request. No model call picks the candidates: they come from the searches' own order.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from app.utils.env_utils import env_bool

ANSWER_CANDIDATE_READING_ENV_VAR = "ANSWER_CANDIDATE_READING"
# record_id -> {"rank": best position among the records a search showed (1 = first), "name": record name}
SEARCH_RECORD_RANKS_KEY = "search_record_ranks"
# Positions within one search's shown records that count as "top".
TOP_RANK = 5
MAX_CANDIDATES = 3


def candidate_reading_enabled() -> bool:
    return env_bool(ANSWER_CANDIDATE_READING_ENV_VAR, False)


@dataclass(frozen=True)
class TopCandidate:
    record_id: str
    name: str
    rank: int


def _record_name(record: Mapping[str, Any]) -> str:
    return str(record.get("record_name") or record.get("recordName") or record.get("name") or "")


def note_search_ranks(
    state: MutableMapping[str, Any],
    ranked_units: Sequence[Mapping[str, Any]],
    shown_records: Mapping[str, Mapping[str, Any]],
) -> None:
    """Record each shown record's best position across this request's searches.

    ``ranked_units`` are the shown blocks in result order; a record's position is the order of its first block.
    ``shown_records`` maps virtual record id to the record (with its ``id``).
    """
    stored = state.get(SEARCH_RECORD_RANKS_KEY)
    ranks: dict[str, dict[str, Any]]
    if isinstance(stored, dict):
        ranks = cast("dict[str, dict[str, Any]]", stored)
    else:
        ranks = state[SEARCH_RECORD_RANKS_KEY] = {}
    seen: set[str] = set()
    for unit in ranked_units:
        vrid = unit.get("virtual_record_id")
        if not vrid or vrid in seen:
            continue
        seen.add(vrid)
        record = shown_records.get(vrid)
        record_id = record.get("id") if isinstance(record, Mapping) else None
        if not record_id:
            continue
        position = len(seen)
        previous = ranks.get(str(record_id))
        if previous is None or position < previous.get("rank", position + 1):
            ranks[str(record_id)] = {
                "rank": position, "name": _record_name(record) if isinstance(record, Mapping) else "",
            }


def unread_top_candidates(
    tool_state: Mapping[str, Any], cited_record_ids: Collection[str], opened_record_ids: Collection[str],
) -> list[TopCandidate]:
    """Records that ranked in the top ``TOP_RANK`` of a search, were neither opened nor cited; best rank first."""
    stored = tool_state.get(SEARCH_RECORD_RANKS_KEY)
    if not isinstance(stored, Mapping):
        return []
    excluded = set(cited_record_ids) | set(opened_record_ids)
    out: list[TopCandidate] = []
    for record_id, item in cast("Mapping[str, object]", stored).items():
        entry = cast("Mapping[str, Any]", item) if isinstance(item, Mapping) else None
        rank = entry.get("rank") if entry is not None else None
        if entry is None or not isinstance(rank, int) or rank > TOP_RANK or record_id in excluded:
            continue
        out.append(TopCandidate(str(record_id), str(entry.get("name") or record_id), rank))
    out.sort(key=lambda candidate: (candidate.rank, candidate.name))
    return out[:MAX_CANDIDATES]
