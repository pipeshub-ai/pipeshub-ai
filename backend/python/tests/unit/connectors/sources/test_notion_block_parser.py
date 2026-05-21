"""Full coverage tests for app.connectors.sources.notion.block_parser."""

import json
import re
from datetime import datetime
from typing import Any, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.models.blocks import (
    Block,
    BlockComment,
    BlockContainerIndex,
    BlockGroup,
    BlockGroupChildren,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
    ListMetadata,
    TableMetadata,
    TableRowMetadata,
)


def _p():
    return NotionBlockParser(logger=MagicMock())


def _block(block_type, type_data, block_id="blk-1", has_children=False,
           archived=False, in_trash=False,
           created_time="2025-01-01T00:00:00Z",
           last_edited_time="2025-01-15T00:00:00Z"):
    return {
        "id": block_id,
        "type": block_type,
        block_type: type_data,
        "has_children": has_children,
        "archived": archived,
        "in_trash": in_trash,
        "created_time": created_time,
        "last_edited_time": last_edited_time,
    }


class TestParseUnknownBlockType:
    @pytest.mark.asyncio
    async def test_with_rich_text(self):
        parser = _p()
        block = _block("future_type", {
            "rich_text": [{"type": "text", "plain_text": "Hello future", "annotations": {}}],
        })
        blk, grp, children = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert "Hello future" in blk.data

    @pytest.mark.asyncio
    async def test_with_text_key(self):
        parser = _p()
        block = _block("new_block", {
            "text": [{"type": "text", "plain_text": "From text key", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None

    @pytest.mark.asyncio
    async def test_with_content_key(self):
        parser = _p()
        block = _block("another_new", {
            "content": [{"type": "text", "plain_text": "From content", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None

    @pytest.mark.asyncio
    async def test_no_extractable_text(self):
        parser = _p()
        block = _block("empty_new", {"foo": "bar"})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None
        assert grp is None

    @pytest.mark.asyncio
    async def test_non_dict_type_data(self):
        parser = _p()
        block = _block("weird", "not-a-dict")
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None


class TestParseMediaBlockDirectFileUrls:
    @pytest.mark.asyncio
    async def test_direct_video_mp4_url(self):
        parser = _p()
        block = _block("video", {
            "type": "external",
            "external": {"url": "https://cdn.example.com/video.mp4"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    @pytest.mark.asyncio
    async def test_direct_audio_mp3_url(self):
        parser = _p()
        block = _block("audio", {
            "type": "external",
            "external": {"url": "https://cdn.example.com/song.mp3"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    @pytest.mark.asyncio
    async def test_audio_notion_hosted(self):
        parser = _p()
        block = _block("audio", {
            "type": "file",
            "file": {"url": "https://notion.so/audio.mp3"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    @pytest.mark.asyncio
    async def test_media_no_url(self):
        parser = _p()
        block = _block("file", {"type": "external", "external": {}, "caption": []})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None
        assert grp is None

    @pytest.mark.asyncio
    async def test_media_with_caption_and_name(self):
        parser = _p()
        block = _block("file", {
            "type": "file",
            "file": {"url": "https://notion.so/report.xlsx"},
            "caption": [{"type": "text", "plain_text": "Quarterly Report", "annotations": {}}],
            "name": "report.xlsx",
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.sub_type == BlockSubType.CHILD_RECORD


class TestParseLinkToPageFallback:
    @pytest.mark.asyncio
    async def test_unknown_link_type_with_page(self):
        parser = _p()
        block = _block("link_to_page", {"type": "unknown", "page_id": "abc"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.name == "WEBPAGE"

    @pytest.mark.asyncio
    async def test_unknown_link_type_with_database(self):
        parser = _p()
        block = _block("link_to_page", {"type": "unknown", "database_id": "db1"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.name == "DATASOURCE"

    @pytest.mark.asyncio
    async def test_unknown_link_type_with_nothing(self):
        parser = _p()
        block = _block("link_to_page", {"type": "unknown"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None


class TestExtractPropertyValueWithResolutionAdvanced:
    @pytest.mark.asyncio
    async def test_created_by_with_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Alice"
        callback = AsyncMock(return_value=mock_child)
        prop = {"type": "created_by", "created_by": {"id": "u1"}}
        result = await parser._extract_property_value_with_resolution(prop, get_user_child_callback=callback)
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_created_by_without_callback(self):
        parser = _p()
        prop = {"type": "created_by", "created_by": {"id": "u1", "name": "Bob"}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == "Bob"

    @pytest.mark.asyncio
    async def test_created_by_empty(self):
        parser = _p()
        result = await parser._extract_property_value_with_resolution({"type": "created_by", "created_by": {}})
        assert result == ""

    @pytest.mark.asyncio
    async def test_created_by_none(self):
        parser = _p()
        result = await parser._extract_property_value_with_resolution({"type": "created_by", "created_by": None})
        assert result == ""

    @pytest.mark.asyncio
    async def test_last_edited_by_with_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Charlie"
        callback = AsyncMock(return_value=mock_child)
        prop = {"type": "last_edited_by", "last_edited_by": {"id": "u2"}}
        result = await parser._extract_property_value_with_resolution(prop, get_user_child_callback=callback)
        assert result == "Charlie"

    @pytest.mark.asyncio
    async def test_last_edited_by_without_callback(self):
        parser = _p()
        prop = {"type": "last_edited_by", "last_edited_by": {"id": "u2", "name": "Dan"}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == "Dan"

    @pytest.mark.asyncio
    async def test_last_edited_by_empty(self):
        parser = _p()
        result = await parser._extract_property_value_with_resolution({"type": "last_edited_by", "last_edited_by": {}})
        assert result == ""

    @pytest.mark.asyncio
    async def test_rollup_array_with_people_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Eve"
        callback = AsyncMock(return_value=mock_child)
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [{"type": "people", "people": [{"id": "u3"}]}],
            },
        }
        result = await parser._extract_property_value_with_resolution(prop, get_user_child_callback=callback)
        assert "Eve" in result

    @pytest.mark.asyncio
    async def test_rollup_array_with_relation_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Related Page"
        callback = AsyncMock(return_value=mock_child)
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [{"type": "relation", "id": "pg1"}],
            },
        }
        result = await parser._extract_property_value_with_resolution(prop, get_record_child_callback=callback)
        assert "Related Page" in result

    @pytest.mark.asyncio
    async def test_rollup_array_fallback(self):
        parser = _p()
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [{"type": "number", "number": 42}],
            },
        }
        result = await parser._extract_property_value_with_resolution(prop)
        assert "42" in result

    @pytest.mark.asyncio
    async def test_rollup_non_array(self):
        parser = _p()
        prop = {"type": "rollup", "rollup": {"type": "number", "number": 10}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert "10" in result

    @pytest.mark.asyncio
    async def test_regular_type_delegates(self):
        parser = _p()
        prop = {"type": "checkbox", "checkbox": True}
        result = await parser._extract_property_value_with_resolution(prop)
        assert "✓" in result


class TestExtractPropertyValueEdgeCases:
    def test_formula_none_string(self):
        parser = _p()
        prop = {"type": "formula", "formula": {"type": "string", "string": None}}
        assert parser._extract_property_value(prop) == ""

    def test_formula_none_number(self):
        parser = _p()
        prop = {"type": "formula", "formula": {"type": "number", "number": None}}
        assert parser._extract_property_value(prop) == ""

    def test_formula_boolean_false(self):
        parser = _p()
        prop = {"type": "formula", "formula": {"type": "boolean", "boolean": False}}
        assert "✗" in parser._extract_property_value(prop)

    def test_rollup_none_number(self):
        parser = _p()
        prop = {"type": "rollup", "rollup": {"type": "number", "number": None}}
        assert parser._extract_property_value(prop) == ""

    def test_rollup_unknown_type(self):
        parser = _p()
        prop = {"type": "rollup", "rollup": {"type": "date"}}
        assert parser._extract_property_value(prop) == ""

    def test_email_none(self):
        parser = _p()
        prop = {"type": "email", "email": None}
        assert parser._extract_property_value(prop) == ""

    def test_phone_none(self):
        parser = _p()
        prop = {"type": "phone_number", "phone_number": None}
        assert parser._extract_property_value(prop) == ""

    def test_files_external_url(self):
        parser = _p()
        prop = {"type": "files", "files": [{"name": "", "external": {"url": "https://ext.com/f.txt"}}]}
        result = parser._extract_property_value(prop)
        assert "ext.com" in result

    def test_multi_select_empty(self):
        parser = _p()
        prop = {"type": "multi_select", "multi_select": []}
        assert parser._extract_property_value(prop) == ""


class TestFinalizeIndicesAndMetadata:
    def test_table_row_header_flags(self):
        parser = _p()
        table_group = BlockGroup(
            index=0, type=GroupType.TABLE,
            table_metadata=TableMetadata(num_of_cols=2, has_header=True),
            children=BlockGroupChildren(),
        )
        row1 = Block(index=0, parent_index=0, type=BlockType.TABLE_ROW,
                      data={}, format=DataFormat.JSON,
                      table_row_metadata=TableRowMetadata(row_number=0, is_header=False))
        row2 = Block(index=1, parent_index=0, type=BlockType.TABLE_ROW,
                      data={}, format=DataFormat.JSON,
                      table_row_metadata=TableRowMetadata(row_number=0, is_header=False))
        parser._finalize_indices_and_metadata([row1, row2], [table_group])
        assert row1.table_row_metadata.is_header is True
        assert row1.table_row_metadata.row_number == 1
        assert row2.table_row_metadata.is_header is False
        assert row2.table_row_metadata.row_number == 2


class TestCalculateListIndentLevels:
    def test_nested_list_items(self):
        parser = _p()
        group0 = BlockGroup(index=0, type=GroupType.TEXT_SECTION, parent_index=None)
        group1 = BlockGroup(index=1, type=GroupType.TEXT_SECTION, parent_index=0)
        b0 = Block(index=0, parent_index=0, type=BlockType.BULLET_LIST,
                    sub_type=BlockSubType.LIST_ITEM, data="item",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="bullet", indent_level=0))
        b1 = Block(index=1, parent_index=1, type=BlockType.BULLET_LIST,
                    sub_type=BlockSubType.LIST_ITEM, data="nested",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="bullet", indent_level=0))
        parser._calculate_list_indent_levels([b0, b1], [group0, group1])
        assert b0.list_metadata.indent_level == 1
        assert b1.list_metadata.indent_level == 2


class TestFixNumberedListNumbering:
    def test_sequential_numbering(self):
        parser = _p()
        blocks = []
        for i in range(3):
            blocks.append(Block(
                index=i, type=BlockType.TEXT, data=f"1. Item {i}",
                format=DataFormat.MARKDOWN,
                list_metadata=ListMetadata(list_style="numbered", indent_level=0),
            ))
        parser._fix_numbered_list_numbering(blocks)
        assert blocks[0].data == "1. Item 0"
        assert blocks[1].data == "2. Item 1"
        assert blocks[2].data == "3. Item 2"

    def test_interrupted_by_non_list(self):
        parser = _p()
        b0 = Block(index=0, type=BlockType.TEXT, data="1. A",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        b1 = Block(index=1, type=BlockType.TEXT, data="Paragraph",
                    format=DataFormat.MARKDOWN)
        b2 = Block(index=2, type=BlockType.TEXT, data="1. B",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        parser._fix_numbered_list_numbering([b0, b1, b2])
        assert b0.data == "1. A"
        assert b2.data == "1. B"


class TestGroupListItems:
    def test_groups_consecutive_bullets(self):
        parser = _p()
        blocks = []
        block_groups = []
        for i in range(3):
            blocks.append(Block(
                index=i, type=BlockType.BULLET_LIST, data=f"- Item {i}",
                format=DataFormat.MARKDOWN,
                list_metadata=ListMetadata(list_style="bullet", indent_level=0),
            ))
        parser._group_list_items(blocks, block_groups)
        assert len(block_groups) == 1
        assert block_groups[0].type == GroupType.LIST

    def test_groups_numbered_list(self):
        parser = _p()
        blocks = [
            Block(index=0, type=BlockType.TEXT, data="1. A",
                  format=DataFormat.MARKDOWN,
                  list_metadata=ListMetadata(list_style="numbered", indent_level=0)),
        ]
        groups = []
        parser._group_list_items(blocks, groups)
        assert len(groups) == 1
        assert groups[0].type == GroupType.ORDERED_LIST

    def test_separate_groups_for_different_styles(self):
        parser = _p()
        b0 = Block(index=0, type=BlockType.TEXT, data="- A",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="bullet", indent_level=0))
        b1 = Block(index=1, type=BlockType.TEXT, data="1. B",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        groups = []
        parser._group_list_items([b0, b1], groups)
        assert len(groups) == 2

    def test_empty_blocks(self):
        parser = _p()
        groups = []
        parser._group_list_items([], groups)
        assert len(groups) == 0

    def test_non_list_items_only(self):
        parser = _p()
        blocks = [Block(index=0, type=BlockType.TEXT, data="Hello", format=DataFormat.MARKDOWN)]
        groups = []
        parser._group_list_items(blocks, groups)
        assert len(groups) == 0


class TestCreateListGroup:
    def test_single_item(self):
        parser = _p()
        blocks = [Block(index=0, type=BlockType.TEXT, data="- A",
                        format=DataFormat.MARKDOWN,
                        list_metadata=ListMetadata(list_style="bullet", indent_level=0))]
        groups = []
        parser._create_list_group(blocks, groups, [0], "bullet")
        assert len(groups) == 1
        assert groups[0].type == GroupType.LIST

    def test_empty_indices(self):
        parser = _p()
        groups = []
        parser._create_list_group([], groups, [], "bullet")
        assert len(groups) == 0


class TestPostProcessBlocksFull:
    def test_full_pipeline(self):
        parser = _p()
        b0 = Block(index=0, type=BlockType.TEXT, data="1. First",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        b1 = Block(index=1, type=BlockType.TEXT, data="1. Second",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        blocks = [b0, b1]
        groups = []
        parser.post_process_blocks(blocks, groups)
        assert b1.data.startswith("2.")
        assert len(groups) >= 1


class TestExtractRichTextEmptyArray:
    def test_empty(self):
        parser = _p()
        assert parser.extract_rich_text([]) == ""
        assert parser.extract_rich_text([], plain_text=True) == ""

    def test_extract_plain_text_internal(self):
        parser = _p()
        assert parser._extract_plain_text([]) == ""
        result = parser._extract_plain_text([{"plain_text": "Hi"}])
        assert result == "Hi"

    def test_extract_markdown_empty(self):
        parser = _p()
        assert parser._extract_markdown_text([]) == ""


class TestExtractRichTextAnnotationCombinations:
    def test_bold_italic(self):
        parser = _p()
        rich_text = [{
            "type": "text", "plain_text": "bolditalic",
            "annotations": {"bold": True, "italic": True, "code": False,
                           "strikethrough": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "**" in result
        assert "*" in result

    def test_empty_text_skipped(self):
        parser = _p()
        rich_text = [{"type": "text", "plain_text": "", "annotations": {}}]
        result = parser.extract_rich_text(rich_text)
        assert result == ""


class TestParseBlockReturnTypes:
    @pytest.mark.asyncio
    async def test_block_group_with_empty_data(self):
        parser = _p()
        block = _block("toggle", {"rich_text": []})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is None or (grp is not None and grp.data != "")


class TestParseDataSourceToBlocksAdvanced:
    @pytest.mark.asyncio
    async def test_truncated_rows(self):
        parser = _p()
        metadata = {
            "properties": {"Name": {"type": "title"}},
            "description": [],
        }
        rows = [
            {"id": f"row-{i}", "properties": {"Name": {"type": "title", "title": [{"plain_text": f"R{i}"}]}}}
            for i in range(105)
        ]
        blocks, block_groups = await parser.parse_data_source_to_blocks(metadata, rows, "ds-1")
        assert len(blocks) > 100

    @pytest.mark.asyncio
    async def test_empty_properties(self):
        parser = _p()
        metadata = {"properties": {}, "description": []}
        blocks, block_groups = await parser.parse_data_source_to_blocks(metadata, [], "ds-1")
        assert len(block_groups) == 1
        assert len(blocks) == 1

    @pytest.mark.asyncio
    async def test_with_relation_callback_in_rows(self):
        parser = _p()
        metadata = {
            "properties": {"Name": {"type": "title"}, "Ref": {"type": "relation"}},
            "description": [],
        }
        rows = [{
            "id": "row-1",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Task"}]},
                "Ref": {"type": "relation", "relation": [{"id": "page-ref"}]},
            },
        }]
        mock_child = MagicMock()
        mock_child.child_name = "Referenced Page"
        mock_child.child_type = ChildType.RECORD
        record_callback = AsyncMock(return_value=mock_child)
        blocks, _ = await parser.parse_data_source_to_blocks(
            metadata, rows, "ds-1", get_record_child_callback=record_callback,
        )
        assert len(blocks) >= 2


class TestGenerateDataSourceMarkdownEdgeCases:
    def test_pipes_in_data(self):
        parser = _p()
        columns = ["A|B"]
        rows = [{"properties": {"A|B": {"type": "title", "title": [{"plain_text": "val|ue"}]}}}]
        result = parser._generate_data_source_markdown(columns, rows)
        assert "\\|" in result

    def test_long_cell_value_truncated(self):
        parser = _p()
        columns = ["Col"]
        long_val = "x" * 300
        rows = [{"properties": {"Col": {"type": "rich_text", "rich_text": [{"plain_text": long_val}]}}}]
        result = parser._generate_data_source_markdown(columns, rows)
        lines = result.split("\n")
        data_line = lines[2]
        assert len(data_line) < 350

    def test_more_than_100_rows(self):
        parser = _p()
        columns = ["A"]
        rows = [{"properties": {"A": {"type": "number", "number": i}}} for i in range(110)]
        result = parser._generate_data_source_markdown(columns, rows)
        assert "more rows" in result


class TestIsEmbedPlatformUrlExtended:
    def test_twitch_url(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://twitch.tv/channel") is True

    def test_tiktok_url(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://tiktok.com/video/123") is True

    def test_wav_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/audio.wav") is False

    def test_webm_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/video.webm") is False

    def test_m4a_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/audio.m4a") is False

    def test_mov_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/video.mov") is False

    def test_dailymotion(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://dailymotion.com/video/abc") is True

    def test_soundcloud(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://soundcloud.com/track/123") is True


class TestExtractRelationsAndPeopleEdgeCases:
    def test_empty_relations(self):
        parser = _p()
        props = {"Ref": {"type": "relation", "relation": []}}
        relations, people = parser._extract_relations_and_people(props)
        assert relations == []

    def test_people_without_id(self):
        parser = _p()
        props = {"Assignee": {"type": "people", "people": [{"name": "No ID"}]}}
        _, people = parser._extract_relations_and_people(props)
        assert people == []

    def test_created_by_non_dict(self):
        parser = _p()
        props = {"Author": {"type": "created_by", "created_by": "not-a-dict"}}
        _, people = parser._extract_relations_and_people(props)
        assert people == []

    def test_rollup_with_non_dict_items(self):
        parser = _p()
        props = {
            "Roll": {
                "type": "rollup",
                "rollup": {"type": "array", "array": ["not-a-dict"]},
            },
        }
        relations, people = parser._extract_relations_and_people(props)
        assert relations == []
        assert people == []


class TestParseTimestampEdgeCases:
    def test_invalid_format(self):
        parser = _p()
        assert parser._parse_timestamp("not-a-timestamp") is None

    def test_with_milliseconds(self):
        parser = _p()
        result = parser._parse_timestamp("2025-06-15T10:30:45.123Z")
        assert result is not None


class TestExtractPlainTextEdgeCases:
    def test_text_from_text_dict(self):
        parser = _p()
        result = parser.extract_plain_text([{"text": {"content": "from dict"}}])
        assert result == "from dict"

    def test_no_text_at_all(self):
        parser = _p()
        result = parser.extract_plain_text([{}])
        assert result == ""


class TestExtractRichTextFromBlockDataEdgeCases:
    def test_text_dict_without_rich_text(self):
        result = NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph",
            "paragraph": {"text": {"something_else": True}},
        })
        assert result is None


class TestBlockGroupEmptyDataSkip:
    @pytest.mark.asyncio
    async def test_block_group_returns_tuple_directly(self):
        parser = _p()
        block = _block("table", {"table_width": 3, "has_column_header": True, "has_row_header": False})
        blk, grp, children = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.type == GroupType.TABLE

# =============================================================================
# Merged from test_notion_block_parser_full_coverage.py
# =============================================================================

def _p():
    return NotionBlockParser(logger=MagicMock())


def _block(block_type, type_data, block_id="blk-1", has_children=False,
           archived=False, in_trash=False,
           created_time="2025-01-01T00:00:00Z",
           last_edited_time="2025-01-15T00:00:00Z"):
    return {
        "id": block_id,
        "type": block_type,
        block_type: type_data,
        "has_children": has_children,
        "archived": archived,
        "in_trash": in_trash,
        "created_time": created_time,
        "last_edited_time": last_edited_time,
    }


class TestParseUnknownBlockTypeFullCoverage:
    @pytest.mark.asyncio
    async def test_with_rich_text(self):
        parser = _p()
        block = _block("future_type", {
            "rich_text": [{"type": "text", "plain_text": "Hello future", "annotations": {}}],
        })
        blk, grp, children = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert "Hello future" in blk.data

    @pytest.mark.asyncio
    async def test_with_text_key(self):
        parser = _p()
        block = _block("new_block", {
            "text": [{"type": "text", "plain_text": "From text key", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None

    @pytest.mark.asyncio
    async def test_with_content_key(self):
        parser = _p()
        block = _block("another_new", {
            "content": [{"type": "text", "plain_text": "From content", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None

    @pytest.mark.asyncio
    async def test_no_extractable_text(self):
        parser = _p()
        block = _block("empty_new", {"foo": "bar"})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None
        assert grp is None

    @pytest.mark.asyncio
    async def test_non_dict_type_data(self):
        parser = _p()
        block = _block("weird", "not-a-dict")
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None


class TestParseMediaBlockDirectFileUrlsFullCoverage:
    @pytest.mark.asyncio
    async def test_direct_video_mp4_url(self):
        parser = _p()
        block = _block("video", {
            "type": "external",
            "external": {"url": "https://cdn.example.com/video.mp4"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    @pytest.mark.asyncio
    async def test_direct_audio_mp3_url(self):
        parser = _p()
        block = _block("audio", {
            "type": "external",
            "external": {"url": "https://cdn.example.com/song.mp3"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    @pytest.mark.asyncio
    async def test_audio_notion_hosted(self):
        parser = _p()
        block = _block("audio", {
            "type": "file",
            "file": {"url": "https://notion.so/audio.mp3"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    @pytest.mark.asyncio
    async def test_media_no_url(self):
        parser = _p()
        block = _block("file", {"type": "external", "external": {}, "caption": []})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None
        assert grp is None

    @pytest.mark.asyncio
    async def test_media_with_caption_and_name(self):
        parser = _p()
        block = _block("file", {
            "type": "file",
            "file": {"url": "https://notion.so/report.xlsx"},
            "caption": [{"type": "text", "plain_text": "Quarterly Report", "annotations": {}}],
            "name": "report.xlsx",
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.sub_type == BlockSubType.CHILD_RECORD


class TestParseLinkToPageFallbackFullCoverage:
    @pytest.mark.asyncio
    async def test_unknown_link_type_with_page(self):
        parser = _p()
        block = _block("link_to_page", {"type": "unknown", "page_id": "abc"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.name == "WEBPAGE"

    @pytest.mark.asyncio
    async def test_unknown_link_type_with_database(self):
        parser = _p()
        block = _block("link_to_page", {"type": "unknown", "database_id": "db1"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.name == "DATASOURCE"

    @pytest.mark.asyncio
    async def test_unknown_link_type_with_nothing(self):
        parser = _p()
        block = _block("link_to_page", {"type": "unknown"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None


class TestExtractPropertyValueWithResolutionAdvancedFullCoverage:
    @pytest.mark.asyncio
    async def test_created_by_with_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Alice"
        callback = AsyncMock(return_value=mock_child)
        prop = {"type": "created_by", "created_by": {"id": "u1"}}
        result = await parser._extract_property_value_with_resolution(prop, get_user_child_callback=callback)
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_created_by_without_callback(self):
        parser = _p()
        prop = {"type": "created_by", "created_by": {"id": "u1", "name": "Bob"}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == "Bob"

    @pytest.mark.asyncio
    async def test_created_by_empty(self):
        parser = _p()
        result = await parser._extract_property_value_with_resolution({"type": "created_by", "created_by": {}})
        assert result == ""

    @pytest.mark.asyncio
    async def test_created_by_none(self):
        parser = _p()
        result = await parser._extract_property_value_with_resolution({"type": "created_by", "created_by": None})
        assert result == ""

    @pytest.mark.asyncio
    async def test_last_edited_by_with_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Charlie"
        callback = AsyncMock(return_value=mock_child)
        prop = {"type": "last_edited_by", "last_edited_by": {"id": "u2"}}
        result = await parser._extract_property_value_with_resolution(prop, get_user_child_callback=callback)
        assert result == "Charlie"

    @pytest.mark.asyncio
    async def test_last_edited_by_without_callback(self):
        parser = _p()
        prop = {"type": "last_edited_by", "last_edited_by": {"id": "u2", "name": "Dan"}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == "Dan"

    @pytest.mark.asyncio
    async def test_last_edited_by_empty(self):
        parser = _p()
        result = await parser._extract_property_value_with_resolution({"type": "last_edited_by", "last_edited_by": {}})
        assert result == ""

    @pytest.mark.asyncio
    async def test_rollup_array_with_people_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Eve"
        callback = AsyncMock(return_value=mock_child)
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [{"type": "people", "people": [{"id": "u3"}]}],
            },
        }
        result = await parser._extract_property_value_with_resolution(prop, get_user_child_callback=callback)
        assert "Eve" in result

    @pytest.mark.asyncio
    async def test_rollup_array_with_relation_callback(self):
        parser = _p()
        mock_child = MagicMock()
        mock_child.child_name = "Related Page"
        callback = AsyncMock(return_value=mock_child)
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [{"type": "relation", "id": "pg1"}],
            },
        }
        result = await parser._extract_property_value_with_resolution(prop, get_record_child_callback=callback)
        assert "Related Page" in result

    @pytest.mark.asyncio
    async def test_rollup_array_fallback(self):
        parser = _p()
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [{"type": "number", "number": 42}],
            },
        }
        result = await parser._extract_property_value_with_resolution(prop)
        assert "42" in result

    @pytest.mark.asyncio
    async def test_rollup_non_array(self):
        parser = _p()
        prop = {"type": "rollup", "rollup": {"type": "number", "number": 10}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert "10" in result

    @pytest.mark.asyncio
    async def test_regular_type_delegates(self):
        parser = _p()
        prop = {"type": "checkbox", "checkbox": True}
        result = await parser._extract_property_value_with_resolution(prop)
        assert "✓" in result


class TestExtractPropertyValueEdgeCasesFullCoverage:
    def test_formula_none_string(self):
        parser = _p()
        prop = {"type": "formula", "formula": {"type": "string", "string": None}}
        assert parser._extract_property_value(prop) == ""

    def test_formula_none_number(self):
        parser = _p()
        prop = {"type": "formula", "formula": {"type": "number", "number": None}}
        assert parser._extract_property_value(prop) == ""

    def test_formula_boolean_false(self):
        parser = _p()
        prop = {"type": "formula", "formula": {"type": "boolean", "boolean": False}}
        assert "✗" in parser._extract_property_value(prop)

    def test_rollup_none_number(self):
        parser = _p()
        prop = {"type": "rollup", "rollup": {"type": "number", "number": None}}
        assert parser._extract_property_value(prop) == ""

    def test_rollup_unknown_type(self):
        parser = _p()
        prop = {"type": "rollup", "rollup": {"type": "date"}}
        assert parser._extract_property_value(prop) == ""

    def test_email_none(self):
        parser = _p()
        prop = {"type": "email", "email": None}
        assert parser._extract_property_value(prop) == ""

    def test_phone_none(self):
        parser = _p()
        prop = {"type": "phone_number", "phone_number": None}
        assert parser._extract_property_value(prop) == ""

    def test_files_external_url(self):
        parser = _p()
        prop = {"type": "files", "files": [{"name": "", "external": {"url": "https://ext.com/f.txt"}}]}
        result = parser._extract_property_value(prop)
        assert "ext.com" in result

    def test_multi_select_empty(self):
        parser = _p()
        prop = {"type": "multi_select", "multi_select": []}
        assert parser._extract_property_value(prop) == ""


class TestFinalizeIndicesAndMetadataFullCoverage:
    def test_table_row_header_flags(self):
        parser = _p()
        table_group = BlockGroup(
            index=0, type=GroupType.TABLE,
            table_metadata=TableMetadata(num_of_cols=2, has_header=True),
            children=BlockGroupChildren(),
        )
        row1 = Block(index=0, parent_index=0, type=BlockType.TABLE_ROW,
                      data={}, format=DataFormat.JSON,
                      table_row_metadata=TableRowMetadata(row_number=0, is_header=False))
        row2 = Block(index=1, parent_index=0, type=BlockType.TABLE_ROW,
                      data={}, format=DataFormat.JSON,
                      table_row_metadata=TableRowMetadata(row_number=0, is_header=False))
        parser._finalize_indices_and_metadata([row1, row2], [table_group])
        assert row1.table_row_metadata.is_header is True
        assert row1.table_row_metadata.row_number == 1
        assert row2.table_row_metadata.is_header is False
        assert row2.table_row_metadata.row_number == 2


class TestCalculateListIndentLevelsFullCoverage:
    def test_nested_list_items(self):
        parser = _p()
        group0 = BlockGroup(index=0, type=GroupType.TEXT_SECTION, parent_index=None)
        group1 = BlockGroup(index=1, type=GroupType.TEXT_SECTION, parent_index=0)
        b0 = Block(index=0, parent_index=0, type=BlockType.BULLET_LIST,
                    sub_type=BlockSubType.LIST_ITEM, data="item",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="bullet", indent_level=0))
        b1 = Block(index=1, parent_index=1, type=BlockType.BULLET_LIST,
                    sub_type=BlockSubType.LIST_ITEM, data="nested",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="bullet", indent_level=0))
        parser._calculate_list_indent_levels([b0, b1], [group0, group1])
        assert b0.list_metadata.indent_level == 1
        assert b1.list_metadata.indent_level == 2


class TestFixNumberedListNumberingFullCoverage:
    def test_sequential_numbering(self):
        parser = _p()
        blocks = []
        for i in range(3):
            blocks.append(Block(
                index=i, type=BlockType.TEXT, data=f"1. Item {i}",
                format=DataFormat.MARKDOWN,
                list_metadata=ListMetadata(list_style="numbered", indent_level=0),
            ))
        parser._fix_numbered_list_numbering(blocks)
        assert blocks[0].data == "1. Item 0"
        assert blocks[1].data == "2. Item 1"
        assert blocks[2].data == "3. Item 2"

    def test_interrupted_by_non_list(self):
        parser = _p()
        b0 = Block(index=0, type=BlockType.TEXT, data="1. A",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        b1 = Block(index=1, type=BlockType.TEXT, data="Paragraph",
                    format=DataFormat.MARKDOWN)
        b2 = Block(index=2, type=BlockType.TEXT, data="1. B",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        parser._fix_numbered_list_numbering([b0, b1, b2])
        assert b0.data == "1. A"
        assert b2.data == "1. B"


class TestGroupListItemsFullCoverage:
    def test_groups_consecutive_bullets(self):
        parser = _p()
        blocks = []
        block_groups = []
        for i in range(3):
            blocks.append(Block(
                index=i, type=BlockType.BULLET_LIST, data=f"- Item {i}",
                format=DataFormat.MARKDOWN,
                list_metadata=ListMetadata(list_style="bullet", indent_level=0),
            ))
        parser._group_list_items(blocks, block_groups)
        assert len(block_groups) == 1
        assert block_groups[0].type == GroupType.LIST

    def test_groups_numbered_list(self):
        parser = _p()
        blocks = [
            Block(index=0, type=BlockType.TEXT, data="1. A",
                  format=DataFormat.MARKDOWN,
                  list_metadata=ListMetadata(list_style="numbered", indent_level=0)),
        ]
        groups = []
        parser._group_list_items(blocks, groups)
        assert len(groups) == 1
        assert groups[0].type == GroupType.ORDERED_LIST

    def test_separate_groups_for_different_styles(self):
        parser = _p()
        b0 = Block(index=0, type=BlockType.TEXT, data="- A",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="bullet", indent_level=0))
        b1 = Block(index=1, type=BlockType.TEXT, data="1. B",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        groups = []
        parser._group_list_items([b0, b1], groups)
        assert len(groups) == 2

    def test_empty_blocks(self):
        parser = _p()
        groups = []
        parser._group_list_items([], groups)
        assert len(groups) == 0

    def test_non_list_items_only(self):
        parser = _p()
        blocks = [Block(index=0, type=BlockType.TEXT, data="Hello", format=DataFormat.MARKDOWN)]
        groups = []
        parser._group_list_items(blocks, groups)
        assert len(groups) == 0


class TestCreateListGroupFullCoverage:
    def test_single_item(self):
        parser = _p()
        blocks = [Block(index=0, type=BlockType.TEXT, data="- A",
                        format=DataFormat.MARKDOWN,
                        list_metadata=ListMetadata(list_style="bullet", indent_level=0))]
        groups = []
        parser._create_list_group(blocks, groups, [0], "bullet")
        assert len(groups) == 1
        assert groups[0].type == GroupType.LIST

    def test_empty_indices(self):
        parser = _p()
        groups = []
        parser._create_list_group([], groups, [], "bullet")
        assert len(groups) == 0


class TestPostProcessBlocksFullFullCoverage:
    def test_full_pipeline(self):
        parser = _p()
        b0 = Block(index=0, type=BlockType.TEXT, data="1. First",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        b1 = Block(index=1, type=BlockType.TEXT, data="1. Second",
                    format=DataFormat.MARKDOWN,
                    list_metadata=ListMetadata(list_style="numbered", indent_level=0))
        blocks = [b0, b1]
        groups = []
        parser.post_process_blocks(blocks, groups)
        assert b1.data.startswith("2.")
        assert len(groups) >= 1


class TestExtractRichTextEmptyArrayFullCoverage:
    def test_empty(self):
        parser = _p()
        assert parser.extract_rich_text([]) == ""
        assert parser.extract_rich_text([], plain_text=True) == ""

    def test_extract_plain_text_internal(self):
        parser = _p()
        assert parser._extract_plain_text([]) == ""
        result = parser._extract_plain_text([{"plain_text": "Hi"}])
        assert result == "Hi"

    def test_extract_markdown_empty(self):
        parser = _p()
        assert parser._extract_markdown_text([]) == ""


class TestExtractRichTextAnnotationCombinationsFullCoverage:
    def test_bold_italic(self):
        parser = _p()
        rich_text = [{
            "type": "text", "plain_text": "bolditalic",
            "annotations": {"bold": True, "italic": True, "code": False,
                           "strikethrough": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "**" in result
        assert "*" in result

    def test_empty_text_skipped(self):
        parser = _p()
        rich_text = [{"type": "text", "plain_text": "", "annotations": {}}]
        result = parser.extract_rich_text(rich_text)
        assert result == ""


class TestParseBlockReturnTypesFullCoverage:
    @pytest.mark.asyncio
    async def test_block_group_with_empty_data(self):
        parser = _p()
        block = _block("toggle", {"rich_text": []})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is None or (grp is not None and grp.data != "")


class TestParseDataSourceToBlocksAdvancedFullCoverage:
    @pytest.mark.asyncio
    async def test_truncated_rows(self):
        parser = _p()
        metadata = {
            "properties": {"Name": {"type": "title"}},
            "description": [],
        }
        rows = [
            {"id": f"row-{i}", "properties": {"Name": {"type": "title", "title": [{"plain_text": f"R{i}"}]}}}
            for i in range(105)
        ]
        blocks, block_groups = await parser.parse_data_source_to_blocks(metadata, rows, "ds-1")
        assert len(blocks) > 100

    @pytest.mark.asyncio
    async def test_empty_properties(self):
        parser = _p()
        metadata = {"properties": {}, "description": []}
        blocks, block_groups = await parser.parse_data_source_to_blocks(metadata, [], "ds-1")
        assert len(block_groups) == 1
        assert len(blocks) == 1

    @pytest.mark.asyncio
    async def test_with_relation_callback_in_rows(self):
        parser = _p()
        metadata = {
            "properties": {"Name": {"type": "title"}, "Ref": {"type": "relation"}},
            "description": [],
        }
        rows = [{
            "id": "row-1",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Task"}]},
                "Ref": {"type": "relation", "relation": [{"id": "page-ref"}]},
            },
        }]
        mock_child = MagicMock()
        mock_child.child_name = "Referenced Page"
        mock_child.child_type = ChildType.RECORD
        record_callback = AsyncMock(return_value=mock_child)
        blocks, _ = await parser.parse_data_source_to_blocks(
            metadata, rows, "ds-1", get_record_child_callback=record_callback,
        )
        assert len(blocks) >= 2


class TestGenerateDataSourceMarkdownEdgeCasesFullCoverage:
    def test_pipes_in_data(self):
        parser = _p()
        columns = ["A|B"]
        rows = [{"properties": {"A|B": {"type": "title", "title": [{"plain_text": "val|ue"}]}}}]
        result = parser._generate_data_source_markdown(columns, rows)
        assert "\\|" in result

    def test_long_cell_value_truncated(self):
        parser = _p()
        columns = ["Col"]
        long_val = "x" * 300
        rows = [{"properties": {"Col": {"type": "rich_text", "rich_text": [{"plain_text": long_val}]}}}]
        result = parser._generate_data_source_markdown(columns, rows)
        lines = result.split("\n")
        data_line = lines[2]
        assert len(data_line) < 350

    def test_more_than_100_rows(self):
        parser = _p()
        columns = ["A"]
        rows = [{"properties": {"A": {"type": "number", "number": i}}} for i in range(110)]
        result = parser._generate_data_source_markdown(columns, rows)
        assert "more rows" in result


class TestIsEmbedPlatformUrlExtendedFullCoverage:
    def test_twitch_url(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://twitch.tv/channel") is True

    def test_tiktok_url(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://tiktok.com/video/123") is True

    def test_wav_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/audio.wav") is False

    def test_webm_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/video.webm") is False

    def test_m4a_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/audio.m4a") is False

    def test_mov_file(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://cdn.com/video.mov") is False

    def test_dailymotion(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://dailymotion.com/video/abc") is True

    def test_soundcloud(self):
        parser = _p()
        assert parser._is_embed_platform_url("https://soundcloud.com/track/123") is True


class TestExtractRelationsAndPeopleEdgeCasesFullCoverage:
    def test_empty_relations(self):
        parser = _p()
        props = {"Ref": {"type": "relation", "relation": []}}
        relations, people = parser._extract_relations_and_people(props)
        assert relations == []

    def test_people_without_id(self):
        parser = _p()
        props = {"Assignee": {"type": "people", "people": [{"name": "No ID"}]}}
        _, people = parser._extract_relations_and_people(props)
        assert people == []

    def test_created_by_non_dict(self):
        parser = _p()
        props = {"Author": {"type": "created_by", "created_by": "not-a-dict"}}
        _, people = parser._extract_relations_and_people(props)
        assert people == []

    def test_rollup_with_non_dict_items(self):
        parser = _p()
        props = {
            "Roll": {
                "type": "rollup",
                "rollup": {"type": "array", "array": ["not-a-dict"]},
            },
        }
        relations, people = parser._extract_relations_and_people(props)
        assert relations == []
        assert people == []


class TestParseTimestampEdgeCasesFullCoverage:
    def test_invalid_format(self):
        parser = _p()
        assert parser._parse_timestamp("not-a-timestamp") is None

    def test_with_milliseconds(self):
        parser = _p()
        result = parser._parse_timestamp("2025-06-15T10:30:45.123Z")
        assert result is not None


class TestExtractPlainTextEdgeCasesFullCoverage:
    def test_text_from_text_dict(self):
        parser = _p()
        result = parser.extract_plain_text([{"text": {"content": "from dict"}}])
        assert result == "from dict"

    def test_no_text_at_all(self):
        parser = _p()
        result = parser.extract_plain_text([{}])
        assert result == ""


class TestExtractRichTextFromBlockDataEdgeCasesFullCoverage:
    def test_text_dict_without_rich_text(self):
        result = NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph",
            "paragraph": {"text": {"something_else": True}},
        })
        assert result is None


class TestBlockGroupEmptyDataSkipFullCoverage:
    @pytest.mark.asyncio
    async def test_block_group_returns_tuple_directly(self):
        parser = _p()
        block = _block("table", {"table_width": 3, "has_column_header": True, "has_row_header": False})
        blk, grp, children = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.type == GroupType.TABLE


class TestExtractMediaFileUrl:
    def test_external_url(self):
        data = {"type": "external", "external": {"url": "https://example.com/f.pdf"}}
        assert NotionBlockParser._extract_media_file_url(data) == "https://example.com/f.pdf"

    def test_notion_hosted_file_url(self):
        data = {"type": "file", "file": {"url": "https://notion.so/f.pdf"}}
        assert NotionBlockParser._extract_media_file_url(data) == "https://notion.so/f.pdf"

    def test_external_not_dict_returns_none(self):
        data = {"type": "external", "external": "bad"}
        assert NotionBlockParser._extract_media_file_url(data) is None

    def test_file_not_dict_returns_none(self):
        data = {"file": "bad"}
        assert NotionBlockParser._extract_media_file_url(data) is None

    def test_missing_keys_returns_none(self):
        assert NotionBlockParser._extract_media_file_url({}) is None


class TestParseBlockControlFlow:
    @pytest.mark.asyncio
    async def test_parser_returns_three_tuple(self):
        parser = _p()

        async def fake_parse(*_args: Any, **_kwargs: Any) -> Tuple[None, None, List[Any]]:
            return (None, None, [])

        parser._parse_paragraph = fake_parse
        block = _block("paragraph", {"rich_text": [{"plain_text": "hi"}]})
        result = await parser.parse_block(block, block_index=0)
        assert result == (None, None, [])

    @pytest.mark.asyncio
    async def test_skips_block_with_empty_data(self):
        parser = _p()
        block = _block("paragraph", {"rich_text": []})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None and grp is None

    @pytest.mark.asyncio
    async def test_skips_block_group_with_empty_data(self):
        parser = _p()

        async def empty_group(*_args: Any, **_kwargs: Any) -> BlockGroup:
            return BlockGroup(
                index=0,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.SYNCED_BLOCK,
                data="",
            )

        parser._parse_column_list = empty_group
        block = _block("column_list", {})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None and grp is None

    @pytest.mark.asyncio
    async def test_parser_exception_returns_none(self):
        parser = _p()

        async def boom(*_args: Any, **_kwargs: Any) -> None:
            raise ValueError("parse failed")

        parser._parse_paragraph = boom
        block = _block("paragraph", {"rich_text": [{"plain_text": "x"}]})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None and grp is None
        parser.logger.warning.assert_called()


class TestParagraphLinkMention:
    @pytest.mark.asyncio
    async def test_single_link_mention_becomes_link_block(self):
        parser = _p()
        block = _block("paragraph", {
            "rich_text": [{
                "type": "mention",
                "plain_text": "Example",
                "href": "https://example.com",
                "mention": {
                    "type": "link_mention",
                    "link_mention": {
                        "href": "https://example.com",
                        "title": "Example Site",
                    },
                },
            }],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.sub_type == BlockSubType.LINK
        assert blk.link_metadata is not None
        assert str(blk.link_metadata.link_url).startswith("https://example.com")

    @pytest.mark.asyncio
    async def test_link_mention_without_href(self):
        parser = _p()
        block = _block("paragraph", {
            "rich_text": [{
                "type": "mention",
                "plain_text": "No URL",
                "mention": {
                    "type": "link_mention",
                    "link_mention": {"title": "No URL"},
                },
            }],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LINK
        assert blk.link_metadata is None


class TestCalloutIconTypes:
    @pytest.mark.asyncio
    async def test_callout_external_icon(self):
        parser = _p()
        block = _block("callout", {
            "rich_text": [{"plain_text": "Note"}],
            "icon": {"type": "external", "external": {"url": "https://icon.example/icon.png"}},
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.link_metadata is not None

    @pytest.mark.asyncio
    async def test_callout_file_icon(self):
        parser = _p()
        block = _block("callout", {
            "rich_text": [{"plain_text": "Note"}],
            "icon": {"type": "file", "file": {"url": "https://notion.so/icon.png"}},
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.link_metadata is not None


class TestMarkdownRichTextEdges:
    def test_equation_in_markdown(self):
        parser = _p()
        result = parser._extract_markdown_text([{
            "type": "equation",
            "equation": {"expression": "E=mc^2"},
        }])
        assert "$$E=mc^2$$" in result

    def test_mention_link_mention_href_only(self):
        parser = _p()
        result = parser._extract_markdown_text([{
            "type": "mention",
            "plain_text": "link",
            "mention": {
                "type": "link_mention",
                "link_mention": {"href": "https://x.com"},
            },
        }])
        assert "https://x.com" in result

    def test_mention_plain_text_only(self):
        parser = _p()
        result = parser._extract_markdown_text([{
            "type": "mention",
            "plain_text": "User Name",
            "mention": {"type": "user", "user": {"id": "u1"}},
        }])
        assert result == "User Name"

    def test_unknown_rich_text_type_fallback(self):
        parser = _p()
        result = parser._extract_markdown_text([{
            "type": "custom",
            "plain_text": "fallback",
        }])
        assert result == "fallback"

    def test_code_with_consecutive_backticks_uses_long_fence(self):
        parser = _p()
        result = parser._extract_markdown_text([{
            "type": "text",
            "plain_text": "``code``",
            "annotations": {"code": True},
        }])
        assert "```" in result or "````" in result


class TestSyncedBlockReferenceMetadata:
    @pytest.mark.asyncio
    async def test_synced_block_reference_stores_original_id(self):
        parser = _p()
        block = _block("synced_block", {
            "synced_from": {"type": "block_id", "block_id": "orig-block"},
        })
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.data["is_reference"] is True
        assert grp.data["original_block_id"] == "orig-block"


class TestExtractRelationsAndPeopleExtended:
    def test_created_by_and_last_edited_by(self):
        parser = _p()
        props = {
            "cb": {"type": "created_by", "created_by": {"id": "user-cb"}},
            "leb": {"type": "last_edited_by", "last_edited_by": {"id": "user-leb"}},
        }
        relations, people = parser._extract_relations_and_people(props)
        assert relations == []
        assert "user-cb" in people
        assert "user-leb" in people

    def test_rollup_array_people_and_relation(self):
        parser = _p()
        props = {
            "roll": {
                "type": "rollup",
                "rollup": {
                    "type": "array",
                    "array": [
                        {"type": "people", "people": [{"id": "u1"}]},
                        {"type": "relation", "id": "rel-page-1"},
                    ],
                },
            },
        }
        relations, people = parser._extract_relations_and_people(props)
        assert "rel-page-1" in relations
        assert "u1" in people

    def test_non_dict_property_skipped(self):
        parser = _p()
        relations, people = parser._extract_relations_and_people({"bad": "x"})
        assert relations == [] and people == []


class TestCreateDataRowBlocks:
    @pytest.mark.asyncio
    async def test_row_with_title_and_child_callbacks(self):
        parser = _p()
        table_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren(),
            table_metadata=TableMetadata(num_of_cols=1, num_of_rows=2, has_header=True),
        )
        blocks = []
        rows = [{
            "id": "row-page-1",
            "url": "https://notion.so/row-1",
            "created_time": "2025-01-01T00:00:00Z",
            "last_edited_time": "2025-01-02T00:00:00Z",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Row Title"}]},
            },
        }]
        child = ChildRecord(child_type=ChildType.RECORD, child_id="rec-1", child_name="Row Title")

        async def record_cb(page_id: str) -> Optional[ChildRecord]:
            if page_id == "row-page-1":
                return child
            if page_id == "rel-1":
                return ChildRecord(child_type=ChildType.RECORD, child_id="rec-2", child_name="Rel")
            return None

        await parser._create_data_row_blocks(
            blocks,
            table_group,
            rows,
            [["Row Title"]],
            None,
            "\u200B|\u200B",
            get_record_child_callback=record_cb,
            relations_and_people_list=[(["rel-1"], [])],
        )
        assert len(blocks) == 1
        assert blocks[0].table_row_metadata.children_records

    @pytest.mark.asyncio
    async def test_row_callback_exceptions_still_create_block(self):
        parser = _p()
        table_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren(),
        )
        blocks = []

        async def failing_cb(_page_id: str) -> None:
            raise RuntimeError("lookup failed")

        await parser._create_data_row_blocks(
            blocks,
            table_group,
            [{"id": "row-1", "properties": {}}],
            [["cell"]],
            ["desc"],
            "|",
            get_record_child_callback=failing_cb,
            relations_and_people_list=[(["rel-bad"], [])],
        )
        assert len(blocks) == 1
        assert blocks[0].table_row_metadata is None or not blocks[0].table_row_metadata.children_records


class TestPropertyValueWithResolutionExtended:
    @pytest.mark.asyncio
    async def test_created_by_with_callback(self):
        parser = _p()
        user_cb = AsyncMock(return_value=ChildRecord(
            child_type=ChildType.USER, child_id="u1", child_name="Alice"
        ))
        prop = {"type": "created_by", "created_by": {"id": "u1"}}
        result = await parser._extract_property_value_with_resolution(
            prop, get_user_child_callback=user_cb
        )
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_created_by_no_user_id(self):
        parser = _p()
        prop = {"type": "created_by", "created_by": {}}
        assert await parser._extract_property_value_with_resolution(prop) == ""

    @pytest.mark.asyncio
    async def test_last_edited_by_fallback_name(self):
        parser = _p()
        prop = {"type": "last_edited_by", "last_edited_by": {"id": "u2", "name": "Bob"}}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == "Bob"

    @pytest.mark.asyncio
    async def test_rollup_people_and_relation(self):
        parser = _p()
        user_cb = AsyncMock(return_value=ChildRecord(
            child_type=ChildType.USER, child_id="u1", child_name="User One"
        ))
        record_cb = AsyncMock(return_value=ChildRecord(
            child_type=ChildType.RECORD, child_id="p1", child_name="Linked Page"
        ))
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [
                    {"type": "people", "people": [{"id": "u1"}]},
                    {"type": "relation", "id": "p1"},
                ],
            },
        }
        result = await parser._extract_property_value_with_resolution(
            prop, get_record_child_callback=record_cb, get_user_child_callback=user_cb
        )
        assert "User One" in result
        assert "Linked Page" in result


class TestExtractPropertyValueException:
    def test_unknown_property_type(self):
        parser = _p()
        prop = {"type": "custom_unknown", "custom_unknown": {"foo": "bar"}}
        result = parser._extract_property_value(prop)
        assert "foo" in result or "bar" in result

    def test_exception_returns_empty(self):
        parser = _p()
        prop = {"type": "files", "files": None}
        result = parser._extract_property_value(prop)
        assert result == ""


class TestConvertIndicesToBlockGroupChildren:
    def test_none_and_empty(self):
        parser = _p()
        assert parser._convert_indices_to_block_group_children(None) is None
        assert parser._convert_indices_to_block_group_children([]) is None

    def test_mixed_indices(self):
        parser = _p()
        indices = [
            BlockContainerIndex(block_index=0),
            BlockContainerIndex(block_group_index=1),
        ]
        children = parser._convert_indices_to_block_group_children(indices)
        assert children is not None


class TestListGroupingTrailingGroup:
    def test_closes_list_group_at_end_of_blocks(self):
        parser = _p()
        blocks = [
            Block(
                id="b0", index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="1. one",
                list_metadata=ListMetadata(list_style="numbered", indent_level=0),
            ),
            Block(
                id="b1", index=1, type=BlockType.TEXT, format=DataFormat.TXT, data="tail",
            ),
        ]
        groups = []
        parser._group_list_items(blocks, groups)
        assert any(g.type == GroupType.ORDERED_LIST for g in groups)


class TestPostProcessTableHeaderFlag:
    def test_first_row_marked_header_when_table_has_header(self):
        parser = _p()
        table_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            table_metadata=TableMetadata(has_header=True, num_of_cols=2, num_of_rows=2),
        )
        row = Block(
            id="r1", index=1, parent_index=0, type=BlockType.TABLE_ROW,
            format=DataFormat.JSON, data={},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=False),
        )
        parser.post_process_blocks([row], [table_group])
        assert row.table_row_metadata.is_header is True


class TestCommentParsing:
    def test_parse_notion_timestamp_invalid(self):
        assert NotionBlockParser.parse_notion_timestamp("not-a-date") is None
        assert NotionBlockParser.parse_notion_timestamp(None) is None

    def test_parse_comment_missing_id(self):
        parser = _p()
        assert parser.parse_notion_comment_to_block_comment({}) is None

    def test_parse_comment_success(self):
        parser = _p()
        comment = parser.parse_notion_comment_to_block_comment(
            {
                "id": "c1",
                "rich_text": [{"plain_text": "Hello"}],
                "created_by": {"id": "u1"},
                "created_time": "2025-01-01T00:00:00.000Z",
                "last_edited_time": "2025-01-02T00:00:00.000Z",
                "discussion_id": "d1",
            },
            author_name="Author",
            comment_attachments=[
                CommentAttachment(name="file.pdf", id="att-1"),
            ],
        )
        assert comment is not None
        assert comment.text == "Hello"
        assert comment.author_name == "Author"

    def test_parse_comment_exception(self):
        parser = _p()
        bad = MagicMock()
        bad.get = MagicMock(side_effect=RuntimeError("fail"))
        assert parser.parse_notion_comment_to_block_comment(bad) is None

    def test_create_comment_group_and_thread(self):
        parser = _p()
        bc = BlockComment(
            text="Hi",
            format=DataFormat.TXT,
            author_id="u1",
            author_name="Author",
            created_at=datetime(2025, 1, 1),
        )
        group = parser.create_comment_group(
            bc, group_index=0, parent_group_index=1, source_id="c1",
            attachment_block_indices=[BlockContainerIndex(block_index=0)],
        )
        assert group.sub_type == GroupSubType.COMMENT
        assert group.children is not None

        thread = parser.create_comment_thread_group(
            "d1", group_index=1,
            comment_group_indices=[BlockContainerIndex(block_group_index=0)],
        )
        assert thread.sub_type == GroupSubType.COMMENT_THREAD
