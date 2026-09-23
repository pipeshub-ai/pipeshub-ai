"""Neighbouring context blocks for units that survived ranking.

Neighbours are context, not candidates: they are added only around units
already chosen, so they never take a slot from a real hit.
"""

from __future__ import annotations

from typing import Any

from app.models.blocks import BlockType
from app.modules.retrieval.context.units import (
    Unit,
    takes_neighbours,
    unit_block_indices,
)
from app.utils.chat_helpers import get_enhanced_metadata


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
                shown.add((vrid, index))
                neighbours.append(neighbour)
    return [*units, *neighbours]


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
    }
