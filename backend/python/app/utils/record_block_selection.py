"""Which blocks of an over-budget record the model should actually get.

A record that does not fit has to lose something. Losing the tail — "the first
N blocks that fit" — is the worst possible choice for the questions this tool
exists to answer: "does this contract mention indemnity", "what are the risks
in this report". The answer is rarely on page one, and a positional window
gives no signal that it was missed.

So when a record exceeds the remaining budget, its blocks are ranked against
what the model is looking for, each hit is widened by its neighbours, and the
selection is rendered in document order with explicit gaps. Sequential reading
is still available: `start_block` walks a record straight through.

Nothing here is new machinery. `RetrievalService.search_with_filters` already
scopes a semantic search to specific `virtualRecordId`s **and runs its own
permission check**, and `virtualRecordId` is an indexed keyword field in every
vector backend. This module only asks it the right question and turns the
answer into a set of block indices.

Failure is never fatal: no retrieval service, an empty result, or an exception
all return `None`, and the caller falls back to the positional window. A search
hiccup must not turn into a failed fetch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.utils.render_budget import RenderBudget

logger = logging.getLogger(__name__)

# Blocks either side of a hit that come along with it. A matched passage whose
# neighbours are missing reads as a fragment: the sentence that defines the
# term, or the row the total belongs to, is usually adjacent. The search path
# widens hits the same way (`adjacent_chunks` in `get_flattened_results`).
NEIGHBOUR_SPAN = 1

# How many ranked hits to ask for. Generous, because the budget — not this
# number — decides how many survive; asking for too few is what would lose a
# relevant block.
DEFAULT_HIT_LIMIT = 60

# Rough cost of a block whose text cannot be measured (an image, a group whose
# content is assembled at render time). Deliberately not zero: assuming free
# is how a selection overruns its budget.
_UNKNOWN_BLOCK_CHARS = 400


def estimate_block_chars(block: dict[str, Any]) -> int:
    """Roughly how many characters this block will render to.

    An estimate, not a measurement: the renderer adds markers and headers this
    cannot know about. It only has to be good enough to decide what fits.
    """
    data = block.get("data")
    if isinstance(data, str):
        return len(data)
    if isinstance(data, dict):
        for key in ("row_natural_language_text", "text", "description"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return len(value)
        # An image block's `uri` is base64 that never reaches the text.
        return _UNKNOWN_BLOCK_CHARS
    return _UNKNOWN_BLOCK_CHARS


def estimate_record_chars(record: dict[str, Any]) -> int:
    """Rough rendered size of a whole record, used to decide whether selection
    is needed at all. Cheap: no rendering, no I/O."""
    containers = record.get("block_containers") or {}
    blocks = containers.get("blocks", []) if isinstance(containers, dict) else []
    return sum(
        estimate_block_chars(b)
        for b in blocks
        if isinstance(b, dict) and b.get("parent_block_index") is None
    )


def build_selection_query(user_query: str, reason: str) -> str:
    """What to rank the record's blocks against.

    Both halves matter. The user's question is the goal; the model's `reason`
    is what it believes it needs from *this* record, which is often narrower
    ("check the indemnity clause"). Until now `reason` was only ever logged.
    """
    parts = [p.strip() for p in (user_query, reason) if p and p.strip()]
    # Deduplicate: the model frequently echoes the question back as its reason.
    seen: set[str] = set()
    unique = [p for p in parts if not (p.lower() in seen or seen.add(p.lower()))]
    return " ".join(unique)


def _block_indices(record: dict[str, Any]) -> list[int]:
    containers = record.get("block_containers") or {}
    blocks = containers.get("blocks", []) if isinstance(containers, dict) else []
    return sorted(
        b.get("index", 0)
        for b in blocks
        if isinstance(b, dict) and b.get("parent_block_index") is None
    )


def _hits_from_search(response: object, virtual_record_id: str) -> list[int]:
    """Block indices from a search response, best first.

    Results without a `blockIndex` are skipped rather than guessed at: a wrong
    index would pull an unrelated block into the selection.
    """
    if not isinstance(response, dict):
        return []
    ordered: list[int] = []
    for result in response.get("searchResults") or []:
        if not isinstance(result, dict):
            continue
        metadata = result.get("metadata") or {}
        if metadata.get("virtualRecordId") not in (None, virtual_record_id):
            continue
        index = metadata.get("blockIndex")
        if isinstance(index, int) and index not in ordered:
            ordered.append(index)
    return ordered


def _widen(hits: list[int], available: list[int], span: int) -> list[list[int]]:
    """Each hit paired with its neighbours, still in hit order.

    Returns groups rather than a flat set so the caller can admit a hit *with*
    its context or not at all — half a neighbourhood is what makes a passage
    unreadable.
    """
    known = set(available)
    groups: list[list[int]] = []
    for hit in hits:
        group = [i for i in range(hit - span, hit + span + 1) if i in known]
        if group:
            groups.append(group)
    return groups


async def select_relevant_blocks(
    *,
    record: dict[str, Any],
    virtual_record_id: str | None,
    query: str,
    retrieval_service: Any,
    user_id: str,
    org_id: str,
    budget: "RenderBudget",
    hit_limit: int = DEFAULT_HIT_LIMIT,
    neighbour_span: int = NEIGHBOUR_SPAN,
) -> set[int] | None:
    """Block indices worth rendering for `query`, or None to fall back.

    `None` means "no opinion" — the caller should render positionally. It is
    returned for every failure mode as well as for a record small enough not to
    need selecting.
    """
    if not (virtual_record_id and query.strip() and retrieval_service and user_id and org_id):
        return None

    available = _block_indices(record)
    if not available:
        return None

    try:
        response = await retrieval_service.search_with_filters(
            queries=[query],
            user_id=user_id,
            org_id=org_id,
            limit=hit_limit,
            virtual_record_ids_from_tool=[virtual_record_id],
        )
    except Exception:
        logger.warning(
            "Block selection search failed for %s; falling back to a positional window",
            virtual_record_id, exc_info=True,
        )
        return None

    hits = _hits_from_search(response, virtual_record_id)
    if not hits:
        logger.debug("No ranked blocks for %s; falling back to a positional window", virtual_record_id)
        return None

    by_index = {
        b.get("index", 0): b
        for b in (record.get("block_containers") or {}).get("blocks", [])
        if isinstance(b, dict)
    }

    # Admit whole neighbourhoods, best first, until the next one would not fit.
    selected: set[int] = set()
    spent = 0
    room = budget.chars_remaining
    for group in _widen(hits, available, neighbour_span):
        fresh = [i for i in group if i not in selected]
        cost = sum(estimate_block_chars(by_index.get(i, {})) for i in fresh)
        if fresh and spent + cost > room:
            break
        selected.update(fresh)
        spent += cost

    if not selected:
        # Even the best neighbourhood does not fit; let the positional path
        # render what it can rather than returning an empty record.
        return None

    # Relevance decides what comes *first*, not how much comes at all. The
    # ranking returns a fixed number of hits, so without this the render was
    # capped at roughly `hit_limit × (2·span + 1)` blocks however much room
    # was left -- a 60-hit ranking used barely half of a 128k allowance and
    # dropped the rest of the document for no reason.
    #
    # The selected regions grow outward a block at a time, so the extra room
    # goes to the passages around what matched rather than to an arbitrary
    # slice elsewhere.
    spent = _grow_selection(selected, available, by_index, room=room, spent=spent)
    return selected


def _grow_selection(
    selected: set[int],
    available: list[int],
    by_index: dict[int, dict[str, Any]],
    *,
    room: int,
    spent: int,
) -> int:
    """Widen the selected regions until the allowance is used up.

    Grows symmetrically around what is already selected rather than appending
    a distant slice: a passage reads correctly when what surrounds it comes
    with it. Returns the characters spent.
    """
    known = set(available)
    while spent < room:
        frontier = sorted(
            index for index in known
            if index not in selected and ((index - 1) in selected or (index + 1) in selected)
        )
        if not frontier:
            break
        progressed = False
        for index in frontier:
            cost = estimate_block_chars(by_index.get(index, {}))
            if spent + cost > room:
                continue
            selected.add(index)
            spent += cost
            progressed = True
        if not progressed:
            break
    return spent


def describe_gaps(selected: set[int], available: list[int]) -> dict[int, str]:
    """Marker text keyed by the selected block index it precedes.

    The model has to know the document it is reading is not contiguous —
    otherwise "the report never mentions X" is a conclusion drawn from a
    filtered view it did not know was filtered.
    """
    markers: dict[int, str] = {}
    previous: int | None = None
    for index in available:
        if index not in selected:
            continue
        if previous is not None and index > previous + 1:
            markers[index] = _gap_text(previous + 1, index - 1)
        previous = index
    first = next((i for i in available if i in selected), None)
    if first is not None and first > available[0]:
        markers[first] = _gap_text(available[0], first - 1)
    return markers


def _gap_text(start: int, end: int) -> str:
    """What a skipped range says for itself.

    Names the range so the model can ask for it: a gap it cannot address is
    just an admission that something is missing.
    """
    missing = end - start + 1
    return (
        f"\n[… {missing} block{'s' if missing != 1 else ''} ({start}–{end}) not shown — "
        f"this record is too large to return in one read. To read this part, call "
        f"knowledgegraph__fetch_record again with start_block={start} …]\n\n"
    )


__all__ = [
    "DEFAULT_HIT_LIMIT",
    "NEIGHBOUR_SPAN",
    "build_selection_query",
    "describe_gaps",
    "estimate_block_chars",
    "estimate_record_chars",
    "select_relevant_blocks",
]
