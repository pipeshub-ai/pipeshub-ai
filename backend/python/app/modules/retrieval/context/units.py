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
    from collections.abc import Iterator, Mapping

Unit = dict[str, Any]


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
