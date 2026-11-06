"""Neighbouring context blocks for the units that survived ranking.

Neighbours are context, not candidates: they are added only around units
already chosen, so they never take a slot from a real hit.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.models.blocks import BlockType
from app.modules.retrieval.context.units import (
    NEIGHBOUR_OF_KEY,
    Unit,
    neighbour_anchors,
    table_block_span,
    takes_neighbours,
    unit_block_indices,
)
from app.utils.chat_helpers import get_enhanced_metadata

logger = logging.getLogger(__name__)

# Marks a neighbour that closes a small gap between two shown spans.
GAP_FILL_KEY = "is_gap_fill"
# A sentence or two between two shown passages usually carries the link
# between them; a wider gap is a different part of the document.
MAX_FILLED_GAP = 2
# A record whose hits are scattered would otherwise pull much of its body in
# through gaps, on top of the neighbours every hit already brings.
MAX_GAP_BLOCKS_PER_RECORD = 4


def expand_neighbours(
    units: list[Unit],
    virtual_record_id_to_result: dict[str, Any],
    *,
    limit: int | None = None,
) -> list[Unit]:
    """``units`` followed by the text blocks just before and after each one.

    ``units`` must be in relevance order: with ``limit`` the best units get
    their neighbours first. Only plain text neighbours are added, and never a
    block some unit already renders. A table's neighbours are the blocks
    either side of the whole table, where its caption and notes are. Each
    neighbour names the units it was added for (``NEIGHBOUR_OF_KEY``), so it
    can be left out with them.
    """
    shown = {
        (unit.get("virtual_record_id"), index)
        for unit in units
        for index in unit_block_indices(unit)
    }
    added: dict[tuple[Any, int], Unit] = {}
    for unit in units:
        if not takes_neighbours(unit):
            continue
        vrid = unit.get("virtual_record_id")
        indices = list(unit_block_indices(unit))
        if not vrid or not indices:
            continue
        record = virtual_record_id_to_result.get(vrid)
        first, last = table_block_span(record, unit) or (min(indices), max(indices))
        anchor = min(indices)
        for index in (first - 1, last + 1):
            key = (vrid, index)
            if key in added:
                if anchor not in added[key][NEIGHBOUR_OF_KEY]:
                    added[key][NEIGHBOUR_OF_KEY].append(anchor)
                continue
            if (limit is not None and len(added) >= limit) or key in shown:
                continue
            neighbour = _text_neighbour(record, vrid, index)
            if neighbour is not None:
                neighbour[NEIGHBOUR_OF_KEY] = [anchor]
                added[key] = neighbour
    return [*units, *added.values()]


def fill_small_gaps(
    units: list[Unit], virtual_record_id_to_result: dict[str, Any]
) -> list[Unit]:
    """``units`` followed by the text blocks that close gaps of at most
    ``MAX_FILLED_GAP`` blocks between two shown spans of one record.

    ``units`` must be in relevance order, passages before neighbours, as
    ``expand_neighbours`` returns them. A span is everything one unit renders,
    so the rows a table left out between its matched rows are not a gap. A gap
    is filled whole or not at all, only when every block in it is plain text,
    smallest gaps first, and at most ``MAX_GAP_BLOCKS_PER_RECORD`` blocks per
    record, counting fills already present, so filling twice adds nothing.
    Fills are neighbours of the less relevant side, so a budget drops them
    with it.
    """
    passage_rank: dict[tuple[Any, int], int] = {}
    for unit in units:
        indices = list(unit_block_indices(unit))
        if indices and neighbour_anchors(unit) is None:
            passage_rank.setdefault((unit.get("virtual_record_id"), min(indices)), len(passage_rank))

    spans: dict[Any, list[tuple[int, int, tuple[int, int] | None]]] = {}
    already_filled: Counter[Any] = Counter()
    for unit in units:
        vrid = unit.get("virtual_record_id")
        indices = list(unit_block_indices(unit))
        if not vrid or not indices:
            continue
        anchors = neighbour_anchors(unit)
        candidates = [min(indices)] if anchors is None else anchors
        sides = [(passage_rank[(vrid, a)], a) for a in candidates if (vrid, a) in passage_rank]
        spans.setdefault(vrid, []).append((min(indices), max(indices), min(sides) if sides else None))
        if unit.get(GAP_FILL_KEY):
            already_filled[vrid] += 1

    fills: list[Unit] = []
    capped = 0
    for vrid, record_spans in spans.items():
        room = MAX_GAP_BLOCKS_PER_RECORD - already_filled[vrid]
        for size, start, side in _small_gaps(record_spans):
            if side is None:
                continue
            if size > room:
                capped += 1
                continue
            record = virtual_record_id_to_result.get(vrid)
            gap = [_text_neighbour(record, vrid, index) for index in range(start, start + size)]
            if any(block is None for block in gap):
                continue
            for block in gap:
                block[GAP_FILL_KEY] = True
                block[NEIGHBOUR_OF_KEY] = [side[1]]
            fills.extend(gap)
            room -= size
    if fills or capped:
        logger.info(
            "fill_small_gaps: filled_blocks=%d records=%d capped_gaps=%d",
            len(fills), len({f["virtual_record_id"] for f in fills}), capped,
        )
    return [*units, *fills]


def _small_gaps(
    spans: list[tuple[int, int, tuple[int, int] | None]],
) -> list[tuple[int, int, tuple[int, int] | None]]:
    """``(size, first_index, side)`` of each gap of 1..MAX_FILLED_GAP blocks
    between merged spans, smallest first. ``side`` is the less relevant
    side's ``(rank, anchor)``, or either side's when only one is known."""
    merged: list[list[Any]] = []
    for low, high, side in sorted(spans, key=lambda span: (span[0], span[1])):
        if merged and low <= merged[-1][1] + 1:
            last = merged[-1]
            last[1] = max(last[1], high)
            last[2] = min((s for s in (last[2], side) if s is not None), default=None)
        else:
            merged.append([low, high, side])
    gaps = []
    for left, right in zip(merged, merged[1:]):
        size = right[0] - left[1] - 1
        if 1 <= size <= MAX_FILLED_GAP:
            sides = [s for s in (left[2], right[2]) if s is not None]
            gaps.append((size, left[1] + 1, max(sides) if sides else None))
    return sorted(gaps, key=lambda gap: (gap[0], gap[1]))


def _text_neighbour(record: object, vrid: str, index: int) -> Unit | None:
    if not isinstance(record, dict) or index < 0:
        return None
    blocks = (record.get("block_containers") or {}).get("blocks") or []
    if index >= len(blocks) or not isinstance(blocks[index], dict):
        return None
    block = blocks[index]
    if block.get("type") != BlockType.TEXT.value:
        return None
    return {
        "content": block.get("data", ""),
        "block_type": BlockType.TEXT.value,
        "metadata": get_enhanced_metadata(record, block, {}),
        "virtual_record_id": vrid,
        "block_index": index,
        "citationType": "vectordb|document",
    }
