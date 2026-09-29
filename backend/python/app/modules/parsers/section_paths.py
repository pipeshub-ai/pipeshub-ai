"""Heading levels and the section path each block sits under.

Parsers record which block opens a section (a heading, or a paragraph a
heading was merged into) while they walk the document, then call
``assign_section_paths`` once at the end. The path is stored in
``citation_metadata.section_title``, the field the JSON and YAML parsers
already use for a block's location, and heading blocks carry their level as
``name="H{n}"``, the convention the Notion connector established.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.blocks import Block, BlocksContainer, BlockSubType, CitationMetadata
from app.modules.parsers.link_text import anchor_text_only

SECTION_PATH_SEPARATOR = " › "
MAX_HEADING_CHARS = 80
MAX_SECTION_PATH_CHARS = 160
_ELLIPSIS = "…"

_HEADING_NAME_RE = re.compile(r"^H([1-6])$")
_ATX_PREFIX_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~|`)(?=\S)(.+?)(?<=\S)\1")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SectionOpener:
    """A heading that starts a section at a given block.

    ``level`` is ``None`` for a heading with no level (a ``<summary>``): it
    gets the path it sits under but does not nest what follows it.
    """

    level: int | None
    text: str


def heading_level_name(level: int) -> str:
    return f"H{level}"


def heading_level(block: Block) -> int | None:
    """The level stored on a heading block, or ``None`` when it has none."""
    match = _HEADING_NAME_RE.match(block.name or "")
    return int(match.group(1)) if match else None


def clean_heading_text(text: str) -> str:
    """A heading as a one-line label: no markup, link targets or excess length."""
    if not text:
        return ""
    first_line = text.strip().split("\n", 1)[0]
    label = _ATX_PREFIX_RE.sub("", first_line)
    label = anchor_text_only(label)
    label = _EMPHASIS_RE.sub(r"\2", label)
    label = _WHITESPACE_RE.sub(" ", label).strip().rstrip("#").strip()
    if len(label) > MAX_HEADING_CHARS:
        label = label[: MAX_HEADING_CHARS - 1].rstrip() + _ELLIPSIS
    return label


def format_section_path(parts: list[str]) -> str:
    """Join headings outermost first, dropping outer ones to fit the length cap.

    The innermost heading says the most about a block, so it is the last to go.
    """
    parts = [part for part in parts if part]
    if not parts:
        return ""
    joined = SECTION_PATH_SEPARATOR.join(parts)
    if len(joined) <= MAX_SECTION_PATH_CHARS:
        return joined
    kept: list[str] = []
    for part in reversed(parts):
        candidate = SECTION_PATH_SEPARATOR.join([_ELLIPSIS, part, *kept])
        if len(candidate) > MAX_SECTION_PATH_CHARS:
            break
        kept.insert(0, part)
    if not kept:
        return parts[-1][:MAX_SECTION_PATH_CHARS]
    return SECTION_PATH_SEPARATOR.join([_ELLIPSIS, *kept])


def block_section_path(block: object) -> str:
    """The section path stored on a block or group (model or dict), or ``""``."""
    citation = (
        block.get("citation_metadata") if isinstance(block, dict)
        else getattr(block, "citation_metadata", None)
    )
    if citation is None:
        return ""
    title = citation.get("section_title") if isinstance(citation, dict) else getattr(
        citation, "section_title", None
    )
    return title.strip() if isinstance(title, str) else ""


def assign_section_paths(
    container: BlocksContainer, openers: dict[int, SectionOpener],
) -> int:
    """Store on every block the headings it sits under; return how many got one.

    Blocks are in document order, so one pass with a heading stack is enough.
    A heading block's own text is not part of its path. A table or list group
    takes the path of its first block. Blocks that already have a path (set by
    a connector) keep it.
    """
    stack: list[tuple[int, str]] = []
    assigned = 0
    for block in container.blocks:
        opener = openers.get(block.index)
        if opener is not None and opener.level is not None:
            while stack and stack[-1][0] >= opener.level:
                stack.pop()
        path = format_section_path([text for _, text in stack])
        if path and _set_section_title(block, path):
            assigned += 1
        if opener is not None and opener.level is not None and opener.text:
            stack.append((opener.level, opener.text))

    by_index = {block.index: block for block in container.blocks}
    for group in container.block_groups:
        children = group.children
        first = None
        if children is not None and children.block_ranges:
            first = min(r.start for r in children.block_ranges)
        path = block_section_path(by_index[first]) if first in by_index else ""
        if path:
            _set_section_title(group, path)
    return assigned


def mark_heading_blocks(blocks: list[Block], start: int, level: int) -> None:
    """Stamp the heading level on heading blocks emitted from ``start`` on."""
    for block in blocks[start:]:
        if block.sub_type == BlockSubType.HEADING:
            block.name = heading_level_name(level)


def first_text_block_index(blocks: list[Block], start: int) -> int | None:
    """Index of the first block from ``start`` that carries text."""
    for block in blocks[start:]:
        if isinstance(block.data, str) and block.data.strip():
            return block.index
    return None


def _set_section_title(item: object, path: str) -> bool:
    citation = getattr(item, "citation_metadata", None)
    if citation is None:
        item.citation_metadata = CitationMetadata(section_title=path)
        return True
    if citation.section_title:
        return False
    citation.section_title = path
    return True
