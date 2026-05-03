"""Deep sync loop tests for Notion block_parser.py - remaining block type handlers."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.models.blocks import BlockSubType, BlockType, DataFormat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parser():
    logger = logging.getLogger("test.notion_parser_deep")
    return NotionBlockParser(logger=logger)


def _make_block(block_type, type_data=None, block_id="block-123",
                created_time="2025-01-01T00:00:00.000Z",
                last_edited_time="2025-06-01T00:00:00.000Z",
                has_children=False):
    block = {
        "id": block_id,
        "type": block_type,
        "created_time": created_time,
        "last_edited_time": last_edited_time,
        "has_children": has_children,
    }
    if type_data is not None:
        block[block_type] = type_data
    return block


def _make_rich_text(text="Hello world"):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


# ---------------------------------------------------------------------------
# Paragraph block
# ---------------------------------------------------------------------------

class TestParseParagraph:
    async def test_regular_paragraph(self, parser):
        block = _make_block("paragraph", {"rich_text": _make_rich_text("Test text")})
        result = await parser._parse_paragraph(block, block["paragraph"], None, 0)
        assert result.sub_type == BlockSubType.PARAGRAPH
        assert "Test text" in result.data

    async def test_empty_paragraph(self, parser):
        block = _make_block("paragraph", {"rich_text": []})
        result = await parser._parse_paragraph(block, block["paragraph"], None, 0)
        assert result.data == ""

    async def test_paragraph_with_link_mention(self, parser):
        rich_text = [{
            "type": "mention",
            "plain_text": "Link Title",
            "mention": {
                "type": "link_mention",
                "link_mention": {"href": "https://example.com", "title": "Link Title"},
            },
        }]
        block = _make_block("paragraph", {"rich_text": rich_text})
        result = await parser._parse_paragraph(block, block["paragraph"], None, 0)
        assert result.sub_type == BlockSubType.LINK

    async def test_paragraph_link_mention_no_title(self, parser):
        rich_text = [{
            "type": "mention",
            "plain_text": "https://example.com",
            "mention": {
                "type": "link_mention",
                "link_mention": {"href": "https://example.com"},
            },
        }]
        block = _make_block("paragraph", {"rich_text": rich_text})
        result = await parser._parse_paragraph(block, block["paragraph"], None, 0)
        assert result.sub_type == BlockSubType.LINK


# ---------------------------------------------------------------------------
# Heading blocks
# ---------------------------------------------------------------------------

class TestParseHeadings:
    async def test_heading_1(self, parser):
        block = _make_block("heading_1", {"rich_text": _make_rich_text("Title")})
        result = await parser._parse_heading_1(block, block["heading_1"], None, 0)
        assert result.sub_type == BlockSubType.HEADING
        assert result.name == "H1"

    async def test_heading_2(self, parser):
        block = _make_block("heading_2", {"rich_text": _make_rich_text("Subtitle")})
        result = await parser._parse_heading_2(block, block["heading_2"], None, 0)
        assert result.name == "H2"

    async def test_heading_3(self, parser):
        block = _make_block("heading_3", {"rich_text": _make_rich_text("Sub-subtitle")})
        result = await parser._parse_heading_3(block, block["heading_3"], None, 0)
        assert result.name == "H3"

    async def test_heading_empty_text(self, parser):
        block = _make_block("heading_1", {"rich_text": []})
        result = await parser._parse_heading(block, block["heading_1"], None, 0, level=1)
        assert result.data == ""


# ---------------------------------------------------------------------------
# Quote block
# ---------------------------------------------------------------------------

class TestParseQuote:
    async def test_basic_quote(self, parser):
        block = _make_block("quote", {"rich_text": _make_rich_text("Wise words")})
        result = await parser._parse_quote(block, block["quote"], None, 0)
        assert result.sub_type == BlockSubType.QUOTE
        assert "> Wise words" in result.data

    async def test_empty_quote(self, parser):
        block = _make_block("quote", {"rich_text": []})
        result = await parser._parse_quote(block, block["quote"], None, 0)
        assert result.data == ""


# ---------------------------------------------------------------------------
# Callout block
# ---------------------------------------------------------------------------

class TestParseCallout:
    async def test_callout_with_emoji_icon(self, parser):
        block = _make_block("callout", {
            "rich_text": _make_rich_text("Important note"),
            "icon": {"type": "emoji", "emoji": "💡"},
        })
        result = await parser._parse_callout(block, block["callout"], None, 0)
        assert result.sub_type == BlockSubType.PARAGRAPH

    async def test_callout_no_icon(self, parser):
        block = _make_block("callout", {
            "rich_text": _make_rich_text("No icon callout"),
            "icon": None,
        })
        result = await parser._parse_callout(block, block["callout"], None, 0)
        assert result.sub_type == BlockSubType.PARAGRAPH

    async def test_callout_with_external_icon(self, parser):
        block = _make_block("callout", {
            "rich_text": _make_rich_text("External icon"),
            "icon": {"type": "external", "external": {"url": "https://example.com/icon.png"}},
        })
        result = await parser._parse_callout(block, block["callout"], None, 0)
        assert result is not None


# ---------------------------------------------------------------------------
# List blocks
# ---------------------------------------------------------------------------

class TestParseListItems:
    async def test_bulleted_list_item(self, parser):
        block = _make_block("bulleted_list_item", {"rich_text": _make_rich_text("Item 1")})
        result = await parser._parse_bulleted_list_item(block, block["bulleted_list_item"], None, 0)
        assert result.type == BlockType.TEXT

    async def test_numbered_list_item(self, parser):
        block = _make_block("numbered_list_item", {"rich_text": _make_rich_text("Step 1")})
        result = await parser._parse_numbered_list_item(block, block["numbered_list_item"], None, 0)
        assert result.type == BlockType.TEXT

    async def test_to_do_item_checked(self, parser):
        block = _make_block("to_do", {
            "rich_text": _make_rich_text("Done task"),
            "checked": True,
        })
        result = await parser._parse_to_do(block, block["to_do"], None, 0)
        assert result is not None

    async def test_to_do_item_unchecked(self, parser):
        block = _make_block("to_do", {
            "rich_text": _make_rich_text("Pending task"),
            "checked": False,
        })
        result = await parser._parse_to_do(block, block["to_do"], None, 0)
        assert result is not None


# ---------------------------------------------------------------------------
# Code block
# ---------------------------------------------------------------------------

class TestParseCode:
    async def test_code_block(self, parser):
        block = _make_block("code", {
            "rich_text": _make_rich_text("print('hello')"),
            "language": "python",
            "caption": [],
        })
        result = await parser._parse_code(block, block["code"], None, 0)
        assert result.sub_type == BlockSubType.CODE

    async def test_code_block_no_language(self, parser):
        block = _make_block("code", {
            "rich_text": _make_rich_text("some code"),
            "language": "",
            "caption": [],
        })
        result = await parser._parse_code(block, block["code"], None, 0)
        assert result is not None


# ---------------------------------------------------------------------------
# Divider block
# ---------------------------------------------------------------------------

class TestParseDivider:
    async def test_divider(self, parser):
        block = _make_block("divider", {})
        result = await parser._parse_divider(block, block["divider"], None, 0)
        assert result.sub_type == BlockSubType.DIVIDER


# ---------------------------------------------------------------------------
# Child page and child database
# ---------------------------------------------------------------------------

class TestParseChildEntities:
    async def test_child_page(self, parser):
        block = _make_block("child_page", {"title": "Sub Page"})
        result = await parser._parse_child_page(block, block["child_page"], None, 0)
        assert result is not None

    async def test_child_database(self, parser):
        block = _make_block("child_database", {"title": "My DB"})
        result = await parser._parse_child_database(block, block["child_database"], None, 0)
        assert result is not None


# ---------------------------------------------------------------------------
# Bookmark block
# ---------------------------------------------------------------------------

class TestParseBookmark:
    async def test_bookmark_with_url(self, parser):
        block = _make_block("bookmark", {
            "url": "https://example.com",
            "caption": _make_rich_text("Example"),
        })
        result = await parser._parse_bookmark(block, block["bookmark"], None, 0)
        assert result.sub_type == BlockSubType.LINK

    async def test_bookmark_no_url(self, parser):
        block = _make_block("bookmark", {"url": "", "caption": []})
        result = await parser._parse_bookmark(block, block["bookmark"], None, 0)
        assert result is not None


# ---------------------------------------------------------------------------
# Embed / Link Preview
# ---------------------------------------------------------------------------

class TestParseEmbed:
    async def test_embed_with_url(self, parser):
        block = _make_block("embed", {
            "url": "https://example.com/embed",
            "caption": [],
        })
        result = await parser._parse_embed(block, block["embed"], None, 0)
        assert result is not None

    async def test_link_preview(self, parser):
        block = _make_block("link_preview", {
            "url": "https://example.com/preview",
        })
        result = await parser._parse_link_preview(block, block["link_preview"], None, 0)
        assert result is not None


# ---------------------------------------------------------------------------
# Equation block
# ---------------------------------------------------------------------------

class TestParseEquation:
    async def test_equation(self, parser):
        block = _make_block("equation", {"expression": "E = mc^2"})
        result = await parser._parse_equation(block, block["equation"], None, 0)
        assert result is not None
        assert "E = mc^2" in result.data


# ---------------------------------------------------------------------------
# Breadcrumb / Table of Contents
# ---------------------------------------------------------------------------

class TestParseNavBlocks:
    async def test_breadcrumb(self, parser):
        block = _make_block("breadcrumb", {})
        result = await parser._parse_breadcrumb(block, block["breadcrumb"], None, 0)
        assert result is None

    async def test_table_of_contents(self, parser):
        block = _make_block("table_of_contents", {})
        result = await parser._parse_table_of_contents(block, block["table_of_contents"], None, 0)
        assert result is None


# ---------------------------------------------------------------------------
# Utility methods
# ---------------------------------------------------------------------------

class TestParserUtilities:
    def test_normalize_url_empty(self, parser):
        assert parser._normalize_url("") is None
        assert parser._normalize_url(None) is None
        assert parser._normalize_url("   ") is None

    def test_normalize_url_valid(self, parser):
        assert parser._normalize_url("https://example.com") == "https://example.com"

    def test_construct_block_url(self, parser):
        url = parser._construct_block_url("https://notion.so/page", "block-id-123")
        assert url == "https://notion.so/page#blockid123"

    def test_construct_block_url_no_page(self, parser):
        assert parser._construct_block_url(None, "block-id") is None

    def test_construct_block_url_no_block(self, parser):
        assert parser._construct_block_url("https://notion.so/page", None) is None

    def test_extract_plain_text(self, parser):
        arr = [{"plain_text": "Hello"}, {"plain_text": " World"}]
        assert parser.extract_plain_text(arr) == "Hello World"

    def test_extract_plain_text_empty(self, parser):
        assert parser.extract_plain_text([]) == ""

    def test_parse_timestamp_valid(self, parser):
        result = parser._parse_timestamp("2025-01-01T00:00:00.000Z")
        assert result is not None

    def test_parse_timestamp_none(self, parser):
        assert parser._parse_timestamp(None) is None

    def test_parse_timestamp_invalid(self, parser):
        result = parser._parse_timestamp("not-a-timestamp")
        assert result is None
