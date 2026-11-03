"""The title of a table: the short lines set just above its first row.

Read by the vector store, which embeds a table's rows and summary under it, and
by reconciliation, which hashes it with them so a changed heading re-embeds them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.models.blocks import BlockType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.models.blocks import Block, BlockGroup

_TITLE_MAX_CHARS = 150
_TITLE_LINES = 2
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}$", re.IGNORECASE)


def table_titles(blocks: Sequence[Block], groups: Sequence[BlockGroup]) -> dict[int, str]:
    """Table group index -> the short lines set above its first row on the same page
    ("Consolidated Balance Sheets", "(in millions)").

    A table's rows are embedded as sentences that never name the statement they
    belong to, so a search for "balance sheet total current assets" had to match a
    bare sentence; prefixing the title puts the statement's words in every row.
    """
    first_row: dict[int, int] = {}
    for position, block in enumerate(blocks):
        if block.type == BlockType.TABLE_ROW and block.parent_index is not None:
            first_row.setdefault(block.parent_index, position)
    titles: dict[int, str] = {}
    for group in groups:
        start = first_row.get(group.index)
        page = group.citation_metadata.page_number if group.citation_metadata else None
        if start is None or page is None:
            continue
        lines: list[str] = []
        for block in reversed(blocks[max(0, start - 6):start]):
            text = block.data.strip() if isinstance(block.data, str) else ""
            same_page = block.citation_metadata and block.citation_metadata.page_number == page
            if block.type != BlockType.TEXT or not same_page or not text or len(text) > _TITLE_MAX_CHARS:
                break
            if not _PAGE_NUMBER.match(text):
                lines.insert(0, " ".join(text.split()))
            if len(lines) >= _TITLE_LINES:
                break
        if lines:
            titles[group.index] = " ".join(lines)
    return titles
