"""Read-only views over the renderable units ``get_flattened_results`` produces.

A unit is one thing the model reads and cites: a block, a list or section
group with its children, a table with its matched rows, or a record summary.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import group_child_results

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

Unit = dict[str, Any]

# On a neighbour unit: the block indices of the units it was added for.
NEIGHBOUR_OF_KEY = "neighbour_of"


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _score_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(value):
        return float(value)
    return None


def unit_children(unit: Mapping[str, Any]) -> list[Unit]:
    """Child blocks of a group or table unit; empty for a plain block."""
    return [child for child in group_child_results(dict(unit)) or () if isinstance(child, dict)]


def unit_block_indices(unit: Mapping[str, Any]) -> Iterator[int]:
    """Every block index a unit renders: its own and its children's."""
    own = _int_or_none(unit.get("block_index"))
    if own is not None:
        yield own
    for child in unit_children(unit):
        index = _int_or_none(child.get("block_index"))
        if index is not None:
            yield index


def unit_score(unit: Mapping[str, Any]) -> float | None:
    """Retrieval score of a unit.

    A table assembled from matched rows carries no score of its own, so it
    ranks by its best row.
    """
    score = _score_or_none(unit.get("score"))
    if score is not None:
        return score
    child_scores = [
        score for child in unit_children(unit)
        if (score := _score_or_none(child.get("score"))) is not None
    ]
    return max(child_scores) if child_scores else None


def takes_neighbours(unit: Mapping[str, Any]) -> bool:
    """Whether the text blocks either side of this unit are useful context for it.

    The block kinds ``get_flattened_results`` used to add neighbours for: a
    standalone block or a table. A list or section group carries its own
    context, and a record summary has no position in the document.
    """
    return unit.get("block_type") in (
        BlockType.TEXT.value,
        BlockType.CODE.value,
        BlockType.IMAGE.value,
        GroupType.TABLE.value,
    )


def neighbour_anchors(unit: Mapping[str, Any]) -> list[int] | None:
    """Block indices of the units ``unit`` is a neighbour of; None for a unit
    that is not a neighbour."""
    anchors = unit.get(NEIGHBOUR_OF_KEY)
    return list(anchors) if isinstance(anchors, list) else None


def is_orphan_neighbour(unit: Mapping[str, Any], alongside: Iterable[Mapping[str, Any]]) -> bool:
    """Whether ``unit`` is a neighbour none of whose units are in ``alongside``.

    A neighbour is context for a passage; shown without that passage it is
    just an unrelated paragraph of the record.
    """
    anchors = neighbour_anchors(unit)
    if anchors is None:
        return False
    vrid = unit.get("virtual_record_id")
    passages = {
        index
        for other in alongside
        if other.get("virtual_record_id") == vrid and neighbour_anchors(other) is None
        for index in unit_block_indices(other)
    }
    return not any(anchor in passages for anchor in anchors)


def without_orphan_neighbours(units: list[Unit]) -> list[Unit]:
    """``units`` minus the neighbours whose units are not among them."""
    return [unit for unit in units if not is_orphan_neighbour(unit, units)]


def table_block_span(record: object, unit: Mapping[str, Any]) -> tuple[int, int] | None:
    """First and last block of the table a table unit belongs to, or None.

    A table unit lists only its matched rows; the caption above the table and
    the note below it sit next to the table's own range, not next to those rows.
    """
    if unit.get("block_type") != GroupType.TABLE.value or not isinstance(record, dict):
        return None
    containers = record.get("block_containers") or {}
    groups = containers.get("block_groups") or []
    group_index = _table_group_index(unit, containers.get("blocks") or [])
    if group_index is None or not 0 <= group_index < len(groups) or not isinstance(groups[group_index], dict):
        return None
    children = groups[group_index].get("children")
    indices: list[int] = []
    if isinstance(children, dict):
        for span in children.get("block_ranges") or ():
            if isinstance(span, dict):
                indices.extend(
                    index for key in ("start", "end")
                    if (index := _int_or_none(span.get(key))) is not None
                )
    elif isinstance(children, list):
        indices = [
            index for child in children
            if isinstance(child, dict) and (index := _int_or_none(child.get("block_index"))) is not None
        ]
    return (min(indices), max(indices)) if indices else None


def _table_group_index(unit: Mapping[str, Any], blocks: list[Any]) -> int | None:
    """The table's block group: the unit's own, else its rows' parent."""
    group_index = _int_or_none(unit.get("block_group_index"))
    if group_index is not None:
        return group_index
    for index in unit_block_indices(unit):
        if 0 <= index < len(blocks) and isinstance(blocks[index], dict):
            parent = _int_or_none(blocks[index].get("parent_index"))
            if parent is not None:
                return parent
    return None
