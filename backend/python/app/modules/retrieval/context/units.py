"""Read-only views over the renderable units ``get_flattened_results`` produces.

A unit is one thing the model reads and cites: a block, a list or section
group with its children, a table with its matched rows, or a record summary.
Groups and tables carry their children as ``content = (header, [child, ...])``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models.blocks import BlockType, GroupType

if TYPE_CHECKING:
    from collections.abc import Iterator

Unit = dict[str, Any]


def unit_children(unit: Unit) -> list[Unit]:
    """Child blocks of a group or table unit; empty for a plain block."""
    content = unit.get("content")
    if isinstance(content, tuple) and len(content) == 2 and isinstance(content[1], list):
        return [child for child in content[1] if isinstance(child, dict)]
    return []


def unit_block_indices(unit: Unit) -> Iterator[int]:
    """Every block index a unit renders: its own and its children's."""
    own = unit.get("block_index")
    if isinstance(own, int):
        yield own
    for child in unit_children(unit):
        index = child.get("block_index")
        if isinstance(index, int):
            yield index


def unit_score(unit: Unit) -> float | None:
    """Retrieval score of a unit.

    A table assembled from matched rows carries no score of its own, so it
    ranks by its best row.
    """
    score = unit.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return float(score)
    child_scores = [
        float(child["score"])
        for child in unit_children(unit)
        if isinstance(child.get("score"), (int, float)) and not isinstance(child.get("score"), bool)
    ]
    return max(child_scores) if child_scores else None


def is_table(unit: Unit) -> bool:
    return unit.get("block_type") == GroupType.TABLE.value


def takes_neighbours(unit: Unit) -> bool:
    """Whether the blocks either side of this unit are useful context for it.

    Standalone blocks and tables read better with the sentence before and
    after them; a list or section group already carries its own context, and
    a record summary has no position in the document.
    """
    return unit.get("block_type") in (
        BlockType.TEXT.value,
        BlockType.CODE.value,
        BlockType.IMAGE.value,
    ) or is_table(unit)
