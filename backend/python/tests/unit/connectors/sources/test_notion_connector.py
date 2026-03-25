"""Tests for the Notion connector and block parser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.connectors.sources.notion.connector import NotionConnector
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockSubType,
    BlockType,
    DataFormat,
)
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a NotionConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    connector_id = "notion-conn-1"
    connector = NotionConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
    )
    return connector


def _make_parser():
    """Build a NotionBlockParser with mocked logger."""
    logger = MagicMock()
    return NotionBlockParser(logger=logger)


# ===================================================================
# NotionBlockParser - Rich Text Extraction
# ===================================================================

class TestNotionBlockParserRichText:
    def test_extract_plain_text_empty(self):
        parser = _make_parser()
        assert parser.extract_plain_text([]) == ""

    def test_extract_plain_text_single_item(self):
        parser = _make_parser()
        rich_text = [{"plain_text": "Hello world"}]
        assert parser.extract_plain_text(rich_text) == "Hello world"

    def test_extract_plain_text_multiple_items(self):
        parser = _make_parser()
        rich_text = [
            {"plain_text": "Hello "},
            {"plain_text": "world"},
        ]
        assert parser.extract_plain_text(rich_text) == "Hello world"

    def test_extract_plain_text_fallback_to_text_content(self):
        parser = _make_parser()
        rich_text = [{"text": {"content": "fallback text"}}]
        assert parser.extract_plain_text(rich_text) == "fallback text"

    def test_extract_rich_text_plain_mode(self):
        parser = _make_parser()
        rich_text = [
            {"plain_text": "Simple text", "type": "text", "annotations": {}},
        ]
        result = parser.extract_rich_text(rich_text, plain_text=True)
        assert result == "Simple text"

    def test_extract_rich_text_markdown_bold(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "text",
                "plain_text": "bold",
                "annotations": {"bold": True, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "**bold**" in result

    def test_extract_rich_text_markdown_italic(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "text",
                "plain_text": "italic",
                "annotations": {"bold": False, "italic": True, "code": False, "strikethrough": False, "underline": False, "color": "default"},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "*italic*" in result

    def test_extract_rich_text_markdown_code(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "text",
                "plain_text": "code",
                "annotations": {"bold": False, "italic": False, "code": True, "strikethrough": False, "underline": False, "color": "default"},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "`code`" in result

    def test_extract_rich_text_markdown_strikethrough(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "text",
                "plain_text": "deleted",
                "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": True, "underline": False, "color": "default"},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "~~deleted~~" in result

    def test_extract_rich_text_link(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "text",
                "plain_text": "click here",
                "href": "https://example.com",
                "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "[click here](https://example.com)" in result

    def test_extract_rich_text_equation(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "equation",
                "equation": {"expression": "E = mc^2"},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "$$E = mc^2$$" in result

    def test_extract_rich_text_mention_link(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "mention",
                "plain_text": "Google",
                "mention": {
                    "type": "link_mention",
                    "link_mention": {"href": "https://google.com", "title": "Google"},
                },
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "[Google](https://google.com)" in result

    def test_extract_rich_text_mention_user(self):
        parser = _make_parser()
        rich_text = [
            {
                "type": "mention",
                "plain_text": "@John Doe",
                "mention": {"type": "user", "user": {"id": "u1"}},
            }
        ]
        result = parser.extract_rich_text(rich_text)
        assert "@John Doe" in result


# ===================================================================
# NotionBlockParser - Static Helpers
# ===================================================================

class TestNotionBlockParserHelpers:
    def test_normalize_url_empty(self):
        assert NotionBlockParser._normalize_url("") is None

    def test_normalize_url_none(self):
        assert NotionBlockParser._normalize_url(None) is None

    def test_normalize_url_valid(self):
        assert NotionBlockParser._normalize_url("https://example.com") == "https://example.com"

    def test_construct_block_url(self):
        parser = _make_parser()
        result = parser._construct_block_url(
            "https://www.notion.so/page-abc123",
            "abc-123-def"
        )
        assert result == "https://www.notion.so/page-abc123#abc123def"

    def test_construct_block_url_missing_parent(self):
        parser = _make_parser()
        assert parser._construct_block_url(None, "abc-123") is None

    def test_construct_block_url_missing_block_id(self):
        parser = _make_parser()
        assert parser._construct_block_url("https://example.com", None) is None

    def test_extract_rich_text_from_block_data_paragraph(self):
        block_data = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"plain_text": "Hello"}]
            },
        }
        result = NotionBlockParser.extract_rich_text_from_block_data(block_data)
        assert result is not None
        assert result[0]["plain_text"] == "Hello"

    def test_extract_rich_text_from_block_data_none(self):
        assert NotionBlockParser.extract_rich_text_from_block_data(None) is None

    def test_extract_rich_text_from_block_data_no_type(self):
        assert NotionBlockParser.extract_rich_text_from_block_data({}) is None


# ===================================================================
# NotionBlockParser - Block Parsing
# ===================================================================

class TestNotionBlockParserBlocks:
    @pytest.mark.asyncio
    async def test_parse_paragraph_block(self):
        parser = _make_parser()
        notion_block = {
            "id": "block-1",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "plain_text": "Hello world", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-02T00:00:00.000Z",
        }
        block, group, children = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert group is None
        assert block.type == BlockType.TEXT
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert "Hello world" in block.data

    @pytest.mark.asyncio
    async def test_parse_heading_1(self):
        parser = _make_parser()
        notion_block = {
            "id": "h1-block",
            "type": "heading_1",
            "heading_1": {
                "rich_text": [{"type": "text", "plain_text": "Title", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.sub_type == BlockSubType.HEADING
        assert block.name == "H1"
        assert "Title" in block.data

    @pytest.mark.asyncio
    async def test_parse_heading_2(self):
        parser = _make_parser()
        notion_block = {
            "id": "h2-block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "plain_text": "Subtitle", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.name == "H2"

    @pytest.mark.asyncio
    async def test_parse_heading_3(self):
        parser = _make_parser()
        notion_block = {
            "id": "h3-block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "plain_text": "Section", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.name == "H3"

    @pytest.mark.asyncio
    async def test_parse_bulleted_list_item(self):
        parser = _make_parser()
        notion_block = {
            "id": "bullet-1",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "plain_text": "Item 1", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.sub_type == BlockSubType.LIST_ITEM
        assert "- Item 1" in block.data
        assert block.list_metadata.list_style == "bullet"

    @pytest.mark.asyncio
    async def test_parse_numbered_list_item(self):
        parser = _make_parser()
        notion_block = {
            "id": "num-1",
            "type": "numbered_list_item",
            "numbered_list_item": {
                "rich_text": [{"type": "text", "plain_text": "Step 1", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert "1. Step 1" in block.data
        assert block.list_metadata.list_style == "numbered"

    @pytest.mark.asyncio
    async def test_parse_to_do_unchecked(self):
        parser = _make_parser()
        notion_block = {
            "id": "todo-1",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "plain_text": "Buy milk", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
                "checked": False,
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert "- [ ] Buy milk" in block.data

    @pytest.mark.asyncio
    async def test_parse_to_do_checked(self):
        parser = _make_parser()
        notion_block = {
            "id": "todo-2",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "plain_text": "Done task", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
                "checked": True,
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert "- [x] Done task" in block.data

    @pytest.mark.asyncio
    async def test_parse_code_block(self):
        parser = _make_parser()
        notion_block = {
            "id": "code-1",
            "type": "code",
            "code": {
                "rich_text": [{"plain_text": "print('hello')"}],
                "language": "python",
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.sub_type == BlockSubType.CODE
        assert block.data == "print('hello')"
        assert block.code_metadata.language == "python"

    @pytest.mark.asyncio
    async def test_parse_quote_block(self):
        parser = _make_parser()
        notion_block = {
            "id": "quote-1",
            "type": "quote",
            "quote": {
                "rich_text": [{"type": "text", "plain_text": "Wise words", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.sub_type == BlockSubType.QUOTE
        assert "> Wise words" in block.data

    @pytest.mark.asyncio
    async def test_parse_divider_block(self):
        parser = _make_parser()
        notion_block = {
            "id": "divider-1",
            "type": "divider",
            "divider": {},
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.sub_type == BlockSubType.DIVIDER
        assert block.data == "---"

    @pytest.mark.asyncio
    async def test_parse_callout_with_emoji(self):
        parser = _make_parser()
        notion_block = {
            "id": "callout-1",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "plain_text": "Important note", "annotations": {"bold": False, "italic": False, "code": False, "strikethrough": False, "underline": False, "color": "default"}}],
                "icon": {"type": "emoji", "emoji": "💡"},
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert "Important note" in block.data

    @pytest.mark.asyncio
    async def test_parse_archived_block_skipped(self):
        parser = _make_parser()
        notion_block = {
            "id": "archived-1",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
            "archived": True,
        }
        block, group, children = await parser.parse_block(notion_block, block_index=0)
        assert block is None
        assert group is None

    @pytest.mark.asyncio
    async def test_parse_unsupported_block_skipped(self):
        parser = _make_parser()
        notion_block = {
            "id": "unsupported-1",
            "type": "unsupported",
            "unsupported": {},
        }
        block, group, children = await parser.parse_block(notion_block, block_index=0)
        assert block is None
        assert group is None

    @pytest.mark.asyncio
    async def test_parse_empty_paragraph_skipped(self):
        parser = _make_parser()
        notion_block = {
            "id": "empty-para",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        # Empty paragraph data should be skipped
        assert block is None

    @pytest.mark.asyncio
    async def test_parse_paragraph_with_link_mention(self):
        """Paragraph with a single link_mention should become a LINK block."""
        parser = _make_parser()
        notion_block = {
            "id": "link-para",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "mention",
                        "plain_text": "Example Link",
                        "mention": {
                            "type": "link_mention",
                            "link_mention": {
                                "href": "https://example.com",
                                "title": "Example Link",
                            },
                        },
                    }
                ],
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-01T00:00:00.000Z",
        }
        block, group, _ = await parser.parse_block(notion_block, block_index=0)
        assert block is not None
        assert block.sub_type == BlockSubType.LINK
        assert block.link_metadata is not None
        assert block.link_metadata.link_url == "https://example.com"


# ===================================================================
# NotionConnector tests
# ===================================================================

class TestNotionConnector:
    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        mock_client = AsyncMock()
        with patch(
            "app.connectors.sources.notion.connector.NotionClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch(
                "app.connectors.sources.notion.connector.NotionDataSource"
            ) as MockDS:
                mock_ds = MagicMock()
                MockDS.return_value = mock_ds
                result = await connector.init()
                assert result is True
                assert connector.notion_client == mock_client

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector = _make_connector()
        with patch(
            "app.connectors.sources.notion.connector.NotionClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(
                side_effect=Exception("Auth failed")
            )
            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_no_client(self):
        connector = _make_connector()
        connector.notion_client = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates_to_full(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_signed_url_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        from app.models.entities import FileRecord, RecordType
        from app.config.constants.arangodb import OriginTypes
        record = FileRecord(
            external_record_id="block-1",
            record_name="file.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
        )
        result = await connector.get_signed_url(record)
        assert result is None
