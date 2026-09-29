"""Neighbouring context blocks for units that survived ranking.

Neighbours are context, not candidates: they are added only around units
already chosen, so they never take a slot from a real hit.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.models.blocks import BlockType
from app.modules.retrieval.context.ranking import UNIT_RANK_KEY
from app.modules.retrieval.context.units import (
    Unit,
    takes_neighbours,
    unit_block_indices,
)
from app.utils.chat_helpers import get_enhanced_metadata

logger = logging.getLogger(__name__)

# Marks a unit added as context around a hit rather than found by the search.
NEIGHBOUR_KEY = "is_neighbour"
# Marks a neighbour that closes a small gap between two shown spans.
GAP_FILL_KEY = "is_gap_fill"
# A sentence or two between two shown passages usually carries the link
# between them; a wider gap is a different part of the document.
MAX_FILLED_GAP = 2
# A record whose hits are scattered would otherwise pull much of its body in
# through gaps, on top of the neighbours every hit already brings.
MAX_GAP_BLOCKS_PER_RECORD = 4


def expand_neighbours(
    units: list[Unit], virtual_record_id_to_result: dict[str, Any]
) -> list[Unit]:
    """``units`` followed by the text blocks just before and after each one.

    Only plain text neighbours are added, and never a block some unit already
    renders.
    """
    shown = {
        (unit.get("virtual_record_id"), index)
        for unit in units
        for index in unit_block_indices(unit)
    }
    neighbours: list[Unit] = []
    for unit in units:
        if not takes_neighbours(unit):
            continue
        vrid = unit.get("virtual_record_id")
        indices = list(unit_block_indices(unit))
        if not vrid or not indices:
            continue
        for index in (min(indices) - 1, max(indices) + 1):
            if (vrid, index) in shown:
                continue
            neighbour = _text_neighbour(virtual_record_id_to_result.get(vrid), vrid, index)
            if neighbour is not None:
                # It goes with its hit: kept or dropped with it under a budget.
                if UNIT_RANK_KEY in unit:
                    neighbour[UNIT_RANK_KEY] = unit[UNIT_RANK_KEY]
                shown.add((vrid, index))
                neighbours.append(neighbour)
    return [*units, *neighbours]


def fill_small_gaps(
    units: list[Unit], virtual_record_id_to_result: dict[str, Any]
) -> list[Unit]:
    """``units`` followed by the text blocks that close gaps of at most
    ``MAX_FILLED_GAP`` blocks between two shown spans of one record.

    A span is everything one unit renders, so the rows a table left out
    between its matched rows are not a gap. A gap is filled whole or not at
    all, only when every block in it is plain text, smallest gaps first, and
    at most ``MAX_GAP_BLOCKS_PER_RECORD`` blocks per record, counting fills
    already present -- so filling twice adds nothing. Fills are neighbours:
    they rank with the less relevant side, so a budget drops them with it.
    """
    spans: dict[str, list[tuple[int, int, int | None]]] = {}
    already_filled: Counter[str] = Counter()
    for unit in units:
        vrid = unit.get("virtual_record_id")
        indices = list(unit_block_indices(unit))
        if not vrid or not indices:
            continue
        spans.setdefault(vrid, []).append((min(indices), max(indices), _rank(unit)))
        if unit.get(GAP_FILL_KEY):
            already_filled[vrid] += 1

    fills: list[Unit] = []
    capped = 0
    for vrid, record_spans in spans.items():
        room = MAX_GAP_BLOCKS_PER_RECORD - already_filled[vrid]
        for size, start, rank in _small_gaps(record_spans):
            if size > room:
                capped += 1
                continue
            gap = [
                _text_neighbour(virtual_record_id_to_result.get(vrid), vrid, index)
                for index in range(start, start + size)
            ]
            if any(block is None for block in gap):
                continue
            for block in gap:
                block[GAP_FILL_KEY] = True
                if rank is not None:
                    block[UNIT_RANK_KEY] = rank
            fills.extend(gap)
            room -= size
    if fills or capped:
        logger.info(
            "fill_small_gaps: filled_blocks=%d records=%d capped_gaps=%d",
            len(fills), len({f["virtual_record_id"] for f in fills}), capped,
        )
    return [*units, *fills]


def _small_gaps(
    spans: list[tuple[int, int, int | None]],
) -> list[tuple[int, int, int | None]]:
    """``(size, first_index, rank)`` of each gap of 1..MAX_FILLED_GAP blocks
    between merged spans, smallest first. ``rank`` is the less relevant side's."""
    merged: list[list[Any]] = []
    for low, high, rank in sorted(spans, key=lambda span: (span[0], span[1])):
        if merged and low <= merged[-1][1] + 1:
            last = merged[-1]
            last[1] = max(last[1], high)
            last[2] = _best(last[2], rank)
        else:
            merged.append([low, high, rank])
    gaps = []
    for left, right in zip(merged, merged[1:]):
        size = right[0] - left[1] - 1
        if 1 <= size <= MAX_FILLED_GAP:
            ranks = [r for r in (left[2], right[2]) if r is not None]
            gaps.append((size, left[1] + 1, max(ranks) if len(ranks) == 2 else None))
    return sorted(gaps, key=lambda gap: (gap[0], gap[1]))


def _rank(unit: Unit) -> int | None:
    rank = unit.get(UNIT_RANK_KEY)
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else None


def _best(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    return a if b is None else min(a, b)


def _text_neighbour(record: dict[str, Any] | None, vrid: str, index: int) -> Unit | None:
    if not record or index < 0:
        return None
    blocks = (record.get("block_containers") or {}).get("blocks") or []
    if index >= len(blocks):
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
        NEIGHBOUR_KEY: True,
    }
