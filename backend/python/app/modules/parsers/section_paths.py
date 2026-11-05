"""Heading levels and the headings each block sits under.

Parsers note which block opens a section while they walk a document, then call
``assign_section_paths`` once at the end. Every block stores the headings above
it, outermost first, in ``citation_metadata.heading_path``; a heading's own text
is not part of its path. The block that opens a section stores the heading's
level as ``name="H{n}"``, the convention the Notion connector uses.

Both are written only with ``INDEXING_SECTION_PATHS=true``. Readers use them
whenever a stored block carries them, so blocks parsed without it read as they
always did.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    CitationMetadata,
)
from app.modules.parsers.link_text import anchor_text_only
from app.utils.env_utils import env_bool

SECTION_PATHS_ENV = "INDEXING_SECTION_PATHS"
SECTION_PATH_SEPARATOR = " › "
MAX_HEADING_CHARS = 80
MAX_SECTION_PATH_CHARS = 160
_ELLIPSIS = "…"

_HEADING_NAME_RE = re.compile(r"^H([1-6])$")
_ATX_PREFIX_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_ATX_CLOSING_RE = re.compile(r"\s+#+\s*$")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
_EMPHASIS_RE = re.compile(r"(\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1")
# Underscores mark emphasis only at word boundaries: get_user_id is a name.
_UNDERSCORE_EMPHASIS_RE = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_WHITESPACE_RE = re.compile(r"\s+")


def section_paths_enabled() -> bool:
    return env_bool(SECTION_PATHS_ENV, False)


@dataclass(frozen=True)
class SectionOpener:
    """A heading that starts a section at a given block.

    ``level`` is ``None`` for a heading with no level (a ``<summary>``): it
    takes the path it sits under but does not nest what follows it.
    """

    level: int | None
    text: str


def heading_level(block: Block) -> int | None:
    """The level stored on a block that opens a section, or ``None``."""
    match = _HEADING_NAME_RE.match(block.name or "")
    return int(match.group(1)) if match else None


def opens_section(block: Block) -> bool:
    """A heading, or a block a parser marked as opening a section."""
    return block.sub_type == BlockSubType.HEADING or heading_level(block) is not None


def clean_heading_text(text: str, *, markdown: bool = True) -> str:
    """A heading as a one-line label: no markup, link targets or excess length.

    ``markdown=False`` is for text read from HTML, which has no markup to remove.
    """
    if not text:
        return ""
    label = _WHITESPACE_RE.sub(" ", text).strip()
    if markdown:
        if _ATX_PREFIX_RE.match(label):
            label = _ATX_CLOSING_RE.sub("", _ATX_PREFIX_RE.sub("", label))
        label = anchor_text_only(label)
        # A code span's text is kept as written: `__init__` is a name.
        pieces: list[str] = []
        last = 0
        for span in _CODE_SPAN_RE.finditer(label):
            pieces += [_without_emphasis(label[last : span.start()]), span.group(2)]
            last = span.end()
        label = "".join([*pieces, _without_emphasis(label[last:])]).strip()
    if len(label) > MAX_HEADING_CHARS:
        label = label[: MAX_HEADING_CHARS - 1].rstrip() + _ELLIPSIS
    return label


def _without_emphasis(text: str) -> str:
    return _UNDERSCORE_EMPHASIS_RE.sub(r"\2", _EMPHASIS_RE.sub(r"\2", text))


def format_section_path(parts: Sequence[str]) -> str:
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
        if len(SECTION_PATH_SEPARATOR.join([_ELLIPSIS, part, *kept])) > MAX_SECTION_PATH_CHARS:
            break
        kept.insert(0, part)
    if not kept:
        return parts[-1][:MAX_SECTION_PATH_CHARS]
    return SECTION_PATH_SEPARATOR.join([_ELLIPSIS, *kept])


def block_heading_path(item: object) -> list[str]:
    """The headings stored on a block or group (model or stored dict), outermost first."""
    citation = (
        item.get("citation_metadata") if isinstance(item, Mapping)
        else getattr(item, "citation_metadata", None)
    )
    path = (
        citation.get("heading_path") if isinstance(citation, Mapping)
        else getattr(citation, "heading_path", None)
    )
    if not isinstance(path, list):
        return []
    return [part for part in path if isinstance(part, str) and part.strip()]


def note_section_opener(
    blocks: list[Block],
    start: int,
    level: int | None,
    heading_text: str,
    openers: dict[int, SectionOpener],
    *,
    markdown: bool = True,
) -> None:
    """Record the section opened by a heading that was emitted as ``blocks[start:]``.

    The first of those blocks that carries text opens it: the heading on its
    own, or the paragraph it was merged into. That block, and any heading block
    split off after it, takes the heading's level.
    """
    opener = next((b for b in blocks[start:] if isinstance(b.data, str) and b.data.strip()), None)
    if opener is None:
        return
    if level is not None:
        for block in blocks[start:]:
            if block is opener or block.sub_type == BlockSubType.HEADING:
                block.name = f"H{level}"
    openers[opener.index] = SectionOpener(level, clean_heading_text(heading_text, markdown=markdown))


def assign_section_paths(container: BlocksContainer, openers: Mapping[int, SectionOpener]) -> None:
    """Store on every block and group the headings it sits under.

    Blocks are in document order, so one pass with a heading stack is enough.
    A table or list group takes the path of its first block.
    """
    stack: list[tuple[int, str]] = []
    for block in container.blocks:
        opener = openers.get(block.index)
        if opener is not None and opener.level is not None:
            while stack and stack[-1][0] >= opener.level:
                stack.pop()
        if stack:
            _set_heading_path(block, [text for _, text in stack])
        if opener is not None and opener.level is not None and opener.text:
            stack.append((opener.level, opener.text))

    by_index = {block.index: block for block in container.blocks}
    for group in container.block_groups:
        ranges = group.children.block_ranges if group.children is not None else None
        if not ranges:
            continue
        first = by_index.get(min(r.start for r in ranges))
        path = block_heading_path(first) if first is not None else []
        if path:
            _set_heading_path(group, path)


def _set_heading_path(item: Block | BlockGroup, path: list[str]) -> None:
    if item.citation_metadata is None:
        item.citation_metadata = CitationMetadata(heading_path=path)
    else:
        item.citation_metadata.heading_path = path
