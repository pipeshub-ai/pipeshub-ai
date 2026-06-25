from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    CodeMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
    ImageMetadata,
    TableMetadata,
)

_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)')
_HTML_IMG_ALT_RE = re.compile(r'<img\s[^>]*alt=["\']([^"\']*)["\']', re.IGNORECASE)


@dataclass
class _OpenGroup:
    index: int
    child_block_indices: list[int] = field(default_factory=list)
    child_group_indices: list[int] = field(default_factory=list)


@dataclass
class _TableCell:
    plain: str
    markdown: str
    images: list[Token] = field(default_factory=list)


@dataclass
class _TableState:
    group_index: int
    headers: list[_TableCell] = field(default_factory=list)
    rows: list[list[_TableCell]] = field(default_factory=list)
    current_row: list[_TableCell] = field(default_factory=list)
    in_header: bool = False


_PLAIN_INLINE_CHILD_TYPES = frozenset({"text", "softbreak", "hardbreak"})


class MarkdownToBlocksConverter:
    """Convert Markdown content directly into BlocksContainer without Docling."""

    def __init__(self) -> None:
        self._md = MarkdownIt("commonmark").enable("table")

    def convert(
        self,
        markdown_content: str,
        caption_map: dict[str, str] | None = None,
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
        return walker.walk(tokens)


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
                    self._add_block(
                        Block(
                            id=str(uuid4()),
                            type=BlockType.TEXT,
                            sub_type=BlockSubType.LIST_ITEM,
                            format=DataFormat.MARKDOWN,
                            data=raw_markdown,
                            parent_index=self._current_parent_index(),
                        )
                    )
                    self._emit_images_from_raw_markdown(raw_markdown)
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
                    self._add_block(
                        Block(
                            id=str(uuid4()),
                            type=BlockType.TEXT,
                            sub_type=BlockSubType.QUOTE,
                            format=DataFormat.MARKDOWN,
                            data=raw_markdown,
                            parent_index=self._current_parent_index(),
                        )
                    )
                    self._emit_images_from_raw_markdown(raw_markdown)
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
        self._add_block(
            Block(
                id=str(uuid4()),
                type=BlockType.TEXT,
                sub_type=BlockSubType.PARAGRAPH,
                format=DataFormat.HTML,
                data=content,
                parent_index=self._current_parent_index(),
            )
        )
        self._close_group()

    def _add_text_blocks_from_inline(
        self,
        inline_token: Token,
        sub_type: BlockSubType,
    ) -> None:
        text, image_tokens, data_format = self._split_inline_content(inline_token)

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

        for image_token in image_tokens:
            self._add_image_block(image_token)

    def _add_paragraph_with_heading(
        self,
        heading_level: int,
        heading_inline: Token,
        paragraph_inline: Token,
    ) -> None:
        """Emit a single PARAGRAPH block whose data is the heading prefix
        followed by the paragraph content.  The result is always MARKDOWN
        format because the heading syntax (``# …``) is markdown markup."""
        heading_text, heading_images, _ = self._split_inline_content(heading_inline)
        para_text, para_images, _ = self._split_inline_content(paragraph_inline)

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

        for img in heading_images:
            self._add_image_block(img)
        for img in para_images:
            self._add_image_block(img)

    def _resolve_image_uri(self, alt_text: str) -> str | None:
        if alt_text and alt_text in self.caption_map:
            uri = self.caption_map[alt_text]
            if uri:
                return uri
        return None

    def _build_image_block(self, alt_text: str) -> Block | None:
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
            format=DataFormat.BASE64,
            data={"uri": uri},
            parent_index=self._current_parent_index(),
            image_metadata=ImageMetadata(
                captions=[alt_text] if alt_text else [],
                image_format=image_fmt,
            ),
        )

    def _add_image_block(self, image_token: Token) -> None:
        alt_text = image_token.content
        if block := self._build_image_block(alt_text):
            self._add_block(block)

    def _emit_images_from_raw_markdown(self, raw_markdown: str) -> None:
        """Scan raw markdown text for image references and emit IMAGE blocks.

        Used for structures (list items, blockquotes) where the content is
        captured as a raw slice and inner image tokens are suppressed.
        Matches both ``![alt](url)`` and ``<img alt="...">`` patterns.
        """
        if not self.caption_map:
            return

        seen: set[str] = set()

        for match in _MD_IMAGE_RE.finditer(raw_markdown):
            alt_text = match.group(1)
            if alt_text in seen:
                continue
            block = self._build_image_block(alt_text)
            if block is None:
                continue
            seen.add(alt_text)
            self._add_block(block)

        for match in _HTML_IMG_ALT_RE.finditer(raw_markdown):
            alt_text = match.group(1)
            if alt_text in seen:
                continue
            block = self._build_image_block(alt_text)
            if block is None:
                continue
            seen.add(alt_text)
            self._add_block(block)

    # ---------------------------------------------------------- inline render

    def _has_inline_formatting(self, children: list[Token]) -> bool:
        return any(
            child.type not in _PLAIN_INLINE_CHILD_TYPES and child.type != "image"
            for child in children
        )

    def _split_inline_content(
        self, inline_token: Token
    ) -> tuple[str, list[Token], DataFormat]:
        if not inline_token.children:
            return inline_token.content.strip(), [], DataFormat.TXT

        image_tokens = [c for c in inline_token.children if c.type == "image"]
        non_image_children = [c for c in inline_token.children if c.type != "image"]

        if not image_tokens:
            if self._has_inline_formatting(inline_token.children):
                return inline_token.content.strip(), [], DataFormat.MARKDOWN
            return self._render_inline(inline_token).strip(), [], DataFormat.TXT

        if self._has_inline_formatting(non_image_children):
            return (
                self._render_inline_markdown_from_children(non_image_children).strip(),
                image_tokens,
                DataFormat.MARKDOWN,
            )

        text_parts = [rendered for c in non_image_children if (rendered := self._render_inline(c))]
        return "".join(text_parts).strip(), image_tokens, DataFormat.TXT

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
        for row_number, row_cells in enumerate(rows, start=1):
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
            for cell in row_cells:
                for image_token in cell.images:
                    if image_block := self._build_image_block(image_token.content):
                        image_block.parent_index = group.index
                        self._append_block(image_block)
                        child_block_indices.append(image_block.index)

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

        # Pop the table group off the stack. Rows use _append_block (not
        # _add_block) so the _OpenGroup carries no child indices to propagate
        # upward — the pop is all that's needed.
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
