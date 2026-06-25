from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    CitationMetadata,
    CodeMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
    ImageMetadata,
    TableMetadata,
)

_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)')
_HTML_IMG_ALT_RE = re.compile(
    r'<img\s[^>]*alt=["\']([^"\']*)["\'][^>]*>', re.IGNORECASE
)
_LIST_MARKER_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")


def _strip_list_marker(raw: str) -> str:
    """Strip the leading bullet/ordered list marker from a list item's raw slice.

    ``token.map`` covers the whole item *including* the ``- `` / ``1. `` prefix,
    so the marker would otherwise leak into the first text segment as a
    standalone fragment. Only the first line's marker is stripped; nested
    markers on later lines stay (they are handled when that nested item is
    suppressed by ``_skip_structural_emission``).
    """
    if not raw:
        return raw
    return _LIST_MARKER_RE.sub("", raw, count=1)


@dataclass
class _Segment:
    kind: Literal["text", "image"]
    text: str = ""
    alt_text: str = ""


@dataclass
class _TableCell:
    plain: str
    markdown: str
    images: list[Token] = field(default_factory=list)


def _next_image_match(text: str, start: int) -> re.Match[str] | None:
    md_match = _MD_IMAGE_RE.search(text, start)
    html_match = _HTML_IMG_ALT_RE.search(text, start)
    if md_match is None:
        return html_match
    if html_match is None:
        return md_match
    return md_match if md_match.start() <= html_match.start() else html_match


def _image_alt_from_match(match: re.Match[str]) -> str:
    return match.group(1)


def _split_raw_markdown_into_segments(raw: str) -> list[_Segment]:
    """Split raw markdown/HTML into ordered text and image segments."""
    if not raw:
        return []

    segments: list[_Segment] = []
    pos = 0
    while pos < len(raw):
        match = _next_image_match(raw, pos)
        if match is None:
            tail = raw[pos:]
            if tail.strip():
                segments.append(_Segment(kind="text", text=tail))
            break

        before = raw[pos:match.start()]
        if before.strip():
            segments.append(_Segment(kind="text", text=before))

        segments.append(_Segment(kind="image", alt_text=_image_alt_from_match(match)))
        pos = match.end()

    return segments


def _split_cell_into_segments(cell: _TableCell) -> list[_Segment]:
    if (
        not cell.images
        and not _MD_IMAGE_RE.search(cell.markdown)
        and not _HTML_IMG_ALT_RE.search(cell.markdown)
    ):
        value = (cell.plain or cell.markdown).strip()
        return [_Segment(kind="text", text=value)] if value else []
    return _split_raw_markdown_into_segments(cell.markdown)


@dataclass
class _OpenGroup:
    index: int
    child_block_indices: list[int] = field(default_factory=list)
    child_group_indices: list[int] = field(default_factory=list)


@dataclass
class _TableState:
    group_index: int
    headers: list[_TableCell] = field(default_factory=list)
    rows: list[list[_TableCell]] = field(default_factory=list)
    current_row: list[_TableCell] = field(default_factory=list)
    in_header: bool = False


_PLAIN_INLINE_CHILD_TYPES = frozenset({"text", "softbreak", "hardbreak"})


def _apply_page_number_to_container(
    container: BlocksContainer, page_number: int
) -> None:
    for block in container.blocks:
        block.citation_metadata = CitationMetadata(page_number=page_number)
    for group in container.block_groups:
        group.citation_metadata = CitationMetadata(page_number=page_number)


class MarkdownToBlocksConverter:
    """Convert Markdown content directly into BlocksContainer without Docling."""

    def __init__(self) -> None:
        self._md = MarkdownIt("commonmark").enable("table")

    def convert(
        self,
        markdown_content: str,
        caption_map: dict[str, str] | None = None,
        page_number: int | None = None,
    ) -> BlocksContainer:
        """Convert Markdown to a BlocksContainer.

        Args:
            markdown_content: Markdown source string.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
                Keys must be unique per image. The intended usage is to first call
                ``extract_and_replace_images()`` (which normalises alt-text to
                unique ``Image_N`` labels), build the caption_map from those labels,
                then call this method with the modified markdown. If duplicate
                alt-text keys exist in the map, later entries silently win.

        Returns:
            Populated BlocksContainer with blocks and block_groups.
        """
        tokens = self._md.parse(markdown_content)
        walker = _TokenWalker(
            markdown_content=markdown_content,
            caption_map=caption_map,
        )
        container = walker.walk(tokens)
        if page_number is not None:
            _apply_page_number_to_container(container, page_number)
        return container


class _TokenWalker:
    def __init__(
        self,
        markdown_content: str,
        caption_map: dict[str, str] | None = None,
    ) -> None:
        self.caption_map = caption_map or {}
        self._source_lines = markdown_content.splitlines()
        self.blocks: list[Block] = []
        self.block_groups: list[BlockGroup] = []
        self.group_stack: list[_OpenGroup] = []
        self.list_item_depth = 0
        self.blockquote_depth = 0
        self.table_state: _TableState | None = None
        self._pending_heading: tuple[int, Token] | None = None

    def walk(self, tokens: list[Token]) -> BlocksContainer:
        index = 0
        while index < len(tokens):
            index = self._process_token(tokens, index)
            index += 1
        self._flush_pending_heading()
        return BlocksContainer(blocks=self.blocks, block_groups=self.block_groups)

    # ------------------------------------------------------------------ guards

    def _inside_list_item(self) -> bool:
        return self.list_item_depth > 0

    def _inside_blockquote(self) -> bool:
        return self.blockquote_depth > 0

    def _skip_structural_emission(self) -> bool:
        """True when we are inside a list item or blockquote.

        Content inside those structures is already captured as raw markdown on
        the container token itself, so all inner tokens must be suppressed.
        """
        return self._inside_list_item() or self._inside_blockquote()

    # --------------------------------------------------------------- utilities

    def _slice_token_map(self, token: Token) -> str:
        """Return the raw source lines covered by *token*.map."""
        if not token.map:
            return ""
        start, end = token.map
        if start >= end or start >= len(self._source_lines):
            return ""
        return "\n".join(self._source_lines[start:end])

    def _skip_inline_block(self, tokens: list[Token], index: int) -> int:
        """Advance past an optional following inline token (used when skipping
        heading_open / paragraph_open inside suppressed contexts)."""
        if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
            return index + 2
        return index + 1

    _SKIP_FOR_LOOKAHEAD = frozenset({"heading_close", "paragraph_close"})

    def _find_next_content_token(self, tokens: list[Token], start: int) -> int | None:
        """Return the index of the next content-bearing token after *start*,
        skipping over close tags and horizontal rules that sit between a heading
        and the content that follows it. Returns ``None`` when no content token
        remains."""
        idx = start
        while idx < len(tokens):
            if tokens[idx].type in self._SKIP_FOR_LOOKAHEAD:
                idx += 1
                continue
            return idx
        return None

    def _flush_pending_heading(self) -> None:
        """Emit a buffered heading as a standalone block when the next content
        turned out not to be a paragraph."""
        if self._pending_heading is None:
            return
        _level, inline_token = self._pending_heading
        self._pending_heading = None
        self._add_text_blocks_from_inline(inline_token, BlockSubType.HEADING)

    # ---------------------------------------------------------- token dispatch

    def _process_token(self, tokens: list[Token], index: int) -> int:  # noqa: C901
        token = tokens[index]

        # ---- inline text blocks (headings, paragraphs) --------------------
        if token.type == "heading_open":
            self._flush_pending_heading()
            if self._skip_structural_emission():
                return self._skip_inline_block(tokens, index)
            if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
                heading_level = int(token.tag[1])  # "h1" -> 1, etc.
                inline_token = tokens[index + 1]
                # Skip heading_open, inline, heading_close
                end_idx = index + 2
                if end_idx < len(tokens) and tokens[end_idx].type == "heading_close":
                    end_idx += 1
                # Look ahead for a paragraph to merge into
                next_idx = self._find_next_content_token(tokens, end_idx)
                if next_idx is not None and tokens[next_idx].type == "paragraph_open":
                    self._pending_heading = (heading_level, inline_token)
                    return end_idx - 1
                self._add_text_blocks_from_inline(inline_token, BlockSubType.HEADING)
                return index + 2
            return index + 1

        if token.type == "paragraph_open":
            if self._skip_structural_emission():
                return self._skip_inline_block(tokens, index)
            if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
                if self._pending_heading is not None:
                    level, heading_inline = self._pending_heading
                    self._pending_heading = None
                    self._add_paragraph_with_heading(level, heading_inline, tokens[index + 1])
                else:
                    self._add_text_blocks_from_inline(tokens[index + 1], BlockSubType.PARAGRAPH)
                return index + 2
            return index + 1

        # ---- code blocks (fenced and indented) ----------------------------
        # Both produce a GroupType.CODE group containing a single code block.
        # fence carries language info; code_block does not.
        if token.type in {"fence", "code_block"}:
            self._flush_pending_heading()
            if not self._skip_structural_emission():
                language = (token.info.strip() or None) if token.type == "fence" else None
                self._add_code_block(token.content.rstrip("\n"), language)
            return index

        # ---- lists --------------------------------------------------------
        if token.type == "bullet_list_open":
            self._flush_pending_heading()
            if not self._skip_structural_emission():
                self._open_group(GroupType.LIST)
            return index

        if token.type == "ordered_list_open":
            self._flush_pending_heading()
            if not self._skip_structural_emission():
                self._open_group(GroupType.ORDERED_LIST)
            return index

        if token.type in {"bullet_list_close", "ordered_list_close"}:
            if not self._skip_structural_emission():
                self._close_group()
            return index

        if token.type == "list_item_open":
            # Only emit a block for the outermost list item (depth == 0).
            # Nested list items are fully represented by their ancestor's raw
            # markdown slice, so they must be suppressed here.
            if not self._skip_structural_emission():
                raw_markdown = self._slice_token_map(token)
                if raw_markdown:
                    # token.map covers the bullet/ordinal marker too; drop it
                    # so the "- " prefix does not leak into the first text
                    # fragment (and become a standalone "- " block when the
                    # item is image-only).
                    stripped = _strip_list_marker(raw_markdown)
                    segments = _split_raw_markdown_into_segments(stripped)
                    if (
                        len(segments) == 1
                        and segments[0].kind == "image"
                    ):
                        # Image-only list item: skip the empty container +
                        # fragmentation and emit a single IMAGE block whose
                        # sub_type marks it as the list item itself.
                        if image_block := self._build_image_block(
                            segments[0].alt_text,
                            sub_type=BlockSubType.LIST_ITEM,
                        ):
                            self._add_block(image_block)
                    else:
                        self._emit_with_image_splits(
                            block_type=BlockType.TEXT,
                            sub_type=BlockSubType.LIST_ITEM,
                            format=DataFormat.MARKDOWN,
                            segments=segments,
                        )
            self.list_item_depth += 1
            return index

        if token.type == "list_item_close":
            self.list_item_depth = max(0, self.list_item_depth - 1)
            return index

        # ---- blockquotes --------------------------------------------------
        if token.type == "blockquote_open":
            self._flush_pending_heading()
            # Blockquotes inside list items are already captured by the list
            # item's raw markdown slice — ignore them here.
            if self._inside_list_item():
                return index
            if self.blockquote_depth == 0:
                self._open_group(GroupType.TEXT_SECTION, GroupSubType.QUOTE)
                raw_markdown = self._slice_token_map(token)
                if raw_markdown:
                    self._emit_with_image_splits(
                        block_type=BlockType.TEXT,
                        sub_type=BlockSubType.QUOTE,
                        format=DataFormat.MARKDOWN,
                        segments=_split_raw_markdown_into_segments(raw_markdown),
                    )
            self.blockquote_depth += 1
            return index

        if token.type == "blockquote_close":
            if self._inside_list_item():
                return index
            if self.blockquote_depth == 1:
                self._close_group()
            self.blockquote_depth = max(0, self.blockquote_depth - 1)
            return index

        # ---- tables -------------------------------------------------------
        if token.type == "table_open":
            self._flush_pending_heading()
            if not self._skip_structural_emission():
                self._start_table()
            return index

        if token.type == "thead_open":
            if self.table_state is not None:
                self.table_state.in_header = True
            return index

        if token.type == "thead_close":
            if self.table_state is not None:
                self.table_state.in_header = False
            return index

        if token.type == "tr_open":
            if self.table_state is not None:
                self.table_state.current_row = []
            return index

        if token.type in {"th_open", "td_open"}:
            if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
                inline_tok = tokens[index + 1]
                plain = self._render_inline(inline_tok).strip()
                md_text = inline_tok.content.strip()
                image_tokens = [
                    child
                    for child in (inline_tok.children or [])
                    if child.type == "image"
                ]
                if self.table_state is not None:
                    self.table_state.current_row.append(
                        _TableCell(plain=plain, markdown=md_text, images=image_tokens)
                    )
                return index + 2
            if self.table_state is not None:
                self.table_state.current_row.append(_TableCell(plain="", markdown=""))
            return index + 1

        if token.type == "tr_close":
            if self.table_state is not None and self.table_state.current_row:
                row = self.table_state.current_row
                if self.table_state.in_header:
                    self.table_state.headers = row
                else:
                    self.table_state.rows.append(row)
                self.table_state.current_row = []
            return index

        if token.type == "table_close":
            if not self._skip_structural_emission():
                self._finish_table()
            return index

        # ---- misc top-level tokens ----------------------------------------
        if token.type == "hr":
            self._flush_pending_heading()
            return index

        if token.type == "html_block" and token.content.strip():
            self._flush_pending_heading()
            if not self._skip_structural_emission():
                self._add_html_block(token.content.strip())
            return index

        return index

    # ------------------------------------------------------- group management

    def _current_parent_index(self) -> int | None:
        return self.group_stack[-1].index if self.group_stack else None

    def _open_group(
        self,
        group_type: GroupType,
        sub_type: GroupSubType | None = None,
    ) -> None:
        group = BlockGroup(
            index=len(self.block_groups),
            type=group_type,
            sub_type=sub_type,
            parent_index=self._current_parent_index(),
        )
        self.block_groups.append(group)
        if self.group_stack:
            self.group_stack[-1].child_group_indices.append(group.index)
        self.group_stack.append(_OpenGroup(index=group.index))

    def _close_group(self) -> None:
        if not self.group_stack:
            return
        open_group = self.group_stack.pop()
        group = self.block_groups[open_group.index]
        group.children = BlockGroupChildren.from_indices(
            block_indices=open_group.child_block_indices,
            block_group_indices=open_group.child_group_indices,
        )

    def _append_block(self, block: Block) -> Block:
        block.index = len(self.blocks)
        self.blocks.append(block)
        return block

    def _add_block(self, block: Block) -> Block:
        block = self._append_block(block)
        if self.group_stack:
            self.group_stack[-1].child_block_indices.append(block.index)
        return block

    # ------------------------------------------------------- block factories

    def _add_code_block(self, content: str, language: str | None) -> None:
        """Emit a GroupType.CODE group containing a single code block.

        The group exists so that code is treated consistently with other
        structured block types (list, quote, table) and can carry group-level
        metadata if needed in the future.
        """
        if not content:
            return
        self._open_group(GroupType.CODE)
        self._add_block(
            Block(
                id=str(uuid4()),
                type=BlockType.TEXT,
                sub_type=BlockSubType.CODE,
                format=DataFormat.CODE,
                data=content,
                parent_index=self._current_parent_index(),
                code_metadata=CodeMetadata(language=language),
            )
        )
        self._close_group()

    def _add_html_block(self, content: str) -> None:
        """Emit a GroupType.TEXT_SECTION group containing a single HTML block."""
        if not content:
            return
        self._open_group(GroupType.TEXT_SECTION)
        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.HTML,
            segments=_split_raw_markdown_into_segments(content),
        )
        self._close_group()

    def _add_text_blocks_from_inline(
        self,
        inline_token: Token,
        sub_type: BlockSubType,
    ) -> None:
        segments = self._split_inline_into_segments(inline_token)
        if not any(seg.kind == "image" for seg in segments):
            text, data_format = self._split_inline_content(inline_token)
            if text:
                self._add_block(
                    Block(
                        id=str(uuid4()),
                        type=BlockType.TEXT,
                        sub_type=sub_type,
                        format=data_format,
                        data=text,
                        parent_index=self._current_parent_index(),
                    )
                )
            return

        data_format = self._inline_segments_format(inline_token)
        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=sub_type,
            format=data_format,
            segments=segments,
        )

    def _add_paragraph_with_heading(
        self,
        heading_level: int,
        heading_inline: Token,
        paragraph_inline: Token,
    ) -> None:
        """Emit a PARAGRAPH block (or split container) with heading prefix and body."""
        segments = self._merge_heading_paragraph_segments(
            heading_level, heading_inline, paragraph_inline
        )
        if not any(seg.kind == "image" for seg in segments):
            heading_text, _ = self._split_inline_content(heading_inline)
            para_text, _ = self._split_inline_content(paragraph_inline)
            prefix = "#" * heading_level + " " + heading_text if heading_text else ""
            if prefix and para_text:
                merged = prefix + "\n" + para_text
            elif prefix:
                merged = prefix
            else:
                merged = para_text
            if merged:
                self._add_block(
                    Block(
                        id=str(uuid4()),
                        type=BlockType.TEXT,
                        sub_type=BlockSubType.PARAGRAPH,
                        format=DataFormat.MARKDOWN,
                        data=merged,
                        parent_index=self._current_parent_index(),
                    )
                )
            return

        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            segments=segments,
        )

    def _resolve_image_uri(self, alt_text: str) -> str | None:
        if alt_text and alt_text in self.caption_map:
            uri = self.caption_map[alt_text]
            if uri:
                return uri
        return None

    def _build_image_block(
        self,
        alt_text: str,
        *,
        parent_block_index: int | None = None,
        sub_type: BlockSubType | None = None,
    ) -> Block | None:
        uri = self._resolve_image_uri(alt_text)
        if not uri:
            return None

        image_fmt = None
        if uri.startswith("data:"):
            header = uri.split(",", 1)[0]
            mime = header.replace("data:", "").split(";")[0]
            parts = mime.split("/")
            image_fmt = parts[1] if len(parts) > 1 else None

        return Block(
            id=str(uuid4()),
            type=BlockType.IMAGE,
            sub_type=sub_type,
            format=DataFormat.BASE64,
            data={"uri": uri},
            parent_index=(
                None
                if parent_block_index is not None
                else self._current_parent_index()
            ),
            parent_block_index=parent_block_index,
            image_metadata=ImageMetadata(
                captions=[alt_text] if alt_text else [],
                image_format=image_fmt,
            ),
        )

    def _append_fragment_block(self, block: Block, parent_block_index: int) -> Block:
        block.parent_block_index = parent_block_index
        return self._append_block(block)

    def _uses_image_split_container(self) -> bool:
        """Return True when inline images should use an empty container block.

        Container + ``parent_block_index`` children apply inside list and
        blockquote groups. Top-level paragraphs emit flat sibling blocks instead.
        """
        if not self.group_stack:
            return False
        group = self.block_groups[self.group_stack[-1].index]
        if group.type in (GroupType.LIST, GroupType.ORDERED_LIST):
            return True
        if group.type == GroupType.TEXT_SECTION and group.sub_type == GroupSubType.QUOTE:
            return True
        return False

    def _emit_with_image_splits(
        self,
        *,
        block_type: BlockType,
        sub_type: BlockSubType | None,
        format: DataFormat,
        segments: list[_Segment],
        container_data: Any | None = None,
    ) -> None:
        """Emit blocks from text/image segments.

        Without images: one block with joined text. With images inside a list or
        quote group: empty container plus TEXT/IMAGE children linked via
        ``parent_block_index``. Fragment children omit ``sub_type``; only the
        container carries the parent semantic type (e.g. ``LIST_ITEM``).
        """
        has_images = any(seg.kind == "image" for seg in segments)
        if not has_images:
            full_text = "".join(seg.text for seg in segments if seg.kind == "text").strip()
            if not full_text and container_data is None:
                return
            data = container_data if container_data is not None else full_text
            if data is None or (isinstance(data, str) and not data.strip()):
                return
            self._add_block(
                Block(
                    id=str(uuid4()),
                    type=block_type,
                    sub_type=sub_type,
                    format=format,
                    data=data,
                    parent_index=self._current_parent_index(),
                )
            )
            return

        if not self._uses_image_split_container():
            for seg in segments:
                if seg.kind == "text":
                    if not seg.text.strip():
                        continue
                    self._add_block(
                        Block(
                            id=str(uuid4()),
                            type=BlockType.TEXT,
                            sub_type=sub_type,
                            format=format,
                            data=seg.text,
                            parent_index=self._current_parent_index(),
                        )
                    )
                elif seg.kind == "image":
                    if image_block := self._build_image_block(seg.alt_text):
                        self._add_block(image_block)
            return

        container = self._add_block(
            Block(
                id=str(uuid4()),
                type=block_type,
                sub_type=sub_type,
                format=format,
                data=container_data,
                parent_index=self._current_parent_index(),
            )
        )
        container_index = container.index

        for seg in segments:
            if seg.kind == "text":
                if not seg.text.strip():
                    continue
                self._append_fragment_block(
                    Block(
                        id=str(uuid4()),
                        type=BlockType.TEXT,
                        format=format,
                        data=seg.text,
                    ),
                    container_index,
                )
            elif seg.kind == "image":
                if image_block := self._build_image_block(
                    seg.alt_text, parent_block_index=container_index
                ):
                    self._append_block(image_block)

    def _merge_heading_paragraph_segments(
        self,
        heading_level: int,
        heading_inline: Token,
        paragraph_inline: Token,
    ) -> list[_Segment]:
        segments = self._split_inline_into_segments(heading_inline)
        para_segments = self._split_inline_into_segments(paragraph_inline)
        if segments and segments[0].kind == "text":
            prefix = "#" * heading_level + " "
            segments[0].text = prefix + segments[0].text
        elif heading_level > 0:
            segments.insert(0, _Segment(kind="text", text="#" * heading_level + " "))
        if segments and para_segments:
            last = segments[-1]
            first_para = para_segments[0]
            if last.kind == "text" and first_para.kind == "text":
                last.text = last.text + "\n" + first_para.text
                segments.extend(para_segments[1:])
            else:
                segments.extend(para_segments)
        else:
            segments.extend(para_segments)
        return segments

    def _inline_segments_format(self, inline_token: Token) -> DataFormat:
        return DataFormat.MARKDOWN

    def _split_inline_into_segments(self, inline_token: Token) -> list[_Segment]:
        if not inline_token.children:
            text = inline_token.content.strip()
            return [_Segment(kind="text", text=text)] if text else []

        segments: list[_Segment] = []
        text_children: list[Token] = []

        def flush_text_children() -> None:
            if not text_children:
                return
            if self._has_inline_formatting(text_children):
                rendered = self._render_inline_markdown_from_children(text_children)
            else:
                rendered = "".join(self._render_inline(c) for c in text_children)
            if rendered.strip():
                segments.append(_Segment(kind="text", text=rendered))
            text_children.clear()

        for child in inline_token.children:
            if child.type == "image":
                flush_text_children()
                segments.append(_Segment(kind="image", alt_text=child.content))
            elif child.type == "html_inline":
                flush_text_children()
                for html_seg in _split_raw_markdown_into_segments(child.content):
                    if html_seg.kind == "image":
                        segments.append(html_seg)
                    elif html_seg.text.strip():
                        segments.append(_Segment(kind="text", text=html_seg.text))
            else:
                text_children.append(child)

        flush_text_children()
        return segments

    def _row_has_images(self, row_cells: list[_TableCell]) -> bool:
        return any(
            cell.images
            or _MD_IMAGE_RE.search(cell.markdown)
            or _HTML_IMG_ALT_RE.search(cell.markdown)
            for cell in row_cells
        )

    def _split_table_row_into_segments(
        self,
        headers: list[str],
        row_cells: list[_TableCell],
    ) -> list[_Segment]:
        pending_pairs: list[tuple[str, str]] = []
        fragments: list[_Segment] = []

        def flush_text_fragment() -> None:
            if not pending_pairs:
                return
            hdrs = [pair[0] for pair in pending_pairs]
            vals = [pair[1] for pair in pending_pairs]
            text = self._format_table_row(hdrs, vals)
            if text.strip():
                fragments.append(_Segment(kind="text", text=text))
            pending_pairs.clear()

        for col_idx, cell in enumerate(row_cells):
            header = (
                headers[col_idx]
                if col_idx < len(headers)
                else f"Column {col_idx + 1}"
            )
            cell_segments = _split_cell_into_segments(cell)
            if not any(seg.kind == "image" for seg in cell_segments):
                value = ""
                if cell_segments and cell_segments[0].kind == "text":
                    value = cell_segments[0].text.strip()
                elif cell.plain:
                    value = cell.plain.strip()
                if value:
                    pending_pairs.append((header, value))
                continue

            for seg in cell_segments:
                if seg.kind == "text":
                    value = seg.text.strip()
                    if value:
                        pending_pairs.append((header, value))
                else:
                    flush_text_fragment()
                    fragments.append(seg)

        flush_text_fragment()
        return fragments

    def _emit_table_row_with_image_splits(
        self,
        *,
        group_index: int,
        row_number: int,
        headers: list[str],
        row_cells: list[_TableCell],
        child_block_indices: list[int],
    ) -> None:
        segments = self._split_table_row_into_segments(headers, row_cells)
        container = self._append_block(
            Block(
                id=str(uuid4()),
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                parent_index=group_index,
                data={"row_number": row_number},
            )
        )
        child_block_indices.append(container.index)
        container_index = container.index

        for seg in segments:
            if seg.kind == "text":
                if not seg.text.strip():
                    continue
                self._append_fragment_block(
                    Block(
                        id=str(uuid4()),
                        type=BlockType.TEXT,
                        format=DataFormat.MARKDOWN,
                        data=seg.text,
                    ),
                    container_index,
                )
            elif seg.kind == "image":
                if image_block := self._build_image_block(
                    seg.alt_text, parent_block_index=container_index
                ):
                    self._append_block(image_block)

    # ---------------------------------------------------------- inline render

    def _has_inline_formatting(self, children: list[Token]) -> bool:
        return any(
            child.type not in _PLAIN_INLINE_CHILD_TYPES and child.type != "image"
            for child in children
        )

    def _split_inline_content(
        self, inline_token: Token
    ) -> tuple[str, DataFormat]:
        if not inline_token.children:
            return inline_token.content.strip(), DataFormat.MARKDOWN

        non_image_children = [c for c in inline_token.children if c.type != "image"]
        has_images = len(non_image_children) != len(inline_token.children)

        if not has_images:
            if self._has_inline_formatting(inline_token.children):
                return inline_token.content.strip(), DataFormat.MARKDOWN
            return self._render_inline(inline_token).strip(), DataFormat.MARKDOWN

        if self._has_inline_formatting(non_image_children):
            return (
                self._render_inline_markdown_from_children(non_image_children).strip(),
                DataFormat.MARKDOWN,
            )

        text_parts = [rendered for c in non_image_children if (rendered := self._render_inline(c))]
        return "".join(text_parts).strip(), DataFormat.MARKDOWN

    def _render_inline(self, token: Token) -> str:
        """Render a token to plain text (no markup decorators)."""
        if token.type in {"text", "code_inline", "html_inline"}:
            return token.content
        if token.type in {"softbreak", "hardbreak"}:
            return "\n"
        if token.children:
            return "".join(self._render_inline(c) for c in token.children)
        return ""

    def _render_inline_markdown(self, token: Token) -> str:
        """Render a single inline token back to markdown syntax."""
        if token.type == "text":
            return token.content
        if token.type == "code_inline":
            return f"`{token.content}`"
        if token.type == "html_inline":
            return token.content
        if token.type in {"softbreak", "hardbreak"}:
            return "\n"
        return token.content

    def _render_inline_markdown_from_children(self, children: list[Token]) -> str:
        """Reconstruct markdown from a flat list of inline child tokens.

        Paired _open/_close tokens are consumed together so the rendered
        output preserves bold, italic, link, and other inline markup.

        Note: Expects children with images already filtered out.
        """
        parts: list[str] = []
        index = 0
        while index < len(children):
            child = children[index]

            if child.type.endswith("_close"):
                index += 1
                continue

            if child.type.endswith("_open"):
                close_type = child.type.replace("_open", "_close")
                inner_tokens: list[Token] = []
                index += 1
                while index < len(children) and children[index].type != close_type:
                    inner_tokens.append(children[index])
                    index += 1
                inner = self._render_inline_markdown_from_children(inner_tokens)
                if child.type == "link_open":
                    href = dict(child.attrs or {}).get("href", "")
                    if child.markup == "autolink":
                        parts.append(f"<{href}>")
                    else:
                        parts.append(f"[{inner}]({href})")
                elif child.markup:
                    parts.append(f"{child.markup}{inner}{child.markup}")
                else:
                    parts.append(inner)
                # consume the closing token
                if index < len(children) and children[index].type == close_type:
                    index += 1
                continue

            parts.append(self._render_inline_markdown(child))
            index += 1

        return "".join(parts)

    # ------------------------------------------------------- table management

    def _start_table(self) -> None:
        group = BlockGroup(
            index=len(self.block_groups),
            type=GroupType.TABLE,
            parent_index=self._current_parent_index(),
            table_metadata=TableMetadata(has_header=True),
            data={"table_summary": "", "column_headers": []},
            format=DataFormat.JSON,
        )
        self.block_groups.append(group)
        if self.group_stack:
            self.group_stack[-1].child_group_indices.append(group.index)
        self.group_stack.append(_OpenGroup(index=group.index))
        self.table_state = _TableState(group_index=group.index)

    def _finish_table(self) -> None:
        if self.table_state is None:
            return

        headers = self.table_state.headers
        rows = self.table_state.rows
        group = self.block_groups[self.table_state.group_index]

        if self.table_state.current_row:
            if not headers:
                headers = self.table_state.current_row
            else:
                rows.append(self.table_state.current_row)

        header_md = [h.markdown for h in headers]

        child_block_indices: list[int] = []

        if headers and self._row_has_images(headers):
            self._emit_table_row_with_image_splits(
                group_index=group.index,
                row_number=0,
                headers=[f"Column {i + 1}" for i in range(len(headers))],
                row_cells=headers,
                child_block_indices=child_block_indices,
            )

        for row_number, row_cells in enumerate(rows, start=1):
            if self._row_has_images(row_cells):
                self._emit_table_row_with_image_splits(
                    group_index=group.index,
                    row_number=row_number,
                    headers=header_md,
                    row_cells=row_cells,
                    child_block_indices=child_block_indices,
                )
                continue

            row_text = self._format_table_row(header_md, [c.markdown for c in row_cells])
            block = self._append_block(
                Block(
                    id=str(uuid4()),
                    type=BlockType.TABLE_ROW,
                    format=DataFormat.JSON,
                    parent_index=group.index,
                    data={
                        "row_natural_language_text": row_text,
                        "row_number": row_number,
                        "cells": [c.markdown for c in row_cells],
                    },
                )
            )
            child_block_indices.append(block.index)

        group.table_metadata = TableMetadata(
            num_of_rows=len(rows),
            num_of_cols=len(headers) if headers else (len(rows[0]) if rows else 0),
            num_of_cells=(
                len(rows) * len(headers)
                if headers and rows
                else sum(len(row) for row in rows)
            ),
            has_header=bool(headers),
        )
        group.data = {"table_summary": "", "column_headers": header_md}
        group.children = BlockGroupChildren.from_indices(block_indices=child_block_indices)

        if self.group_stack and self.group_stack[-1].index == group.index:
            self.group_stack.pop()

        self.table_state = None

    @staticmethod
    def _format_table_row(headers: list[str], cells: list[str]) -> str:
        if headers:
            parts = [
                f"{headers[i] if i < len(headers) else f'Column {i + 1}'}: {cell}"
                for i, cell in enumerate(cells)
            ]
            return ", ".join(parts)
        return ", ".join(cells)
