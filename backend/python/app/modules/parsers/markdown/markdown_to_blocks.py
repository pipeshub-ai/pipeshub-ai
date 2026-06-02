from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
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


@dataclass
class _OpenGroup:
    index: int
    group_type: GroupType
    sub_type: GroupSubType | None = None
    child_block_indices: list[int] = field(default_factory=list)
    child_group_indices: list[int] = field(default_factory=list)


@dataclass
class _TableState:
    group_index: int
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] = field(default_factory=list)
    in_header: bool = False


class MarkdownToBlocksConverter:
    """Convert Markdown content directly into BlocksContainer without Docling."""

    def __init__(self) -> None:
        self._md = MarkdownIt("commonmark").enable("table")

    def convert(
        self,
        markdown_content: str,
        caption_map: dict[str, str] | None = None,
    ) -> BlocksContainer:
        tokens = self._md.parse(markdown_content)
        walker = _TokenWalker(caption_map=caption_map)
        return walker.walk(tokens)


class _TokenWalker:
    def __init__(self, caption_map: dict[str, str] | None = None) -> None:
        self.caption_map = caption_map or {}
        self.blocks: list[Block] = []
        self.block_groups: list[BlockGroup] = []
        self.group_stack: list[_OpenGroup] = []
        self.list_item_depth = 0
        self.table_state: _TableState | None = None

    def walk(self, tokens: list[Token]) -> BlocksContainer:
        index = 0
        while index < len(tokens):
            index = self._process_token(tokens, index)
            index += 1
        return BlocksContainer(blocks=self.blocks, block_groups=self.block_groups)

    def _process_token(self, tokens: list[Token], index: int) -> int:
        token = tokens[index]

        if token.type == "heading_open":
            inline_token = tokens[index + 1]
            self._add_text_blocks_from_inline(
                inline_token,
                BlockSubType.HEADING,
                heading_level=token.tag,
            )
            return index + 2

        if token.type == "paragraph_open":
            inline_token = tokens[index + 1]
            sub_type = BlockSubType.LIST_ITEM if self.list_item_depth > 0 else BlockSubType.PARAGRAPH
            self._add_text_blocks_from_inline(inline_token, sub_type)
            return index + 2

        if token.type == "fence":
            self._add_code_block(token.content.rstrip("\n"), token.info.strip() or None)
            return index

        if token.type == "code_block":
            self._add_code_block(token.content.rstrip("\n"), None)
            return index

        if token.type == "bullet_list_open":
            self._open_group(GroupType.LIST)
            return index

        if token.type == "ordered_list_open":
            self._open_group(GroupType.ORDERED_LIST)
            return index

        if token.type in {"bullet_list_close", "ordered_list_close"}:
            self._close_group()
            return index

        if token.type == "list_item_open":
            self.list_item_depth += 1
            return index

        if token.type == "list_item_close":
            self.list_item_depth = max(0, self.list_item_depth - 1)
            return index

        if token.type == "blockquote_open":
            self._open_group(GroupType.TEXT_SECTION, GroupSubType.QUOTE)
            return index

        if token.type == "blockquote_close":
            self._close_group()
            return index

        if token.type == "table_open":
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
            inline_token = tokens[index + 1]
            cell_text = self._render_inline(inline_token).strip()
            if self.table_state is not None:
                self.table_state.current_row.append(cell_text)
            return index + 2

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
            self._finish_table()
            return index

        if token.type == "hr":
            self._add_block(
                Block(
                    id=str(uuid4()),
                    index=0,
                    type=BlockType.TEXT,
                    sub_type=BlockSubType.DIVIDER,
                    format=DataFormat.TXT,
                    data="---",
                    parent_index=self._current_parent_index(),
                )
            )
            return index

        if token.type == "html_block" and token.content.strip():
            self._add_block(
                Block(
                    id=str(uuid4()),
                    index=0,
                    type=BlockType.TEXT,
                    sub_type=BlockSubType.PARAGRAPH,
                    format=DataFormat.HTML,
                    data=token.content.strip(),
                    parent_index=self._current_parent_index(),
                )
            )
            return index

        return index

    def _current_parent_index(self) -> int | None:
        if self.group_stack:
            return self.group_stack[-1].index
        return None

    def _open_group(
        self,
        group_type: GroupType,
        sub_type: GroupSubType | None = None,
    ) -> None:
        parent_index = self._current_parent_index()
        group = BlockGroup(
            index=len(self.block_groups),
            type=group_type,
            sub_type=sub_type,
            parent_index=parent_index,
        )
        self.block_groups.append(group)
        if self.group_stack:
            self.group_stack[-1].child_group_indices.append(group.index)
        self.group_stack.append(
            _OpenGroup(index=group.index, group_type=group_type, sub_type=sub_type)
        )

    def _close_group(self) -> None:
        if not self.group_stack:
            return
        open_group = self.group_stack.pop()
        group = self.block_groups[open_group.index]
        group.children = BlockGroupChildren.from_indices(
            block_indices=open_group.child_block_indices,
            block_group_indices=open_group.child_group_indices,
        )

    def _add_block(self, block: Block) -> Block:
        block.index = len(self.blocks)
        self.blocks.append(block)
        if self.group_stack:
            self.group_stack[-1].child_block_indices.append(block.index)
        return block

    def _add_code_block(self, content: str, language: str | None) -> None:
        if not content:
            return
        self._add_block(
            Block(
                id=str(uuid4()),
                index=0,
                type=BlockType.TEXT,
                sub_type=BlockSubType.CODE,
                format=DataFormat.CODE,
                data=content,
                parent_index=self._current_parent_index(),
                code_metadata=CodeMetadata(language=language),
            )
        )

    def _add_text_blocks_from_inline(
        self,
        inline_token: Token,
        sub_type: BlockSubType,
        heading_level: str | None = None,
    ) -> None:
        del heading_level
        text, image_tokens = self._split_inline_content(inline_token)

        if text:
            data: Any = text
            self._add_block(
                Block(
                    id=str(uuid4()),
                    index=0,
                    type=BlockType.TEXT,
                    sub_type=sub_type,
                    format=DataFormat.TXT,
                    data=data,
                    parent_index=self._current_parent_index(),
                )
            )

        for image_token in image_tokens:
            self._add_image_block(image_token)

    def _split_inline_content(self, inline_token: Token) -> tuple[str, list[Token]]:
        if not inline_token.children:
            return inline_token.content.strip(), []

        text_parts: list[str] = []
        image_tokens: list[Token] = []
        for child in inline_token.children:
            if child.type == "image":
                image_tokens.append(child)
            else:
                rendered = self._render_inline(child)
                if rendered:
                    text_parts.append(rendered)
        return "".join(text_parts).strip(), image_tokens

    def _render_inline(self, token: Token) -> str:
        if token.type in {"text", "code_inline", "html_inline"}:
            return token.content
        if token.type in {"softbreak", "hardbreak"}:
            return "\n"
        if token.children:
            return "".join(self._render_inline(child) for child in token.children)
        return ""

    def _add_image_block(self, image_token: Token) -> None:
        alt_text = image_token.content
        attrs = dict(image_token.attrs) if image_token.attrs else {}
        src = attrs.get("src", "")

        data: dict[str, str] | None = None
        if alt_text and alt_text in self.caption_map:
            data = {"uri": self.caption_map[alt_text]}
        elif src:
            data = {"url": src}

        self._add_block(
            Block(
                id=str(uuid4()),
                index=0,
                type=BlockType.IMAGE,
                format=DataFormat.BASE64 if data and "uri" in data else DataFormat.TXT,
                data=data,
                parent_index=self._current_parent_index(),
                image_metadata=ImageMetadata(captions=[alt_text] if alt_text else []),
            )
        )

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
        self.group_stack.append(_OpenGroup(index=group.index, group_type=GroupType.TABLE))
        self.table_state = _TableState(group_index=group.index)

    def _finish_table(self) -> None:
        if self.table_state is None:
            return

        headers = self.table_state.headers
        rows = self.table_state.rows
        group = self.block_groups[self.table_state.group_index]

        if headers and not rows and self.table_state.current_row:
            headers = self.table_state.current_row

        row_block_indices: list[int] = []
        for row_number, row_cells in enumerate(rows, start=1):
            row_text = self._format_table_row(headers, row_cells)
            block = Block(
                id=str(uuid4()),
                index=len(self.blocks),
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                parent_index=group.index,
                data={
                    "row_natural_language_text": row_text,
                    "row_number": row_number,
                    "cells": row_cells,
                },
            )
            self.blocks.append(block)
            row_block_indices.append(block.index)

        group.table_metadata = TableMetadata(
            num_of_rows=len(rows),
            num_of_cols=len(headers) if headers else (len(rows[0]) if rows else 0),
            num_of_cells=(
                len(rows) * len(headers)
                if headers and rows
                else sum(len(row) for row in rows)
            ),
            has_header=bool(headers),
            column_names=headers or None,
        )
        group.data = {
            "table_summary": "",
            "column_headers": headers,
        }
        group.children = BlockGroupChildren.from_indices(block_indices=row_block_indices)

        if self.group_stack and self.group_stack[-1].index == group.index:
            open_group = self.group_stack.pop()
            if self.group_stack:
                self.group_stack[-1].child_block_indices.extend(open_group.child_block_indices)
                self.group_stack[-1].child_group_indices.extend(open_group.child_group_indices)

        self.table_state = None

    @staticmethod
    def _format_table_row(headers: list[str], cells: list[str]) -> str:
        if headers:
            parts = []
            for index, cell in enumerate(cells):
                header = headers[index] if index < len(headers) else f"Column {index + 1}"
                parts.append(f"{header}: {cell}")
            return ", ".join(parts)
        return ", ".join(cells)
