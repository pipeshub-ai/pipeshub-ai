"""Digests of a revision's content: stage fingerprint inputs and the facts on a ``RecordView``."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from app.models.blocks import Block, BlocksContainer, BlockType, GroupType

# The block types classification reads (DocumentExtraction._prepare_content).
_TEXT_TYPES = frozenset({BlockType.TEXT, BlockType.TABLE_ROW, BlockType.CODE})
_TABLE_GROUPS = frozenset({GroupType.TABLE, GroupType.SHEET})


@dataclass(frozen=True)
class ContentFacts:
    text_digest: str
    blocks_digest: str
    text_chars: int
    has_tables: bool
    has_images: bool


def content_revision(content: bytes) -> str:
    """``contentRev``: identical source bytes are the same revision."""
    return hashlib.sha256(content).hexdigest()[:16]


def _digest(parts: Iterable[str]) -> str:
    sha = hashlib.sha256()
    for part in parts:
        sha.update(part.encode("utf-8"))
        # A separator no text contains, so ("ab", "c") and ("a", "bc") differ.
        sha.update(b"\x1f")
    return sha.hexdigest()[:32]


def _content_hash(data: object, existing: str | None) -> str:
    if existing:
        return existing
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def block_text(block: Block) -> str:
    """The text classification reads from a block."""
    data: object = block.data
    if isinstance(data, dict):
        key = "row_natural_language_text" if block.type == BlockType.TABLE_ROW else "text"
        data = cast(dict[str, object], data).get(key)
    if data is None:
        return ""
    return data if isinstance(data, str) else str(data)


def content_facts(container: BlocksContainer) -> ContentFacts:
    """Digest what classification reads (text, row text, code, images, in order) separately
    from every block and group, so a change only to structure re-runs only what reads it."""
    text_parts: list[str] = []
    text_chars = 0
    for block in container.blocks:
        if block.type in _TEXT_TYPES:
            text = block_text(block)
            text_chars += len(text)
            text_parts.append(f"{block.type.value}:{text}")
        elif block.type == BlockType.IMAGE:
            text_parts.append(f"image:{_content_hash(block.data, block.content_hash)}")
    block_parts = [f"b:{b.type.value}:{_content_hash(b.data, b.content_hash)}" for b in container.blocks]
    group_parts = [f"g:{g.type.value}:{_content_hash(g.data, g.content_hash)}" for g in container.block_groups]
    return ContentFacts(
        text_digest=_digest(text_parts),
        blocks_digest=_digest([*block_parts, *group_parts]),
        text_chars=text_chars,
        has_tables=any(g.type in _TABLE_GROUPS for g in container.block_groups)
        or any(b.type == BlockType.TABLE_ROW for b in container.blocks),
        has_images=any(b.type == BlockType.IMAGE for b in container.blocks),
    )
