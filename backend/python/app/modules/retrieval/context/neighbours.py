"""Neighbouring context blocks for the units that survived ranking.

Neighbours are context, not candidates: they are added only around units
already chosen, so they never take a slot from a real hit.
"""

from __future__ import annotations

from typing import Any

from app.models.blocks import BlockType
from app.modules.retrieval.context.units import (
    NEIGHBOUR_OF_KEY,
    Unit,
    table_block_span,
    takes_neighbours,
    unit_block_indices,
)
from app.utils.chat_helpers import SPAN_BLOCK_INDICES_KEY, get_enhanced_metadata


def expand_neighbours(
    units: list[Unit],
    virtual_record_id_to_result: dict[str, Any],
    *,
    limit: int | None = None,
) -> list[Unit]:
    """``units`` followed by the rest of the chunk each was found through, and
    the text blocks just before and after that.

    ``units`` must be in relevance order: with ``limit`` the best units get
    their neighbours first. Only plain text neighbours are added, and never a
    block some unit already renders. A table's neighbours are the blocks
    either side of the whole table, where its caption and notes are. Each
    neighbour names the units it was added for (``NEIGHBOUR_OF_KEY``), so it
    can be left out with them. A unit's chunk blocks are part of its hit, so
    ``limit`` does not count them.
    """
    shown = {
        (unit.get("virtual_record_id"), index)
        for unit in units
        for index in unit_block_indices(unit)
    }
    added: dict[tuple[Any, int], Unit] = {}
    neighbour_count = 0

    def add(record: object, vrid: str, index: int, anchor: int) -> bool:
        key = (vrid, index)
        if key in added:
            if anchor not in added[key][NEIGHBOUR_OF_KEY]:
                added[key][NEIGHBOUR_OF_KEY].append(anchor)
            return False
        if key in shown:
            return False
        neighbour = _text_neighbour(record, vrid, index)
        if neighbour is None:
            return False
        neighbour[NEIGHBOUR_OF_KEY] = [anchor]
        added[key] = neighbour
        return True

    for unit in units:
        vrid = unit.get("virtual_record_id")
        hit = list(unit_block_indices(unit))
        if not vrid or not hit:
            continue
        span = [index for index in unit.get(SPAN_BLOCK_INDICES_KEY) or () if isinstance(index, int)]
        record = virtual_record_id_to_result.get(vrid)
        anchor = min(hit)
        # The search matched the chunk as a whole, so all of it is the hit.
        for index in span:
            add(record, vrid, index, anchor)
        if not takes_neighbours(unit):
            continue
        indices = [*hit, *span]
        first, last = table_block_span(record, unit) or (min(indices), max(indices))
        for index in (first - 1, last + 1):
            if limit is not None and neighbour_count >= limit and (vrid, index) not in added:
                continue
            if add(record, vrid, index, anchor):
                neighbour_count += 1
    return [*units, *added.values()]


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
