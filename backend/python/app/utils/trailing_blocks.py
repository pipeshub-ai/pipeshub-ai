"""Resolving a stored block by local index.

A record may store the blocks of context pages in ``trailing_context``, beside ``blocks``. Their local index is
``len(blocks) + j``, the address the neighbour expansion already mints, so a citation of such a block resolves to it.
Every resolver of a block by local index reads through here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def addressable_blocks(containers: Mapping[str, Any] | None) -> list[Any]:
    """The blocks a local index can name: ``blocks`` then ``trailing_context``. The very list for a record
    without trailing context, so such a record resolves exactly as before."""
    if not containers:
        return []
    blocks = containers.get("blocks") or []
    trailing = containers.get("trailing_context")
    if not isinstance(trailing, list) or not trailing:
        return blocks
    return [*blocks, *trailing]
