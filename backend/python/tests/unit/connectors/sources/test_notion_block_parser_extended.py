"""Extended tests for NotionBlockParser covering all block types and edge cases."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockSubType,
    BlockType,
    DataFormat,
    GroupType,
    GroupSubType,
)


def _make_parser():
    logger = MagicMock()
    return NotionBlockParser(logger=logger)


def _make_notion_block(block_type, type_data, block_id="block-1",
                       has_children=False, archived=False, in_trash=False,
                       created_time="2025-01-01T00:00:00Z",
                       last_edited_time="2025-01-15T00:00:00Z"):
    block = {
        "id": block_id,
        "type": block_type,
        block_type: type_data,
        "has_children": has_children,
        "archived": archived,
        "in_trash": in_trash,
        "created_time": created_time,
        "last_edited_time": last_edited_time,
    }
    return block


# ===========================================================================
# Static/helper methods
# ===========================================================================

class TestNormalizeUrl:
    def test_none_returns_none(self):
        assert NotionBlockParser._normalize_url(None) is None

    def test_empty_string_returns_none(self):
        assert NotionBlockParser._normalize_url("") is None

    def test_whitespace_returns_none(self):
        assert NotionBlockParser._normalize_url("   ") is None

    def test_valid_url_returns_url(self):
        assert NotionBlockParser._normalize_url("https://example.com") == "https://example.com"


class TestConstructBlockUrl:
    def test_no_page_url_returns_none(self):
        parser = _make_parser()
        assert parser._construct_block_url(None, "block-id") is None

    def test_no_block_id_returns_none(self):
        parser = _make_parser()
        assert parser._construct_block_url("https://notion.so/page", None) is None

    def test_valid_inputs(self):
        parser = _make_parser()
        result = parser._construct_block_url("https://notion.so/page", "abc-def-123")
        assert result == "https://notion.so/page#abcdef123"


class TestExtractRichTextFromBlockData:
    def test_non_dict_returns_none(self):
        assert NotionBlockParser.extract_rich_text_from_block_data("string") is None

    def test_empty_type_returns_none(self):
        assert NotionBlockParser.extract_rich_text_from_block_data({"type": ""}) is None

    def test_non_dict_type_data_returns_none(self):
        assert NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph", "paragraph": "not-a-dict"
        }) is None

    def test_rich_text_extracted(self):
        result = NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "Hello"}]},
        })
        assert result == [{"plain_text": "Hello"}]

    def test_fallback_text_list(self):
        result = NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph",
            "paragraph": {"text": [{"plain_text": "Fallback"}]},
        })
        assert result == [{"plain_text": "Fallback"}]

    def test_fallback_text_dict_with_rich_text(self):
        result = NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph",
            "paragraph": {"text": {"rich_text": [{"plain_text": "Nested"}]}},
        })
        assert result == [{"plain_text": "Nested"}]

    def test_no_rich_text_returns_none(self):
        result = NotionBlockParser.extract_rich_text_from_block_data({
            "type": "paragraph",
            "paragraph": {},
        })
        assert result is None


# ===========================================================================
# Rich text extraction
# ===========================================================================

class TestExtractMarkdownText:
    def test_equation_type(self):
        parser = _make_parser()
        rich_text = [{"type": "equation", "equation": {"expression": "E=mc^2"}}]
        result = parser.extract_rich_text(rich_text)
        assert "$$E=mc^2$$" in result

    def test_mention_link_mention(self):
        parser = _make_parser()
        rich_text = [{
            "type": "mention",
            "mention": {
                "type": "link_mention",
                "link_mention": {"href": "https://example.com", "title": "Example"},
            },
            "plain_text": "Example",
        }]
        result = parser.extract_rich_text(rich_text)
        assert "[Example](https://example.com)" in result

    def test_mention_link_mention_no_href(self):
        parser = _make_parser()
        rich_text = [{
            "type": "mention",
            "mention": {"type": "link_mention", "link_mention": {}},
            "plain_text": "Fallback Text",
        }]
        result = parser.extract_rich_text(rich_text)
        assert "Fallback Text" in result

    def test_mention_other_type(self):
        parser = _make_parser()
        rich_text = [{
            "type": "mention",
            "mention": {"type": "user"},
            "plain_text": "@John",
        }]
        result = parser.extract_rich_text(rich_text)
        assert "@John" in result

    def test_unknown_rich_text_type(self):
        parser = _make_parser()
        rich_text = [{"type": "unknown_type", "plain_text": "Unknown"}]
        result = parser.extract_rich_text(rich_text)
        assert "Unknown" in result

    def test_text_with_inline_link(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "Click here",
            "href": "https://example.com",
            "annotations": {},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "[Click here](https://example.com)" in result

    def test_text_link_from_text_obj(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "Click",
            "text": {"link": {"url": "https://example.com"}},
            "annotations": {},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "[Click](https://example.com)" in result

    def test_code_annotation(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "code_here",
            "annotations": {"code": True, "bold": False, "italic": False,
                           "strikethrough": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "`code_here`" in result

    def test_code_with_backticks_in_content(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "code`with`backtick",
            "annotations": {"code": True, "bold": False, "italic": False,
                           "strikethrough": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "``" in result  # Uses longer fence

    def test_code_with_consecutive_backticks(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "code``double",
            "annotations": {"code": True, "bold": False, "italic": False,
                           "strikethrough": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        # Should use even longer fence
        assert "```" in result or "````" in result

    def test_strikethrough_annotation(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "deleted",
            "annotations": {"strikethrough": True, "bold": False, "italic": False,
                           "code": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "~~deleted~~" in result

    def test_underline_annotation(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "underlined",
            "annotations": {"underline": True, "bold": False, "italic": False,
                           "code": False, "strikethrough": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "<u>underlined</u>" in result

    def test_color_annotation(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": "colored",
            "annotations": {"bold": False, "italic": False, "code": False,
                           "strikethrough": False, "underline": True, "color": "red"},
        }]
        result = parser.extract_rich_text(rich_text)
        assert 'style="color: red"' in result

    def test_whitespace_preservation_with_formatting(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "plain_text": " bold ",
            "annotations": {"bold": True, "italic": False, "code": False,
                           "strikethrough": False, "underline": False, "color": "default"},
        }]
        result = parser.extract_rich_text(rich_text)
        # Leading space should be outside markers
        assert " **bold** " in result

    def test_text_content_from_text_dict(self):
        parser = _make_parser()
        rich_text = [{
            "type": "text",
            "text": {"content": "from text dict"},
            "annotations": {},
        }]
        result = parser.extract_rich_text(rich_text)
        assert "from text dict" in result


# ===========================================================================
# Block parsers
# ===========================================================================

class TestParseBlock:
    async def test_archived_block_skipped(self):
        parser = _make_parser()
        block = _make_notion_block("paragraph", {"rich_text": []}, archived=True)
        result = await parser.parse_block(block)
        assert result == (None, None, [])

    async def test_trashed_block_skipped(self):
        parser = _make_parser()
        block = _make_notion_block("paragraph", {"rich_text": []}, in_trash=True)
        result = await parser.parse_block(block)
        assert result == (None, None, [])

    async def test_unsupported_block_skipped(self):
        parser = _make_parser()
        block = _make_notion_block("unsupported", {})
        result = await parser.parse_block(block)
        assert result == (None, None, [])

    async def test_paragraph_block(self):
        parser = _make_parser()
        block = _make_notion_block("paragraph", {
            "rich_text": [{"type": "text", "plain_text": "Hello world", "annotations": {}}],
        })
        blk, grp, children = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.type == BlockType.TEXT
        assert blk.sub_type == BlockSubType.PARAGRAPH
        assert "Hello world" in blk.data

    async def test_paragraph_with_link_mention(self):
        parser = _make_parser()
        block = _make_notion_block("paragraph", {
            "rich_text": [{
                "type": "mention",
                "mention": {
                    "type": "link_mention",
                    "link_mention": {"href": "https://example.com", "title": "Link"},
                },
                "plain_text": "Link",
            }],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.sub_type == BlockSubType.LINK

    async def test_empty_paragraph_skipped(self):
        parser = _make_parser()
        block = _make_notion_block("paragraph", {"rich_text": []})
        blk, grp, _ = await parser.parse_block(block, block_index=0)
        assert blk is None  # Empty data blocks are skipped

    async def test_heading_1(self):
        parser = _make_parser()
        block = _make_notion_block("heading_1", {
            "rich_text": [{"type": "text", "plain_text": "H1 Title", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.HEADING
        assert blk.name == "H1"

    async def test_heading_2(self):
        parser = _make_parser()
        block = _make_notion_block("heading_2", {
            "rich_text": [{"type": "text", "plain_text": "H2 Title", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.name == "H2"

    async def test_heading_3(self):
        parser = _make_parser()
        block = _make_notion_block("heading_3", {
            "rich_text": [{"type": "text", "plain_text": "H3 Title", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.name == "H3"

    async def test_quote_block(self):
        parser = _make_parser()
        block = _make_notion_block("quote", {
            "rich_text": [{"type": "text", "plain_text": "A quote", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.QUOTE
        assert "> A quote" in blk.data

    async def test_callout_with_emoji_icon(self):
        parser = _make_parser()
        block = _make_notion_block("callout", {
            "rich_text": [{"type": "text", "plain_text": "Important", "annotations": {}}],
            "icon": {"type": "emoji", "emoji": "⚠️"},
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert "Important" in blk.data

    async def test_callout_with_external_icon(self):
        parser = _make_parser()
        block = _make_notion_block("callout", {
            "rich_text": [{"type": "text", "plain_text": "Note", "annotations": {}}],
            "icon": {"type": "external", "external": {"url": "https://example.com/icon.png"}},
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.link_metadata is not None

    async def test_callout_with_file_icon(self):
        parser = _make_parser()
        block = _make_notion_block("callout", {
            "rich_text": [{"type": "text", "plain_text": "Attached", "annotations": {}}],
            "icon": {"type": "file", "file": {"url": "https://notion.so/file.png"}},
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None

    async def test_bulleted_list_item(self):
        parser = _make_parser()
        block = _make_notion_block("bulleted_list_item", {
            "rich_text": [{"type": "text", "plain_text": "Item 1", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LIST_ITEM
        assert "- Item 1" in blk.data

    async def test_numbered_list_item(self):
        parser = _make_parser()
        block = _make_notion_block("numbered_list_item", {
            "rich_text": [{"type": "text", "plain_text": "Step 1", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert "1. Step 1" in blk.data

    async def test_todo_checked(self):
        parser = _make_parser()
        block = _make_notion_block("to_do", {
            "rich_text": [{"type": "text", "plain_text": "Done task", "annotations": {}}],
            "checked": True,
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert "[x]" in blk.data

    async def test_todo_unchecked(self):
        parser = _make_parser()
        block = _make_notion_block("to_do", {
            "rich_text": [{"type": "text", "plain_text": "Pending task", "annotations": {}}],
            "checked": False,
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert "[ ]" in blk.data

    async def test_code_block(self):
        parser = _make_parser()
        block = _make_notion_block("code", {
            "rich_text": [{"plain_text": "print('hello')"}],
            "language": "python",
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CODE
        assert "python" in blk.name

    async def test_divider_block(self):
        parser = _make_parser()
        block = _make_notion_block("divider", {})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.data == "---"

    async def test_child_page_block(self):
        parser = _make_parser()
        block = _make_notion_block("child_page", {"title": "My Page"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD
        assert blk.data == "My Page"

    async def test_child_database_block(self):
        parser = _make_parser()
        block = _make_notion_block("child_database", {"title": "My DB"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.data == "My DB"
        assert blk.name == "DATASOURCE"

    async def test_bookmark_block(self):
        parser = _make_parser()
        block = _make_notion_block("bookmark", {
            "url": "https://example.com",
            "caption": [{"type": "text", "plain_text": "Example", "annotations": {}}],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LINK

    async def test_link_preview_block(self):
        parser = _make_parser()
        block = _make_notion_block("link_preview", {"url": "https://example.com"})
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LINK

    async def test_embed_block(self):
        parser = _make_parser()
        block = _make_notion_block("embed", {
            "url": "https://example.com/embed",
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LINK

    async def test_image_block(self):
        parser = _make_parser()
        block = _make_notion_block("image", {
            "type": "external",
            "external": {"url": "https://example.com/img.png"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.type == BlockType.IMAGE

    async def test_video_external_embed(self):
        parser = _make_parser()
        block = _make_notion_block("video", {
            "type": "external",
            "external": {"url": "https://youtube.com/watch?v=123"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LINK

    async def test_video_direct_file(self):
        parser = _make_parser()
        block = _make_notion_block("video", {
            "type": "file",
            "file": {"url": "https://notion.so/video.mp4"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    async def test_audio_external_embed(self):
        parser = _make_parser()
        block = _make_notion_block("audio", {
            "type": "external",
            "external": {"url": "https://spotify.com/track/123"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.LINK

    async def test_file_block(self):
        parser = _make_parser()
        block = _make_notion_block("file", {
            "type": "file",
            "file": {"url": "https://notion.so/doc.pdf"},
            "caption": [],
            "name": "doc.pdf",
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    async def test_pdf_block(self):
        parser = _make_parser()
        block = _make_notion_block("pdf", {
            "type": "file",
            "file": {"url": "https://notion.so/report.pdf"},
            "caption": [],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk.sub_type == BlockSubType.CHILD_RECORD

    async def test_column_list_block(self):
        parser = _make_parser()
        block = _make_notion_block("column_list", {})
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.type == GroupType.COLUMN_LIST

    async def test_column_block_with_width(self):
        parser = _make_parser()
        block = _make_notion_block("column", {"width_ratio": 0.5}, has_children=True)
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.type == GroupType.COLUMN

    async def test_toggle_block(self):
        parser = _make_parser()
        block = _make_notion_block("toggle", {
            "rich_text": [{"type": "text", "plain_text": "Toggle title", "annotations": {}}],
        })
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.sub_type == GroupSubType.TOGGLE

    async def test_synced_block_original(self):
        parser = _make_parser()
        block = _make_notion_block("synced_block", {"synced_from": None})
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert "Original" in grp.name

    async def test_synced_block_reference(self):
        parser = _make_parser()
        block = _make_notion_block("synced_block", {
            "synced_from": {"type": "block_id", "block_id": "ref-block-123"},
        })
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert "Reference" in grp.name

    async def test_table_block(self):
        parser = _make_parser()
        block = _make_notion_block("table", {
            "table_width": 3,
            "has_column_header": True,
            "has_row_header": True,
        })
        _, grp, _ = await parser.parse_block(block, block_index=0)
        assert grp is not None
        assert grp.type == GroupType.TABLE
        assert grp.table_metadata.num_of_cols == 3

    async def test_table_row_block(self):
        parser = _make_parser()
        block = _make_notion_block("table_row", {
            "cells": [
                [{"type": "text", "plain_text": "Cell1", "annotations": {}}],
                [{"type": "text", "plain_text": "Cell2", "annotations": {}}],
            ],
        })
        blk, _, _ = await parser.parse_block(block, block_index=0)
        assert blk is not None
        assert blk.type == BlockType.TABLE_ROW

    async def test_unknown_block_type(self):
        parser = _make_parser()
        block = _make_notion_block("totally_new_type", {})
        result = await parser.parse_block(block, block_index=0)
        assert result == (None, None, [])

    async def test_parser_error_returns_none_tuple(self):
        parser = _make_parser()
        block = _make_notion_block("paragraph", None)
        result = await parser.parse_block(block, block_index=0)
        assert result == (None, None, [])


# ===========================================================================
# _is_embed_platform_url
# ===========================================================================

class TestIsEmbedPlatformUrl:
    def test_none_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url(None) is False

    def test_youtube_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://youtube.com/watch?v=123") is True

    def test_youtu_be_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://youtu.be/123") is True

    def test_vimeo_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://vimeo.com/123") is True

    def test_spotify_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://spotify.com/track/123") is True

    def test_direct_mp4_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://example.com/video.mp4") is False

    def test_direct_mp3_url(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://example.com/audio.mp3") is False

    def test_mp4_with_query_params(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://example.com/video.mp4?token=abc") is False

    def test_unknown_url_defaults_to_embed(self):
        parser = _make_parser()
        assert parser._is_embed_platform_url("https://unknown-service.com/content") is True


# ===========================================================================
# _extract_relations_and_people
# ===========================================================================

class TestExtractRelationsAndPeople:
    def test_relation_property(self):
        parser = _make_parser()
        properties = {
            "Related": {"type": "relation", "relation": [{"id": "page-1"}, {"id": "page-2"}]},
        }
        relations, people = parser._extract_relations_and_people(properties)
        assert relations == ["page-1", "page-2"]
        assert people == []

    def test_people_property(self):
        parser = _make_parser()
        properties = {
            "Assignee": {"type": "people", "people": [{"id": "user-1"}]},
        }
        relations, people = parser._extract_relations_and_people(properties)
        assert relations == []
        assert people == ["user-1"]

    def test_created_by_property(self):
        parser = _make_parser()
        properties = {
            "Created By": {"type": "created_by", "created_by": {"id": "user-1"}},
        }
        _, people = parser._extract_relations_and_people(properties)
        assert "user-1" in people

    def test_last_edited_by_property(self):
        parser = _make_parser()
        properties = {
            "Edited By": {"type": "last_edited_by", "last_edited_by": {"id": "user-2"}},
        }
        _, people = parser._extract_relations_and_people(properties)
        assert "user-2" in people

    def test_rollup_with_people(self):
        parser = _make_parser()
        properties = {
            "Rollup": {
                "type": "rollup",
                "rollup": {
                    "type": "array",
                    "array": [
                        {"type": "people", "people": [{"id": "user-3"}]},
                    ],
                },
            },
        }
        _, people = parser._extract_relations_and_people(properties)
        assert "user-3" in people

    def test_rollup_with_relation(self):
        parser = _make_parser()
        properties = {
            "Rollup": {
                "type": "rollup",
                "rollup": {
                    "type": "array",
                    "array": [
                        {"type": "relation", "id": "page-rel-1"},
                    ],
                },
            },
        }
        relations, _ = parser._extract_relations_and_people(properties)
        assert "page-rel-1" in relations

    def test_non_dict_property_skipped(self):
        parser = _make_parser()
        properties = {"Bad": "not-a-dict"}
        relations, people = parser._extract_relations_and_people(properties)
        assert relations == []
        assert people == []


# ===========================================================================
# parse_data_source_to_blocks
# ===========================================================================

class TestParseDataSourceToBlocks:
    async def test_basic_data_source(self):
        parser = _make_parser()
        metadata = {
            "properties": {"Name": {"type": "title"}, "Status": {"type": "select"}},
            "description": [],
        }
        rows = [
            {
                "id": "row-1",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Task 1"}]},
                    "Status": {"type": "select", "select": {"name": "Done"}},
                },
            },
        ]
        blocks, block_groups = await parser.parse_data_source_to_blocks(
            metadata, rows, "ds-1",
        )
        assert len(block_groups) == 1
        assert block_groups[0].type == GroupType.TABLE
        assert len(blocks) >= 2  # header + at least one data row

    async def test_data_source_with_callbacks(self):
        parser = _make_parser()
        metadata = {
            "properties": {"Name": {"type": "title"}, "Ref": {"type": "relation"}},
            "description": [{"type": "text", "plain_text": "Description"}],
        }
        rows = [
            {
                "id": "row-1",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Task"}]},
                    "Ref": {"type": "relation", "relation": [{"id": "page-ref"}]},
                },
            },
        ]
        mock_child = MagicMock()
        mock_child.child_name = "Referenced Page"
        mock_child.child_type = MagicMock(value="RECORD")
        record_callback = AsyncMock(return_value=mock_child)
        user_callback = AsyncMock(return_value=None)

        blocks, block_groups = await parser.parse_data_source_to_blocks(
            metadata, rows, "ds-1",
            get_record_child_callback=record_callback,
            get_user_child_callback=user_callback,
        )
        assert len(blocks) >= 2


# ===========================================================================
# _extract_property_value_with_resolution
# ===========================================================================

class TestExtractPropertyValueWithResolution:
    async def test_relation_with_callback(self):
        parser = _make_parser()
        mock_child = MagicMock()
        mock_child.child_name = "Page Title"
        callback = AsyncMock(return_value=mock_child)
        prop = {"type": "relation", "relation": [{"id": "page-1"}]}
        result = await parser._extract_property_value_with_resolution(prop, callback)
        assert "Page Title" in result

    async def test_relation_without_callback(self):
        parser = _make_parser()
        prop = {"type": "relation", "relation": [{"id": "page-1"}]}
        result = await parser._extract_property_value_with_resolution(prop)
        assert "page-1" in result

    async def test_relation_empty(self):
        parser = _make_parser()
        prop = {"type": "relation", "relation": []}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == ""

    async def test_people_with_callback(self):
        parser = _make_parser()
        mock_child = MagicMock()
        mock_child.child_name = "John Doe"
        callback = AsyncMock(return_value=mock_child)
        prop = {"type": "people", "people": [{"id": "user-1"}]}
        result = await parser._extract_property_value_with_resolution(
            prop, get_user_child_callback=callback
        )
        assert "John Doe" in result

    async def test_people_without_callback(self):
        parser = _make_parser()
        prop = {"type": "people", "people": [{"id": "user-1"}]}
        result = await parser._extract_property_value_with_resolution(prop)
        assert "user-1" in result

    async def test_people_empty(self):
        parser = _make_parser()
        prop = {"type": "people", "people": []}
        result = await parser._extract_property_value_with_resolution(prop)
        assert result == ""


# ===========================================================================
# _extract_property_value
# ===========================================================================
class TestExtractPropertyValue:
    def test_title(self):
        parser = _make_parser()
        prop = {"type": "title", "title": [{"plain_text": "Hello"}, {"plain_text": " World"}]}
        assert parser._extract_property_value(prop) == "Hello World"

    def test_rich_text(self):
        parser = _make_parser()
        prop = {"type": "rich_text", "rich_text": [{"plain_text": "Some text"}]}
        assert parser._extract_property_value(prop) == "Some text"

    def test_number(self):
        parser = _make_parser()
        assert parser._extract_property_value({"type": "number", "number": 42}) == "42"

    def test_number_none(self):
        parser = _make_parser()
        assert parser._extract_property_value({"type": "number", "number": None}) == ""

    def test_select(self):
        parser = _make_parser()
        prop = {"type": "select", "select": {"name": "Option A"}}
        assert parser._extract_property_value(prop) == "Option A"

    def test_select_none(self):
        parser = _make_parser()
        prop = {"type": "select", "select": None}
        assert parser._extract_property_value(prop) == ""

    def test_multi_select(self):
        parser = _make_parser()
        prop = {"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]}
        assert parser._extract_property_value(prop) == "A, B"

    def test_status(self):
        parser = _make_parser()
        prop = {"type": "status", "status": {"name": "In Progress"}}
        assert parser._extract_property_value(prop) == "In Progress"

    def test_status_none(self):
        parser = _make_parser()
        prop = {"type": "status", "status": None}
        assert parser._extract_property_value(prop) == ""

    def test_date_with_end(self):
        parser = _make_parser()
        prop = {"type": "date", "date": {"start": "2025-01-01", "end": "2025-01-31"}}
        assert parser._extract_property_value(prop) == "2025-01-01 - 2025-01-31"

    def test_date_no_end(self):
        parser = _make_parser()
        prop = {"type": "date", "date": {"start": "2025-01-01"}}
        assert parser._extract_property_value(prop) == "2025-01-01"

    def test_date_none(self):
        parser = _make_parser()
        prop = {"type": "date", "date": None}
        assert parser._extract_property_value(prop) == ""

    def test_people(self):
        parser = _make_parser()
        prop = {"type": "people", "people": [{"id": "user-1"}, {"id": "user-2"}]}
        assert parser._extract_property_value(prop) == "user-1, user-2"

    def test_relation(self):
        parser = _make_parser()
        prop = {"type": "relation", "relation": [{"id": "page-1"}]}
        assert parser._extract_property_value(prop) == "page-1"

    def test_checkbox_true(self):
        parser = _make_parser()
        prop = {"type": "checkbox", "checkbox": True}
        assert "✓" in parser._extract_property_value(prop)

    def test_checkbox_false(self):
        parser = _make_parser()
        prop = {"type": "checkbox", "checkbox": False}
        assert "✗" in parser._extract_property_value(prop)

    def test_url(self):
        parser = _make_parser()
        prop = {"type": "url", "url": "https://example.com"}
        assert parser._extract_property_value(prop) == "https://example.com"

    def test_url_none(self):
        parser = _make_parser()
        prop = {"type": "url", "url": None}
        assert parser._extract_property_value(prop) == ""

    def test_email(self):
        parser = _make_parser()
        prop = {"type": "email", "email": "test@test.com"}
        assert parser._extract_property_value(prop) == "test@test.com"

    def test_phone_number(self):
        parser = _make_parser()
        prop = {"type": "phone_number", "phone_number": "+1234567890"}
        assert parser._extract_property_value(prop) == "+1234567890"

    def test_formula_string(self):
        parser = _make_parser()
        prop = {"type": "formula", "formula": {"type": "string", "string": "result"}}
        assert parser._extract_property_value(prop) == "result"

    def test_formula_number(self):
        parser = _make_parser()
        prop = {"type": "formula", "formula": {"type": "number", "number": 99}}
        assert parser._extract_property_value(prop) == "99"

    def test_formula_boolean(self):
        parser = _make_parser()
        prop = {"type": "formula", "formula": {"type": "boolean", "boolean": True}}
        assert "✓" in parser._extract_property_value(prop)

    def test_formula_date(self):
        parser = _make_parser()
        prop = {"type": "formula", "formula": {"type": "date", "date": {"start": "2025-01-01"}}}
        assert parser._extract_property_value(prop) == "2025-01-01"

    def test_formula_unknown_type(self):
        parser = _make_parser()
        prop = {"type": "formula", "formula": {"type": "unknown"}}
        assert parser._extract_property_value(prop) == ""

    def test_rollup_number(self):
        parser = _make_parser()
        prop = {"type": "rollup", "rollup": {"type": "number", "number": 10}}
        assert parser._extract_property_value(prop) == "10"

    def test_rollup_array(self):
        parser = _make_parser()
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [
                    {"type": "number", "number": 1},
                    {"type": "number", "number": 2},
                ]
            }
        }
        assert "1" in parser._extract_property_value(prop)

    def test_created_time(self):
        parser = _make_parser()
        prop = {"type": "created_time", "created_time": "2025-01-01T00:00:00Z"}
        assert parser._extract_property_value(prop) == "2025-01-01T00:00:00Z"

    def test_created_by(self):
        parser = _make_parser()
        prop = {"type": "created_by", "created_by": {"id": "user-1"}}
        assert parser._extract_property_value(prop) == "user-1"

    def test_last_edited_time(self):
        parser = _make_parser()
        prop = {"type": "last_edited_time", "last_edited_time": "2025-06-01T00:00:00Z"}
        assert parser._extract_property_value(prop) == "2025-06-01T00:00:00Z"

    def test_last_edited_by(self):
        parser = _make_parser()
        prop = {"type": "last_edited_by", "last_edited_by": {"id": "user-2"}}
        assert parser._extract_property_value(prop) == "user-2"

    def test_files(self):
        parser = _make_parser()
        prop = {"type": "files", "files": [{"name": "doc.pdf"}, {"name": "", "external": {"url": "https://ext.com/f.txt"}}]}
        result = parser._extract_property_value(prop)
        assert "doc.pdf" in result

    def test_unknown_type(self):
        parser = _make_parser()
        prop = {"type": "mystery", "mystery": "value"}
        result = parser._extract_property_value(prop)
        assert result == "value"


# ===========================================================================
# _parse_timestamp
# ===========================================================================
class TestParseTimestamp:
    def test_valid_timestamp(self):
        parser = _make_parser()
        result = parser._parse_timestamp("2025-01-01T00:00:00.000Z")
        assert result is not None
        assert isinstance(result, object)

    def test_none(self):
        parser = _make_parser()
        result = parser._parse_timestamp(None)
        assert result is None

    def test_empty(self):
        parser = _make_parser()
        result = parser._parse_timestamp("")
        assert result is None


# ===========================================================================
# post_process_blocks / _finalize_indices_and_metadata
# ===========================================================================
class TestPostProcessBlocks:
    def test_empty_blocks(self):
        parser = _make_parser()
        blocks = []
        block_groups = []
        parser.post_process_blocks(blocks, block_groups)

    def test_with_blocks_and_groups(self):
        parser = _make_parser()
        b1 = Block(index=0, type=BlockType.TEXT.value, sub_type=BlockSubType.PARAGRAPH.value,
                    data="text", format=DataFormat.MARKDOWN.value)
        bg1 = BlockGroup(index=0, name="group", type=GroupType.TEXT_SECTION.value)
        parser.post_process_blocks([b1], [bg1])
        # Should not crash; indices get finalized

    def test_list_items_get_indent(self):
        parser = _make_parser()
        b1 = Block(index=0, type=BlockType.BULLET_LIST.value,
                    sub_type=BlockSubType.LIST_ITEM.value,
                    data="item 1", format=DataFormat.MARKDOWN.value)
        b2 = Block(index=1, type=BlockType.BULLET_LIST.value,
                    sub_type=BlockSubType.LIST_ITEM.value,
                    data="item 2", format=DataFormat.MARKDOWN.value,
                    parent_index=0)
        bg = BlockGroup(index=0, name="list group", type=GroupType.LIST.value)
        parser.post_process_blocks([b1, b2], [bg])


# ===========================================================================
# _generate_data_source_markdown
# ===========================================================================
class TestGenerateDataSourceMarkdown:
    def test_basic(self):
        parser = _make_parser()
        columns = ["Name", "Value"]
        rows = [
            {"properties": {"Name": {"type": "title", "title": [{"plain_text": "A"}]}, "Value": {"type": "number", "number": 1}}},
            {"properties": {"Name": {"type": "title", "title": [{"plain_text": "B"}]}, "Value": {"type": "number", "number": 2}}},
        ]
        result = parser._generate_data_source_markdown(columns, rows)
        assert "Name" in result
        assert "Value" in result
        assert "A" in result
        assert "2" in result

    def test_empty_rows(self):
        parser = _make_parser()
        result = parser._generate_data_source_markdown(["Col1"], [])
        assert "Col1" in result

    def test_empty_columns(self):
        parser = _make_parser()
        result = parser._generate_data_source_markdown([], [])
        assert result == ""


# ===========================================================================
# _parse_equation
# ===========================================================================
class TestParseEquation:
    @pytest.mark.asyncio
    async def test_equation_block(self):
        parser = _make_parser()
        block = _make_notion_block("equation", {"expression": "E = mc^2"})
        result = await parser.parse_block(block, parent_page_url="https://notion.so/page")
        assert result is not None


# ===========================================================================
# _parse_breadcrumb / _parse_table_of_contents
# ===========================================================================
class TestParseBreadcrumbAndTOC:
    @pytest.mark.asyncio
    async def test_breadcrumb(self):
        parser = _make_parser()
        block = _make_notion_block("breadcrumb", {})
        result = await parser.parse_block(block, parent_page_url="https://notion.so/page")

    @pytest.mark.asyncio
    async def test_table_of_contents(self):
        parser = _make_parser()
        block = _make_notion_block("table_of_contents", {"color": "default"})
        result = await parser.parse_block(block, parent_page_url="https://notion.so/page")


# ===========================================================================
# _parse_link_to_page
# ===========================================================================
class TestParseLinkToPage:
    @pytest.mark.asyncio
    async def test_page_id_link(self):
        parser = _make_parser()
        block = _make_notion_block("link_to_page", {"type": "page_id", "page_id": "abc123"})
        result = await parser.parse_block(block, parent_page_url="https://notion.so/page")
        assert result is not None

    @pytest.mark.asyncio
    async def test_database_id_link(self):
        parser = _make_parser()
        block = _make_notion_block("link_to_page", {"type": "database_id", "database_id": "db123"})
        result = await parser.parse_block(block, parent_page_url="https://notion.so/page")
        assert result is not None


# ===========================================================================
# extract_plain_text
# ===========================================================================
class TestExtractPlainText:
    def test_empty_array(self):
        parser = _make_parser()
        result = parser.extract_plain_text([])
        assert result == ""

    def test_array_with_text(self):
        parser = _make_parser()
        result = parser.extract_plain_text([
            {"plain_text": "Hello "},
            {"plain_text": "World"}
        ])
        assert result == "Hello World"

    def test_missing_plain_text_key(self):
        parser = _make_parser()
        result = parser.extract_plain_text([{"text": "no plain_text key"}])
        assert result == ""


# ===========================================================================
# extract_rich_text with plain_text flag
# ===========================================================================
class TestExtractRichTextPlainFlag:
    def test_plain_text_mode(self):
        parser = _make_parser()
        result = parser.extract_rich_text(
            [{"type": "text", "plain_text": "Hello", "text": {"content": "Hello"}, "annotations": {}}],
            plain_text=True
        )
        assert result == "Hello"

    def test_markdown_mode(self):
        parser = _make_parser()
        result = parser.extract_rich_text(
            [{"type": "text", "plain_text": "Hello", "text": {"content": "Hello"}, "annotations": {"bold": True, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"}}],
            plain_text=False
        )
        assert "Hello" in result
