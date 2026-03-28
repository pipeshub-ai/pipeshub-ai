"""Full coverage tests for app.connectors.sources.notion.block_parser."""

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
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
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None

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
