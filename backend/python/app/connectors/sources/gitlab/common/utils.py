"""Shared utility helpers for the GitLab connector."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.blocks import BlockGroup


def wire_block_group_parent_children(block_groups: list[BlockGroup]) -> None:
    """Set parent.children.block_group_ranges from each child's parent_index.

    Keeps existing block_ranges on the parent when present.
    """
    from app.models.blocks import BlockGroupChildren

    children_map: dict[int, list[int]] = defaultdict(list)
    for bg in block_groups:
        if bg.parent_index is not None:
            children_map[bg.parent_index].append(bg.index)

    for bg in block_groups:
        child_indices = children_map.get(bg.index)
        if not child_indices:
            continue
        wired = BlockGroupChildren.from_indices(block_group_indices=sorted(child_indices))
        if bg.children is None:
            bg.children = wired
        else:
            bg.children.block_group_ranges = wired.block_group_ranges


def parse_item_id_from_url(url: str) -> int:
    """Return the numeric item ID from a GitLab web URL.

    GitLab uses two URL shapes depending on version / feature flags:
      - Classic issues:  …/-/issues/<id>
      - Work items:      …/-/work_items/<id>
      - Merge requests:  …/-/merge_requests/<id>

    Raises ``ValueError`` if no recognisable numeric ID can be found.
    """
    markers = ("issues", "work_items", "merge_requests")
    parts = url.rstrip("/").split("/")
    for marker in markers:
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                return int(parts[idx + 1])
    raise ValueError(f"Cannot parse item ID from GitLab URL: {url}")
