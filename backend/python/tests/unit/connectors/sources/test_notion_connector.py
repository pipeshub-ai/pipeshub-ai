"""Tests for the Notion connector and block parser."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.connectors.sources.notion.connector import NotionConnector
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockSubType,
    BlockType,
    DataFormat,
)
from app.models.entities import FileRecord, RecordType, WebpageRecord
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
import aiohttp
from fastapi import HTTPException
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
    TableRowMetadata,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a NotionConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_store_provider = MagicMock()
    # Set up transaction context manager
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx
    config_service = AsyncMock()
    connector_id = "notion-conn-1"
    connector = NotionConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
        scope="team",
        created_by="test-user",
    )
    return connector


def _make_parser():
    """Build a NotionBlockParser with mocked logger."""
    logger = MagicMock()
    return NotionBlockParser(logger=logger)


def _make_api_response(success=True, data=None, error=None):
    """Create a mock API response object."""
    resp = MagicMock()
    resp.success = success
    resp.error = error
    if data is not None:
        resp.data = MagicMock()
        resp.data.json.return_value = data
    else:
        resp.data = None
    return resp


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
        assert str(block.link_metadata.link_url).rstrip("/") == "https://example.com"


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
    async def test_test_connection_success(self):
        connector = _make_connector()
        connector.notion_client = MagicMock()
        mock_ds = MagicMock()
        mock_ds.retrieve_bot_user = AsyncMock(return_value=_make_api_response(success=True, data={"bot": {}}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure_response(self):
        connector = _make_connector()
        connector.notion_client = MagicMock()
        mock_ds = MagicMock()
        mock_ds.retrieve_bot_user = AsyncMock(return_value=_make_api_response(success=False, error="Unauthorized"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        connector = _make_connector()
        connector.notion_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("Network error"))
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

    @pytest.mark.asyncio
    async def test_get_signed_url_routes_to_comment_attachment(self):
        """get_signed_url routes ca_ prefix to _get_comment_attachment_url."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_comment_attachment_url = AsyncMock(return_value="https://signed.example.com/file.pdf")
        record = FileRecord(
            external_record_id="ca_comment123_file_pdf",
            record_name="file.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
        )
        result = await connector.get_signed_url(record)
        assert result == "https://signed.example.com/file.pdf"
        connector._get_comment_attachment_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_signed_url_routes_to_block_file(self):
        """get_signed_url routes non-prefixed IDs to _get_block_file_url."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_block_file_url = AsyncMock(return_value="https://signed.example.com/block.pdf")
        record = FileRecord(
            external_record_id="block-123",
            record_name="block.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
        )
        result = await connector.get_signed_url(record)
        assert result == "https://signed.example.com/block.pdf"
        connector._get_block_file_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_block_file_url_success(self):
        """_get_block_file_url fetches block and extracts file URL."""
        connector = _make_connector()
        block_data = {
            "type": "file",
            "file": {"file": {"url": "https://notion.so/signed/file.pdf"}},
        }
        mock_ds = MagicMock()
        mock_ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=True, data=block_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = FileRecord(
            external_record_id="block-file-1",
            record_name="file.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
        )
        result = await connector._get_block_file_url(record)
        assert result == "https://notion.so/signed/file.pdf"

    @pytest.mark.asyncio
    async def test_get_block_file_url_failure(self):
        """_get_block_file_url returns signed_url from record on API failure."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=False, error="Not found"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = FileRecord(
            external_record_id="block-file-1",
            record_name="file.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
            signed_url="https://fallback.example.com/file.pdf",
        )
        result = await connector._get_block_file_url(record)
        assert result == "https://fallback.example.com/file.pdf"

    @pytest.mark.asyncio
    async def test_get_block_file_url_empty_block_id_raises(self):
        connector = _make_connector()
        record = FileRecord(
            external_record_id="",
            record_name="file.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
        )
        with pytest.raises(ValueError, match="Invalid block file"):
            await connector._get_block_file_url(record)

    @pytest.mark.asyncio
    async def test_get_comment_attachment_url_invalid_prefix_raises(self):
        connector = _make_connector()
        record = FileRecord(
            external_record_id="not_ca_prefix",
            record_name="file.pdf",
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id="notion-conn-1",
            record_type=RecordType.FILE,
            version=1,
            is_file=True,
        )
        with pytest.raises(ValueError, match="Invalid comment attachment"):
            await connector._get_comment_attachment_url(record)


class TestNotionConnectorCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector()
        connector.notion_client = MagicMock()
        connector.data_source = MagicMock()
        await connector.cleanup()
        assert connector.notion_client is None
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        connector = _make_connector()
        await connector.handle_webhook_notification({"type": "test"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_get_filter_options_raises(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("some_key")

    @pytest.mark.asyncio
    async def test_reindex_records_empty(self):
        connector = _make_connector()
        await connector.reindex_records([])
        # Should not raise, no records to process

    @pytest.mark.asyncio
    async def test_reindex_records_with_records(self):
        connector = _make_connector()
        record = MagicMock()
        await connector.reindex_records([record])
        # Should not raise (TODO implementation)


class TestNotionSyncUsers:
    @pytest.mark.asyncio
    async def test_sync_users_person_and_bot(self):
        """Tests full user sync with person users, bot workspace extraction, and email retrieval."""
        connector = _make_connector()

        # First call: list_users returns person + bot
        list_response = _make_api_response(success=True, data={
            "results": [
                {"id": "person-1", "type": "person", "name": "Alice"},
                {
                    "id": "bot-1", "type": "bot", "name": "Integration Bot",
                    "bot": {"workspace_id": "ws-1", "workspace_name": "My Workspace"},
                },
            ],
            "has_more": False,
            "next_cursor": None,
        })

        # retrieve_user returns person user details with email
        retrieve_response = _make_api_response(success=True, data={
            "id": "person-1",
            "type": "person",
            "name": "Alice",
            "person": {"email": "alice@example.com"},
        })

        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=list_response)
        mock_ds.retrieve_user = AsyncMock(return_value=retrieve_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._create_workspace_record_group = AsyncMock()
        connector._add_users_to_workspace_permissions = AsyncMock()
        connector._transform_to_app_user = MagicMock(return_value=MagicMock(email="alice@example.com"))

        await connector._sync_users()

        # Verify workspace was extracted from bot user
        assert connector.workspace_id == "ws-1"
        assert connector.workspace_name == "My Workspace"
        connector._create_workspace_record_group.assert_awaited_once()
        connector.data_entities_processor.on_new_app_users.assert_awaited()
        connector._add_users_to_workspace_permissions.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_api_failure_raises(self):
        """Tests that user sync raises on API failure."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_api_response(success=False, error="API down"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Notion API error"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_skips_failed_retrieve(self):
        """Tests user sync continues when individual user retrieval fails."""
        connector = _make_connector()

        list_response = _make_api_response(success=True, data={
            "results": [
                {"id": "p1", "type": "person"},
                {"id": "p2", "type": "person"},
            ],
            "has_more": False,
        })

        # First user retrieval fails, second succeeds
        fail_resp = _make_api_response(success=False, error="Not found")
        success_resp = _make_api_response(success=True, data={
            "id": "p2", "type": "person", "person": {"email": "bob@example.com"},
        })

        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=list_response)
        mock_ds.retrieve_user = AsyncMock(side_effect=[fail_resp, success_resp])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_app_user = MagicMock(return_value=MagicMock(email="bob@example.com"))

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_awaited()


class TestNotionSyncObjectsByType:
    @pytest.mark.asyncio
    async def test_sync_pages_full_sync(self):
        """Test full page sync with records, attachments, comments."""
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "page-1",
            "last_edited_time": "2024-06-01T12:00:00.000Z",
            "archived": False,
            "url": "https://notion.so/page-1",
        }

        search_response = _make_api_response(success=True, data={
            "results": [page_data],
            "has_more": False,
        })

        mock_ds = MagicMock()
        mock_ds.search = AsyncMock(return_value=search_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Mock transform
        mock_record = MagicMock()
        mock_record.record_name = "Test Page"
        mock_record.indexing_status = ProgressStatus.NOT_STARTED
        connector._transform_to_webpage_record = AsyncMock(return_value=mock_record)

        # Mock attachments and comments
        connector._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))

        await connector._sync_objects_by_type("page")

        connector.data_entities_processor.on_new_records.assert_awaited()
        connector.pages_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_data_sources_with_delta(self):
        """Test incremental data_source sync that stops at sync point."""
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2024-05-01T00:00:00.000Z"})
        connector.pages_sync_point.update_sync_point = AsyncMock()

        # Record with older timestamp than sync point - should stop
        old_data = {
            "id": "ds-old",
            "last_edited_time": "2024-04-01T00:00:00.000Z",
            "archived": False,
        }

        search_response = _make_api_response(success=True, data={
            "results": [old_data],
            "has_more": False,
        })

        mock_ds = MagicMock()
        mock_ds.search = AsyncMock(return_value=search_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_objects_by_type("data_source")

        # No records should be synced since all are older than sync point
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_objects_api_failure_raises(self):
        """Test that search API failure raises an exception."""
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        mock_ds = MagicMock()
        mock_ds.search = AsyncMock(return_value=_make_api_response(success=False, error="Server error"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Notion API error"):
            await connector._sync_objects_by_type("page")


class TestNotionRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_calls_all_steps(self):
        """run_sync loads filters then syncs users, data_sources, and pages."""
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection

        with patch("app.connectors.sources.notion.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (FilterCollection(), FilterCollection())
            connector._sync_users = AsyncMock()
            connector._sync_objects_by_type = AsyncMock()

            await connector.run_sync()

            connector._sync_users.assert_awaited_once()
            assert connector._sync_objects_by_type.await_count == 2
            calls = [c.args[0] for c in connector._sync_objects_by_type.await_args_list]
            assert "data_source" in calls
            assert "page" in calls


class TestNotionFetchComments:
    @pytest.mark.asyncio
    async def test_fetch_comments_for_block_empty_block_id(self):
        """Returns empty list for empty block IDs."""
        connector = _make_connector()
        result = await connector._fetch_comments_for_block("")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_comments_for_block_whitespace_only(self):
        connector = _make_connector()
        result = await connector._fetch_comments_for_block("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_comments_for_block_success(self):
        """Fetches comments with pagination."""
        connector = _make_connector()
        mock_ds = MagicMock()
        response = _make_api_response(success=True, data={
            "results": [{"id": "comment-1", "rich_text": []}],
            "has_more": False,
        })
        mock_ds.retrieve_comments = AsyncMock(return_value=response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_comments_for_block("block-1")
        assert len(result) == 1
        assert result[0]["id"] == "comment-1"

    @pytest.mark.asyncio
    async def test_fetch_comments_for_block_api_failure(self):
        """Returns empty list on API failure."""
        connector = _make_connector()
        mock_ds = MagicMock()
        # Response has data but is not successful
        response = _make_api_response(success=False, error="Server error")
        response.data = MagicMock()
        response.data.json.return_value = {"object": "error", "message": "Server error"}
        mock_ds.retrieve_comments = AsyncMock(return_value=response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_comments_for_blocks_combines(self):
        """Fetches comments for page + blocks in parallel."""
        connector = _make_connector()

        # Mock to return different comments for page and block
        async def mock_fetch(block_id):
            if block_id == "page-1":
                return [{"id": "page-comment"}]
            elif block_id == "block-A":
                return [{"id": "block-comment"}]
            return []

        connector._fetch_comments_for_block = AsyncMock(side_effect=mock_fetch)

        result = await connector._fetch_comments_for_blocks("page-1", ["block-A"])
        assert len(result) == 2
        # Result is list of (comment, block_id) tuples
        comment_ids = [c[0]["id"] for c in result]
        assert "page-comment" in comment_ids
        assert "block-comment" in comment_ids


class TestNotionFetchAttachmentBlocks:
    @pytest.mark.asyncio
    async def test_fetch_attachment_blocks_and_block_ids(self):
        """Collects file blocks and block IDs, recurses into children, skips child_page/child_database."""
        connector = _make_connector()

        # Page has 3 blocks: a file block, a child_page (skipped), and a paragraph with children
        page_blocks = {
            "results": [
                {"id": "file-block-1", "type": "file", "file": {}, "has_children": False},
                {"id": "child-page-1", "type": "child_page", "child_page": {}, "has_children": True},
                {"id": "para-1", "type": "paragraph", "paragraph": {}, "has_children": True},
            ],
            "has_more": False,
        }

        # Children of para-1: a video block
        para_children = {
            "results": [
                {"id": "video-1", "type": "video", "video": {}, "has_children": False},
            ],
            "has_more": False,
        }

        call_count = 0

        async def mock_retrieve_children(block_id, start_cursor=None, page_size=50):
            nonlocal call_count
            call_count += 1
            if block_id == "page-1":
                return _make_api_response(success=True, data=page_blocks)
            elif block_id == "para-1":
                return _make_api_response(success=True, data=para_children)
            return _make_api_response(success=True, data={"results": [], "has_more": False})

        mock_ds = MagicMock()
        mock_ds.retrieve_block_children = AsyncMock(side_effect=mock_retrieve_children)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        attachments, block_ids = await connector._fetch_attachment_blocks_and_block_ids_recursive("page-1")

        # Should have file-block-1 and video-1 as attachments
        assert len(attachments) == 2
        attachment_types = {a["type"] for a in attachments}
        assert "file" in attachment_types
        assert "video" in attachment_types

        # Block IDs should include file-block-1, para-1, video-1 (but NOT child-page-1)
        assert "file-block-1" in block_ids
        assert "para-1" in block_ids
        assert "video-1" in block_ids
        assert "child-page-1" not in block_ids


class TestNotionFetchDataSource:
    @pytest.mark.asyncio
    async def test_fetch_data_source_as_blocks_metadata_failure(self):
        """Returns empty BlocksContainer when metadata fetch fails."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.retrieve_data_source_by_id = AsyncMock(
            return_value=_make_api_response(success=False, error="Not found")
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        parser = _make_parser()
        result = await connector._fetch_data_source_as_blocks("ds-1", parser)
        assert result.blocks == []
        assert result.block_groups == []


class TestNotionAddWorkspacePermissions:
    @pytest.mark.asyncio
    async def test_add_workspace_permissions_no_workspace(self):
        """Does nothing if workspace_id is not set."""
        connector = _make_connector()
        connector.workspace_id = None
        await connector._add_users_to_workspace_permissions(["user@example.com"])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_workspace_permissions_empty_emails(self):
        """Does nothing if no emails provided."""
        connector = _make_connector()
        connector.workspace_id = "ws-1"
        await connector._add_users_to_workspace_permissions([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_workspace_permissions_creates_group_and_permissions(self):
        """Creates record group with permissions for all provided emails."""
        connector = _make_connector()
        connector.workspace_id = "ws-1"
        connector.workspace_name = "My Workspace"

        await connector._add_users_to_workspace_permissions(["a@example.com", "b@example.com"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


class TestNotionExtractBlockText:
    @pytest.mark.asyncio
    async def test_extract_block_text_content_success(self):
        """Extracts text content from a block via API."""
        connector = _make_connector()
        parser = _make_parser()

        block_data = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "plain_text": "Hello world", "annotations": {}}]},
        }
        mock_ds = MagicMock()
        mock_ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=True, data=block_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._extract_block_text_content("block-1", parser)
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_extract_block_text_content_no_text(self):
        """Returns None when block has no text content."""
        connector = _make_connector()
        parser = _make_parser()

        block_data = {"type": "divider", "divider": {}}
        mock_ds = MagicMock()
        mock_ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=True, data=block_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._extract_block_text_content("block-1", parser)
        assert result is None

    @pytest.mark.asyncio
    async def test_extract_block_text_content_api_failure(self):
        """Returns None on API failure."""
        connector = _make_connector()
        parser = _make_parser()

        mock_ds = MagicMock()
        mock_ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=False, error="Not found"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._extract_block_text_content("block-1", parser)
        assert result is None


class TestNotionFetchBlockChildrenRecursive:
    @pytest.mark.asyncio
    async def test_fetch_block_children_single_page(self):
        """Fetches a single page of children blocks."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.retrieve_block_children = AsyncMock(return_value=_make_api_response(
            success=True,
            data={"results": [{"id": "b1"}, {"id": "b2"}], "has_more": False}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_block_children_recursive("page-1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_block_children_api_failure(self):
        """Returns empty list on API failure."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.retrieve_block_children = AsyncMock(return_value=_make_api_response(success=False, error="Fail"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_block_children_recursive("page-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_block_children_non_dict_response(self):
        """Handles non-dict response data gracefully."""
        connector = _make_connector()
        mock_ds = MagicMock()
        resp = _make_api_response(success=True, data="not a dict")
        mock_ds.retrieve_block_children = AsyncMock(return_value=resp)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_block_children_recursive("page-1")
        assert result == []


class TestNotionFetchPageAttachmentsAndComments:
    @pytest.mark.asyncio
    async def test_success_with_attachments_and_comments(self):
        """Fetches attachments and groups comments by block."""
        connector = _make_connector()

        # Mock the recursive fetcher
        connector._fetch_attachment_blocks_and_block_ids_recursive = AsyncMock(
            return_value=(
                [{"id": "file-1", "type": "file", "file": {"file": {"url": "https://example.com/file.pdf"}}}],
                ["block-A"],
            )
        )

        # Mock file record transform
        mock_file_record = MagicMock(spec=FileRecord)
        connector._transform_to_file_record = MagicMock(return_value=mock_file_record)

        # Mock comments
        connector._fetch_comments_for_blocks = AsyncMock(
            return_value=[
                ({"id": "comment-1"}, "page-1"),
                ({"id": "comment-2"}, "block-A"),
            ]
        )

        file_records, comments_by_block = await connector._fetch_page_attachments_and_comments("page-1", "https://notion.so/page-1")

        assert len(file_records) == 1
        assert "page-1" in comments_by_block
        assert "block-A" in comments_by_block
        assert len(comments_by_block["page-1"]) == 1
        assert len(comments_by_block["block-A"]) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """Returns empty results on exception."""
        connector = _make_connector()
        connector._fetch_attachment_blocks_and_block_ids_recursive = AsyncMock(
            side_effect=Exception("API error")
        )

        file_records, comments_by_block = await connector._fetch_page_attachments_and_comments("page-1")
        assert file_records == []
        assert comments_by_block == {}


# ===========================================================================
# DEEP SYNC LOOP TESTS — run_sync, _sync_objects_by_type, _sync_users,
# _fetch_attachment_blocks_and_block_ids_recursive, _fetch_comments_for_block,
# _fetch_comments_for_blocks, _fetch_block_children_recursive
# ===========================================================================


class TestNotionRunSync:
    """Tests for run_sync orchestration."""

    @pytest.mark.asyncio
    async def test_run_sync_calls_sync_users_and_objects(self):
        connector = _make_connector()
        connector._sync_users = AsyncMock()
        connector._sync_objects_by_type = AsyncMock()
        with patch(
            "app.connectors.sources.notion.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await connector.run_sync()
        connector._sync_users.assert_awaited_once()
        assert connector._sync_objects_by_type.await_count == 2
        calls = [c.args[0] for c in connector._sync_objects_by_type.await_args_list]
        assert "data_source" in calls
        assert "page" in calls

    @pytest.mark.asyncio
    async def test_run_sync_raises_on_error(self):
        connector = _make_connector()
        connector._sync_users = AsyncMock(side_effect=Exception("sync fail"))
        with patch(
            "app.connectors.sources.notion.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            with pytest.raises(Exception, match="sync fail"):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_incremental_delegates_to_run_sync(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


class TestNotionSyncObjectsByType:
    """Tests for _sync_objects_by_type (pages & data_sources)."""

    @pytest.mark.asyncio
    async def test_sync_pages_single_page(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "p1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "url": "https://notion.so/p1",
            "parent": {"type": "workspace", "workspace": True},
        }
        search_resp = _make_api_response(
            data={"results": [page_data], "has_more": False, "next_cursor": None}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        mock_record = MagicMock()
        connector._transform_to_webpage_record = AsyncMock(return_value=mock_record)
        connector._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))

        await connector._sync_objects_by_type("page")
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_pages_empty_results(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        search_resp = _make_api_response(data={"results": [], "has_more": False})
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )

        await connector._sync_objects_by_type("page")
        connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_pages_skips_archived(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "p1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "archived": True,
        }
        search_resp = _make_api_response(
            data={"results": [page_data], "has_more": False}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        connector._transform_to_webpage_record = AsyncMock()

        await connector._sync_objects_by_type("page")
        connector._transform_to_webpage_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_pages_delta_sync_stops_at_threshold(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-06-01T00:00:00Z"}
        )
        connector.pages_sync_point.update_sync_point = AsyncMock()

        old_page = {
            "id": "p-old",
            "last_edited_time": "2024-05-30T00:00:00Z",
        }
        search_resp = _make_api_response(
            data={"results": [old_page], "has_more": True, "next_cursor": "c2"}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        connector._transform_to_webpage_record = AsyncMock()

        await connector._sync_objects_by_type("page")
        connector._transform_to_webpage_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_pages_pagination(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page1_data = {
            "id": "p1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "url": "https://notion.so/p1",
            "parent": {"type": "workspace"},
        }
        page2_data = {
            "id": "p2",
            "last_edited_time": "2024-06-01T09:00:00Z",
            "url": "https://notion.so/p2",
            "parent": {"type": "workspace"},
        }
        resp1 = _make_api_response(
            data={"results": [page1_data], "has_more": True, "next_cursor": "c2"}
        )
        resp2 = _make_api_response(
            data={"results": [page2_data], "has_more": False, "next_cursor": None}
        )

        ds_mock = MagicMock()
        ds_mock.search = AsyncMock(side_effect=[resp1, resp2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds_mock)
        connector._transform_to_webpage_record = AsyncMock(return_value=MagicMock())
        connector._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))

        await connector._sync_objects_by_type("page")
        assert connector.data_entities_processor.on_new_records.await_count == 2

    @pytest.mark.asyncio
    async def test_sync_data_sources(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        ds_data = {
            "id": "ds1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "parent": {"type": "workspace"},
        }
        search_resp = _make_api_response(
            data={"results": [ds_data], "has_more": False}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        connector._transform_to_webpage_record = AsyncMock(return_value=MagicMock())

        await connector._sync_objects_by_type("data_source")
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_data_source_with_database_parent(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        ds_data = {
            "id": "ds1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "parent": {"type": "database_id", "database_id": "db1"},
        }
        search_resp = _make_api_response(
            data={"results": [ds_data], "has_more": False}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        connector._get_database_parent_page_id = AsyncMock(return_value="parent-page")
        connector._transform_to_webpage_record = AsyncMock(return_value=MagicMock())

        await connector._sync_objects_by_type("data_source")
        connector._transform_to_webpage_record.assert_awaited_once()
        call_kwargs = connector._transform_to_webpage_record.await_args
        assert call_kwargs.kwargs.get("database_parent_id") == "parent-page"

    @pytest.mark.asyncio
    async def test_sync_pages_indexing_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.side_effect = lambda key, **kw: False
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "p1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "url": "https://notion.so/p1",
            "parent": {"type": "workspace"},
        }
        search_resp = _make_api_response(
            data={"results": [page_data], "has_more": False}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        mock_record = MagicMock()
        connector._transform_to_webpage_record = AsyncMock(return_value=mock_record)
        connector._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))

        await connector._sync_objects_by_type("page")
        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_sync_pages_api_error_raises(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        error_resp = _make_api_response(success=False, error="Rate limited")
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=error_resp))
        )

        with pytest.raises(Exception, match="Notion API error"):
            await connector._sync_objects_by_type("page")

    @pytest.mark.asyncio
    async def test_sync_pages_first_sync_initializes_sync_point(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        search_resp = _make_api_response(data={"results": [], "has_more": False})
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )

        await connector._sync_objects_by_type("page")
        connector.pages_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_pages_with_file_attachments(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "p1",
            "last_edited_time": "2024-06-01T10:00:00Z",
            "url": "https://notion.so/p1",
            "parent": {"type": "workspace"},
        }
        search_resp = _make_api_response(
            data={"results": [page_data], "has_more": False}
        )
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(search=AsyncMock(return_value=search_resp))
        )
        mock_record = MagicMock()
        connector._transform_to_webpage_record = AsyncMock(return_value=mock_record)

        file_record = MagicMock()
        connector._fetch_page_attachments_and_comments = AsyncMock(
            return_value=([file_record], {"p1": [({}, "p1")]})
        )
        connector._extract_comment_attachment_file_records = AsyncMock(return_value=[])

        await connector._sync_objects_by_type("page")
        call_args = connector.data_entities_processor.on_new_records.await_args[0][0]
        assert len(call_args) == 2  # page + file


class TestNotionSyncUsers:
    """Tests for _sync_users deep loop."""

    @pytest.mark.asyncio
    async def test_single_page_with_person(self):
        connector = _make_connector()
        connector.workspace_id = None
        connector.workspace_name = None

        users_list_resp = _make_api_response(data={
            "results": [
                {"id": "u1", "type": "person", "name": "Alice"},
            ],
            "has_more": False,
            "next_cursor": None,
        })
        user_detail_resp = _make_api_response(data={
            "id": "u1",
            "type": "person",
            "name": "Alice",
            "person": {"email": "alice@test.com"},
        })
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=users_list_resp)
        mock_ds.retrieve_user = AsyncMock(return_value=user_detail_resp)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_bot_users(self):
        connector = _make_connector()
        connector.workspace_id = None

        users_list_resp = _make_api_response(data={
            "results": [
                {"id": "b1", "type": "bot", "bot": {"workspace_id": "ws1", "workspace_name": "My WS"}},
            ],
            "has_more": False,
        })
        empty_resp = _make_api_response(data={
            "results": [],
            "has_more": False,
        })
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(side_effect=[users_list_resp, empty_resp])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._create_workspace_record_group = AsyncMock()

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_not_called()
        assert connector.workspace_id == "ws1"

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()
        connector.workspace_id = None

        resp1 = _make_api_response(data={
            "results": [{"id": "u1", "type": "person"}],
            "has_more": True,
            "next_cursor": "cursor-2",
        })
        user_detail = _make_api_response(data={
            "id": "u1",
            "type": "person",
            "name": "Test User",
            "person": {"email": "a@b.com"},
        })
        resp2 = _make_api_response(data={
            "results": [],
            "has_more": False,
        })
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(side_effect=[resp1, resp2])
        mock_ds.retrieve_user = AsyncMock(return_value=user_detail)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_error_raises(self):
        connector = _make_connector()
        connector.workspace_id = None

        error_resp = _make_api_response(success=False, error="Unauthorized")
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=error_resp)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Notion API error"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_user_detail_exception_skipped(self):
        connector = _make_connector()
        connector.workspace_id = None

        users_list_resp = _make_api_response(data={
            "results": [{"id": "u1", "type": "person"}],
            "has_more": False,
        })
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=users_list_resp)
        mock_ds.retrieve_user = AsyncMock(side_effect=Exception("timeout"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_not_called()


class TestNotionFetchBlockChildrenRecursive:
    """Tests for _fetch_block_children_recursive."""

    @pytest.mark.asyncio
    async def test_single_page_of_children(self):
        connector = _make_connector()
        children_resp = _make_api_response(data={
            "results": [{"id": "b1", "type": "paragraph"}, {"id": "b2", "type": "heading_1"}],
            "has_more": False,
        })
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_block_children=AsyncMock(return_value=children_resp))
        )

        blocks = await connector._fetch_block_children_recursive("page-1")
        assert len(blocks) == 2

    @pytest.mark.asyncio
    async def test_paginated_children(self):
        connector = _make_connector()
        resp1 = _make_api_response(data={
            "results": [{"id": "b1"}],
            "has_more": True,
            "next_cursor": "c2",
        })
        resp2 = _make_api_response(data={
            "results": [{"id": "b2"}],
            "has_more": False,
        })
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=[resp1, resp2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        blocks = await connector._fetch_block_children_recursive("page-1")
        assert len(blocks) == 2

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        fail_resp = _make_api_response(success=False, error="Not found")
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_block_children=AsyncMock(return_value=fail_resp))
        )

        blocks = await connector._fetch_block_children_recursive("bad-id")
        assert blocks == []

    @pytest.mark.asyncio
    async def test_non_dict_data_returns_empty(self):
        connector = _make_connector()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock()
        resp.data.json.return_value = "not a dict"
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_block_children=AsyncMock(return_value=resp))
        )

        blocks = await connector._fetch_block_children_recursive("page-1")
        assert blocks == []

    @pytest.mark.asyncio
    async def test_exception_breaks_loop(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(
                retrieve_block_children=AsyncMock(side_effect=Exception("network"))
            )
        )

        blocks = await connector._fetch_block_children_recursive("page-1")
        assert blocks == []


class TestNotionFetchAttachmentBlocksRecursive:
    """Tests for _fetch_attachment_blocks_and_block_ids_recursive."""

    @pytest.mark.asyncio
    async def test_collects_file_attachments(self):
        connector = _make_connector()
        children_resp = _make_api_response(data={
            "results": [
                {"id": "b1", "type": "file", "has_children": False},
                {"id": "b2", "type": "paragraph", "has_children": False},
            ],
            "has_more": False,
        })
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_block_children=AsyncMock(return_value=children_resp))
        )

        attachments, block_ids = await connector._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 1
        assert attachments[0]["type"] == "file"
        assert "b1" in block_ids
        assert "b2" in block_ids

    @pytest.mark.asyncio
    async def test_skips_child_page_and_database(self):
        connector = _make_connector()
        children_resp = _make_api_response(data={
            "results": [
                {"id": "cp1", "type": "child_page", "has_children": True},
                {"id": "cd1", "type": "child_database", "has_children": True},
            ],
            "has_more": False,
        })
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_block_children=AsyncMock(return_value=children_resp))
        )

        attachments, block_ids = await connector._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 0
        assert len(block_ids) == 0

    @pytest.mark.asyncio
    async def test_recurses_into_children(self):
        connector = _make_connector()
        parent_resp = _make_api_response(data={
            "results": [
                {"id": "b1", "type": "toggle", "has_children": True},
            ],
            "has_more": False,
        })
        child_resp = _make_api_response(data={
            "results": [
                {"id": "b2", "type": "video", "has_children": False},
            ],
            "has_more": False,
        })
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=[parent_resp, child_resp])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        attachments, block_ids = await connector._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 1
        assert attachments[0]["type"] == "video"
        assert "b1" in block_ids
        assert "b2" in block_ids

    @pytest.mark.asyncio
    async def test_image_block_skipped_as_file_but_recurses(self):
        connector = _make_connector()
        parent_resp = _make_api_response(data={
            "results": [
                {"id": "img1", "type": "image", "has_children": True},
            ],
            "has_more": False,
        })
        child_resp = _make_api_response(data={
            "results": [
                {"id": "b2", "type": "pdf", "has_children": False},
            ],
            "has_more": False,
        })
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=[parent_resp, child_resp])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        attachments, block_ids = await connector._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 1
        assert attachments[0]["type"] == "pdf"
        assert "img1" in block_ids


class TestNotionFetchCommentsForBlock:
    """Tests for _fetch_comments_for_block."""

    @pytest.mark.asyncio
    async def test_empty_block_id_returns_empty(self):
        connector = _make_connector()
        result = await connector._fetch_comments_for_block("")
        assert result == []

    @pytest.mark.asyncio
    async def test_single_page_of_comments(self):
        connector = _make_connector()
        resp = _make_api_response(data={
            "results": [{"id": "c1"}, {"id": "c2"}],
            "has_more": False,
        })
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_comments=AsyncMock(return_value=resp))
        )

        comments = await connector._fetch_comments_for_block("block-1")
        assert len(comments) == 2

    @pytest.mark.asyncio
    async def test_paginated_comments(self):
        connector = _make_connector()
        resp1 = _make_api_response(data={
            "results": [{"id": "c1"}],
            "has_more": True,
            "next_cursor": "c2",
        })
        resp2 = _make_api_response(data={
            "results": [{"id": "c2"}],
            "has_more": False,
        })
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(side_effect=[resp1, resp2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        comments = await connector._fetch_comments_for_block("block-1")
        assert len(comments) == 2

    @pytest.mark.asyncio
    async def test_error_response_returns_empty(self):
        connector = _make_connector()
        resp = _make_api_response(data={
            "object": "error",
            "code": "object_not_found",
            "message": "Not found",
        })
        resp.success = False
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(retrieve_comments=AsyncMock(return_value=resp))
        )

        comments = await connector._fetch_comments_for_block("block-1")
        assert comments == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(
            return_value=MagicMock(
                retrieve_comments=AsyncMock(side_effect=Exception("network"))
            )
        )

        comments = await connector._fetch_comments_for_block("block-1")
        assert comments == []

    @pytest.mark.asyncio
    async def test_whitespace_block_id_returns_empty(self):
        connector = _make_connector()
        result = await connector._fetch_comments_for_block("   ")
        assert result == []


class TestNotionFetchCommentsForBlocks:
    """Tests for _fetch_comments_for_blocks."""

    @pytest.mark.asyncio
    async def test_collects_page_and_block_comments(self):
        connector = _make_connector()
        connector._fetch_comments_for_block = AsyncMock(
            side_effect=[
                [{"id": "c-page"}],      # page comments
                [{"id": "c-block"}],      # block-1 comments
            ]
        )

        result = await connector._fetch_comments_for_blocks("page-1", ["block-1"])
        assert len(result) == 2
        page_comments = [c for c, bid in result if bid == "page-1"]
        block_comments = [c for c, bid in result if bid == "block-1"]
        assert len(page_comments) == 1
        assert len(block_comments) == 1

    @pytest.mark.asyncio
    async def test_block_exception_continues(self):
        connector = _make_connector()
        connector._fetch_comments_for_block = AsyncMock(
            side_effect=[
                [{"id": "c-page"}],
                Exception("timeout"),
            ]
        )

        result = await connector._fetch_comments_for_blocks("page-1", ["block-1"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_blocks_list(self):
        connector = _make_connector()
        connector._fetch_comments_for_block = AsyncMock(return_value=[])

        result = await connector._fetch_comments_for_blocks("page-1", [])
        assert result == []


class TestNotionTransformToWebpageRecord:
    """Tests for _transform_to_webpage_record."""

    @pytest.mark.asyncio
    async def test_page_type(self):
        connector = _make_connector()
        connector.workspace_id = "ws1"
        connector._extract_page_title = MagicMock(return_value="My Page")
        connector._parse_iso_timestamp = MagicMock(return_value=1717228800000)

        obj_data = {
            "id": "p1",
            "url": "https://notion.so/p1",
            "created_time": "2024-06-01T10:00:00Z",
            "last_edited_time": "2024-06-01T12:00:00Z",
            "parent": {"type": "workspace"},
        }

        result = await connector._transform_to_webpage_record(obj_data, "page")
        assert result is not None
        assert result.record_type == RecordType.WEBPAGE
        assert result.record_name == "My Page"

    @pytest.mark.asyncio
    async def test_data_source_type(self):
        connector = _make_connector()
        connector.workspace_id = "ws1"
        connector._parse_iso_timestamp = MagicMock(return_value=1717228800000)

        obj_data = {
            "id": "ds1",
            "title": [{"plain_text": "My DB"}],
            "created_time": "2024-06-01T10:00:00Z",
            "last_edited_time": "2024-06-01T12:00:00Z",
        }

        result = await connector._transform_to_webpage_record(
            obj_data, "data_source", database_parent_id="parent-page"
        )
        assert result is not None
        assert result.record_type == RecordType.DATASOURCE
        assert result.parent_external_record_id == "parent-page"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector._extract_page_title = MagicMock(side_effect=Exception("parse error"))

        result = await connector._transform_to_webpage_record({"id": "p1"}, "page")
        assert result is None

# =============================================================================
# Merged from test_notion_connector_full_coverage.py
# =============================================================================

def _make_connector_fullcov():
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx
    cs = AsyncMock()
    conn = NotionConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="notion-conn-1",
    )
    conn._mock_tx = mock_tx
    return conn


def _api_resp(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.error = error
    if data is not None:
        resp.data = MagicMock()
        resp.data.json.return_value = data
    else:
        resp.data = None
    return resp


def _make_webpage_record(**kwargs):
    defaults = dict(
        org_id="org-1",
        record_name="Test Page",
        record_type=RecordType.WEBPAGE,
        external_record_id="page-123",
        connector_id="notion-conn-1",
        connector_name=Connectors.NOTION,
        record_group_type=RecordGroupType.NOTION_WORKSPACE,
        external_record_group_id="ws-1",
        mime_type=MimeTypes.BLOCKS.value,
        version=1,
        origin=OriginTypes.CONNECTOR,
    )
    defaults.update(kwargs)
    return WebpageRecord(**defaults)


def _make_file_record(**kwargs):
    defaults = dict(
        org_id="org-1",
        record_name="test.pdf",
        record_type=RecordType.FILE,
        external_record_id="file-123",
        connector_id="notion-conn-1",
        connector_name=Connectors.NOTION,
        record_group_type=RecordGroupType.NOTION_WORKSPACE,
        external_record_group_id="ws-1",
        mime_type=MimeTypes.PDF.value,
        version=1,
        origin=OriginTypes.CONNECTOR,
        is_file=True,
        size_in_bytes=100,
    )
    defaults.update(kwargs)
    return FileRecord(**defaults)


# ===================================================================
# Init / Connection / Cleanup
# ===================================================================

class TestInit:
    @pytest.mark.asyncio
    async def test_init_success(self):
        conn = _make_connector_fullcov()
        with patch(
            "app.connectors.sources.notion.connector.NotionClient.build_from_services",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_build.return_value = MagicMock()
            result = await conn.init()
        assert result is True
        assert conn.notion_client is not None
        assert conn.data_source is not None

    @pytest.mark.asyncio
    async def test_init_failure(self):
        conn = _make_connector_fullcov()
        with patch(
            "app.connectors.sources.notion.connector.NotionClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth error"),
        ):
            result = await conn.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_no_client(self):
        conn = _make_connector_fullcov()
        conn.notion_client = None
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        conn = _make_connector_fullcov()
        conn.notion_client = MagicMock()
        ds = MagicMock()
        ds.retrieve_bot_user = AsyncMock(return_value=_api_resp(True, {"object": "user"}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        conn = _make_connector_fullcov()
        conn.notion_client = MagicMock()
        ds = MagicMock()
        ds.retrieve_bot_user = AsyncMock(return_value=_api_resp(False, error="unauthorized"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        conn = _make_connector_fullcov()
        conn.notion_client = MagicMock()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("network"))
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup(self):
        conn = _make_connector_fullcov()
        conn.notion_client = MagicMock()
        conn.data_source = MagicMock()
        await conn.cleanup()
        assert conn.notion_client is None
        assert conn.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_exception_logged(self):
        conn = _make_connector_fullcov()
        conn.logger = MagicMock()
        del conn.notion_client
        del conn.data_source
        await conn.cleanup()

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        conn = _make_connector_fullcov()
        await conn.handle_webhook_notification({"type": "update"})
        conn.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates(self):
        conn = _make_connector_fullcov()
        conn.run_sync = AsyncMock()
        await conn.run_incremental_sync()
        conn.run_sync.assert_awaited_once()


# ===================================================================
# get_signed_url
# ===================================================================

class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_no_datasource_returns_none(self):
        conn = _make_connector_fullcov()
        conn.data_source = None
        record = _make_file_record(external_record_id="block-1")
        result = await conn.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_comment_attachment_prefix_ca(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn._get_comment_attachment_url = AsyncMock(return_value="https://signed.url/file")
        record = _make_file_record(external_record_id="ca_comment1_file.pdf")
        result = await conn.get_signed_url(record)
        assert result == "https://signed.url/file"

    @pytest.mark.asyncio
    async def test_comment_attachment_prefix_full(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn._get_comment_attachment_url = AsyncMock(return_value="https://x.url")
        record = _make_file_record(external_record_id="comment_attachment_xyz")
        result = await conn.get_signed_url(record)
        assert result == "https://x.url"

    @pytest.mark.asyncio
    async def test_block_file_url(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn._get_block_file_url = AsyncMock(return_value="https://block.url/f")
        record = _make_file_record(external_record_id="block-abc")
        result = await conn.get_signed_url(record)
        assert result == "https://block.url/f"

    @pytest.mark.asyncio
    async def test_get_signed_url_exception_raised(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn._get_block_file_url = AsyncMock(side_effect=Exception("fail"))
        record = _make_file_record(external_record_id="block-abc")
        with pytest.raises(Exception, match="fail"):
            await conn.get_signed_url(record)


# ===================================================================
# _get_comment_attachment_url
# ===================================================================

class TestGetCommentAttachmentUrl:
    @pytest.mark.asyncio
    async def test_invalid_prefix_raises(self):
        conn = _make_connector_fullcov()
        record = _make_file_record(external_record_id="invalid_format")
        with pytest.raises(ValueError, match="Invalid comment attachment"):
            await conn._get_comment_attachment_url(record)

    @pytest.mark.asyncio
    async def test_no_comment_id_raises(self):
        conn = _make_connector_fullcov()
        record = _make_file_record(external_record_id="ca_")
        with pytest.raises(ValueError, match="Failed to extract comment_id"):
            await conn._get_comment_attachment_url(record)

    @pytest.mark.asyncio
    async def test_no_normalized_filename_returns_signed_url(self):
        conn = _make_connector_fullcov()
        record = _make_file_record(external_record_id="ca_commentid123", signed_url="https://old.url")
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://old.url"

    @pytest.mark.asyncio
    async def test_api_failure_returns_signed_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comment = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(
            external_record_id="ca_commentid_report.pdf",
            signed_url="https://fallback.url",
        )
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://fallback.url"

    @pytest.mark.asyncio
    async def test_no_attachments_returns_signed_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comment = AsyncMock(return_value=_api_resp(True, {"attachments": []}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(
            external_record_id="ca_commentid_report.pdf",
            signed_url="https://fallback.url",
        )
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://fallback.url"

    @pytest.mark.asyncio
    async def test_matching_attachment_returns_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comment = AsyncMock(return_value=_api_resp(True, {
            "attachments": [
                {
                    "file": {"url": "https://notion.so/files/report.pdf?token=abc"},
                }
            ]
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._normalize_filename_for_id = MagicMock(return_value="report.pdf")
        record = _make_file_record(external_record_id="ca_commentid_report.pdf")
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://notion.so/files/report.pdf?token=abc"

    @pytest.mark.asyncio
    async def test_no_matching_filename_returns_signed_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comment = AsyncMock(return_value=_api_resp(True, {
            "attachments": [
                {"file": {"url": "https://notion.so/files/other.pdf?token=abc"}}
            ]
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._normalize_filename_for_id = MagicMock(side_effect=lambda f: f)
        record = _make_file_record(
            external_record_id="ca_commentid_report.pdf",
            signed_url="https://fallback.url",
        )
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://fallback.url"

    @pytest.mark.asyncio
    async def test_attachment_without_file_key_skipped(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comment = AsyncMock(return_value=_api_resp(True, {
            "attachments": [{"name": "no file key"}]
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(
            external_record_id="ca_commentid_report.pdf",
            signed_url="https://fallback.url",
        )
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://fallback.url"

    @pytest.mark.asyncio
    async def test_attachment_url_without_slash_uses_name_fallback(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comment = AsyncMock(return_value=_api_resp(True, {
            "attachments": [
                {"file": {"url": "https://flat-url"}, "name": "doc.pdf"}
            ]
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._normalize_filename_for_id = MagicMock(return_value="doc.pdf")
        record = _make_file_record(external_record_id="ca_commentid_doc.pdf")
        result = await conn._get_comment_attachment_url(record)
        assert result is not None


# ===================================================================
# _get_block_file_url
# ===================================================================

class TestGetBlockFileUrl:
    @pytest.mark.asyncio
    async def test_empty_block_id_raises(self):
        conn = _make_connector_fullcov()
        record = _make_file_record(external_record_id="")
        with pytest.raises(ValueError):
            await conn._get_block_file_url(record)

    @pytest.mark.asyncio
    async def test_api_failure_returns_signed_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(
            external_record_id="block-1",
            signed_url="https://old.url",
        )
        result = await conn._get_block_file_url(record)
        assert result == "https://old.url"

    @pytest.mark.asyncio
    async def test_file_key_returns_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "type": "pdf",
            "pdf": {"file": {"url": "https://s3.aws/doc.pdf"}},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(external_record_id="block-1")
        result = await conn._get_block_file_url(record)
        assert result == "https://s3.aws/doc.pdf"

    @pytest.mark.asyncio
    async def test_external_key_returns_url(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "type": "image",
            "image": {"external": {"url": "https://ext.com/img.png"}},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(external_record_id="block-2")
        result = await conn._get_block_file_url(record)
        assert result == "https://ext.com/img.png"

    @pytest.mark.asyncio
    async def test_no_url_returns_none(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "type": "video",
            "video": {},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        record = _make_file_record(external_record_id="block-3")
        result = await conn._get_block_file_url(record)
        assert result is None


# ===================================================================
# stream_record
# ===================================================================

class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_datasource_raises_500(self):
        conn = _make_connector_fullcov()
        conn.data_source = None
        record = _make_webpage_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_file_record_no_signed_url_raises_404(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn.get_signed_url = AsyncMock(return_value=None)
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_file_record_returns_streaming_response(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn.get_signed_url = AsyncMock(return_value="https://signed.url/file.pdf")
        record = _make_file_record(mime_type="application/pdf")
        response = await conn.stream_record(record)
        assert response.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_file_record_no_mimetype_defaults(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn.get_signed_url = AsyncMock(return_value="https://signed.url/file")
        record = _make_file_record(mime_type="")
        response = await conn.stream_record(record)
        assert response.media_type == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_datasource_record_streams_blocks(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        mock_container = MagicMock()
        mock_container.model_dump_json.return_value = '{"blocks":[],"block_groups":[]}'
        mock_container.blocks = []

        conn._fetch_data_source_as_blocks = AsyncMock(return_value=mock_container)
        conn._resolve_table_row_children = AsyncMock()

        record = _make_webpage_record(record_type=RecordType.DATASOURCE, external_record_id="ds-1")
        response = await conn.stream_record(record)
        assert response.media_type == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_webpage_record_streams_blocks(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        mock_container = MagicMock()
        mock_container.model_dump_json.return_value = '{"blocks":[],"block_groups":[]}'
        mock_container.blocks = []

        conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))
        conn._fetch_page_as_blocks = AsyncMock(return_value=mock_container)
        conn._resolve_child_reference_blocks = AsyncMock()

        record = _make_webpage_record(weburl="https://notion.so/page-123")
        response = await conn.stream_record(record)
        assert response.media_type == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_webpage_record_comments_fetch_failure_continues(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        mock_container = MagicMock()
        mock_container.model_dump_json.return_value = '{"blocks":[]}'
        mock_container.blocks = []

        conn._fetch_page_attachments_and_comments = AsyncMock(side_effect=Exception("comments fail"))
        conn._fetch_page_as_blocks = AsyncMock(return_value=mock_container)
        conn._resolve_child_reference_blocks = AsyncMock()

        record = _make_webpage_record()
        response = await conn.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_unsupported_record_type_raises_400(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        record = _make_webpage_record(record_type=RecordType.DATABASE)
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_generic_exception_wraps_in_500(self):
        conn = _make_connector_fullcov()
        conn.data_source = MagicMock()
        conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))
        conn._fetch_page_as_blocks = AsyncMock(side_effect=RuntimeError("boom"))

        record = _make_webpage_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500


# ===================================================================
# reindex_records
# ===================================================================

class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_list_returns_early(self):
        conn = _make_connector_fullcov()
        await conn.reindex_records([])
        conn.logger.info.assert_any_call("No records to reindex")

    @pytest.mark.asyncio
    async def test_non_empty_list(self):
        conn = _make_connector_fullcov()
        record = _make_webpage_record()
        await conn.reindex_records([record])
        conn.logger.info.assert_any_call("Starting reindex for 1 Notion records")

    @pytest.mark.asyncio
    async def test_exception_is_raised(self):
        conn = _make_connector_fullcov()
        conn.logger.info = MagicMock(side_effect=Exception("boom"))
        with pytest.raises(Exception):
            await conn.reindex_records([_make_webpage_record()])


# ===================================================================
# get_filter_options
# ===================================================================

class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        conn = _make_connector_fullcov()
        with pytest.raises(NotImplementedError):
            await conn.get_filter_options("some_key")


# ===================================================================
# _sync_users
# ===================================================================

class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_sync_users_no_users(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(True, {"results": [], "has_more": False}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_api_failure_raises(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(False, error="rate limit"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(Exception, match="Notion API error"):
            await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_api_none_response_raises(self):
        conn = _make_connector_fullcov()
        conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(
            list_users=AsyncMock(return_value=None)
        ))
        with pytest.raises(Exception, match="Notion API error"):
            await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_with_person_and_bot(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(True, {
            "results": [
                {"id": "user-1", "type": "person", "name": "Alice"},
                {"id": "bot-1", "type": "bot", "bot": {"workspace_id": "ws-1", "workspace_name": "My WS"}},
                {"id": "ext-1", "type": "external", "name": "External"},
            ],
            "has_more": False,
        }))
        ds.retrieve_user = AsyncMock(return_value=_api_resp(True, {
            "id": "user-1", "type": "person", "name": "Alice",
            "person": {"email": "alice@ex.com"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._create_workspace_record_group = AsyncMock()
        conn._add_users_to_workspace_permissions = AsyncMock()
        await conn._sync_users()
        assert conn.workspace_id == "ws-1"
        assert conn.workspace_name == "My WS"
        conn.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_users_retrieve_fails(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(True, {
            "results": [{"id": "u1", "type": "person"}],
            "has_more": False,
        }))
        ds.retrieve_user = AsyncMock(return_value=_api_resp(False, error="not found"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        await conn._sync_users()
        conn.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_retrieve_exception(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(True, {
            "results": [{"id": "u1", "type": "person"}],
            "has_more": False,
        }))
        ds.retrieve_user = AsyncMock(side_effect=Exception("network"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_pagination(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()

        page1 = _api_resp(True, {
            "results": [{"id": "u1", "type": "person"}],
            "has_more": True,
            "next_cursor": "cursor-2",
        })
        page2 = _api_resp(True, {
            "results": [{"id": "u2", "type": "person"}],
            "has_more": False,
        })
        ds.list_users = AsyncMock(side_effect=[page1, page2])
        ds.retrieve_user = AsyncMock(return_value=_api_resp(True, {
            "id": "u1", "type": "person", "name": "User",
            "person": {"email": "user@ex.com"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        await conn._sync_users()
        assert conn.data_entities_processor.on_new_app_users.await_count == 2

    @pytest.mark.asyncio
    async def test_sync_users_bot_without_workspace_id(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.list_users = AsyncMock(side_effect=[
            _api_resp(True, {
                "results": [
                    {"id": "bot-1", "type": "bot", "bot": {}},
                ],
                "has_more": False,
            }),
            _api_resp(True, {
                "results": [],
                "has_more": False,
            }),
        ])
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        await conn._sync_users()
        assert conn.workspace_id is None


# ===================================================================
# _add_users_to_workspace_permissions
# ===================================================================

class TestAddUsersToWorkspacePermissions:
    @pytest.mark.asyncio
    async def test_no_workspace_id_returns(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = None
        await conn._add_users_to_workspace_permissions(["a@b.com"])
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_emails_returns(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        await conn._add_users_to_workspace_permissions([])
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_record_group_if_not_exists(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "My Workspace"
        conn._mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        await conn._add_users_to_workspace_permissions(["alice@ex.com"])
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args = conn.data_entities_processor.on_new_record_groups.call_args[0][0]
        rg, perms = args[0]
        assert len(perms) == 1
        assert perms[0].email == "alice@ex.com"

    @pytest.mark.asyncio
    async def test_uses_existing_record_group(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "My Workspace"
        existing_rg = MagicMock()
        conn._mock_tx.get_record_group_by_external_id = AsyncMock(return_value=existing_rg)
        await conn._add_users_to_workspace_permissions(["bob@ex.com"])
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_raised(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "W"
        conn.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=Exception("db error"))
        with pytest.raises(Exception, match="db error"):
            await conn._add_users_to_workspace_permissions(["x@y.com"])


# ===================================================================
# _transform_to_app_user
# ===================================================================

class TestTransformToAppUser:
    def test_person_user_success(self):
        conn = _make_connector_fullcov()
        data = {
            "id": "u1", "type": "person", "name": "Alice",
            "person": {"email": "alice@ex.com"},
        }
        result = conn._transform_to_app_user(data)
        assert result is not None
        assert result.email == "alice@ex.com"

    def test_non_person_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_app_user({"id": "b1", "type": "bot"})
        assert result is None

    def test_no_id_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_app_user({"type": "person", "person": {"email": "a@b.c"}})
        assert result is None

    def test_no_email_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_app_user({"id": "u1", "type": "person", "person": {}})
        assert result is None

    def test_empty_email_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_app_user({"id": "u1", "type": "person", "person": {"email": "  "}})
        assert result is None

    def test_none_person_data(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_app_user({"id": "u1", "type": "person", "person": None})
        assert result is None


# ===================================================================
# _extract_page_title
# ===================================================================

class TestExtractPageTitle:
    def test_standard_title(self):
        conn = _make_connector_fullcov()
        data = {"properties": {"title": {"type": "title", "title": [{"plain_text": "My Page"}]}}}
        assert conn._extract_page_title(data) == "My Page"

    def test_name_property(self):
        conn = _make_connector_fullcov()
        data = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Named"}]}}}
        assert conn._extract_page_title(data) == "Named"

    def test_fallback_title_type(self):
        conn = _make_connector_fullcov()
        data = {"properties": {"custom_prop": {"type": "title", "title": [{"plain_text": "Custom"}]}}}
        assert conn._extract_page_title(data) == "Custom"

    def test_no_title_returns_untitled(self):
        conn = _make_connector_fullcov()
        data = {"properties": {}}
        assert conn._extract_page_title(data) == "Untitled"

    def test_empty_title_array_returns_untitled(self):
        conn = _make_connector_fullcov()
        data = {"properties": {"title": {"type": "title", "title": []}}}
        assert conn._extract_page_title(data) == "Untitled"


# ===================================================================
# _normalize_filename_for_id
# ===================================================================

class TestNormalizeFilenameForId:
    def test_basic(self):
        conn = _make_connector_fullcov()
        assert conn._normalize_filename_for_id("report.pdf") == "report.pdf"

    def test_empty_returns_attachment(self):
        conn = _make_connector_fullcov()
        assert conn._normalize_filename_for_id("") == "attachment"

    def test_none_returns_attachment(self):
        conn = _make_connector_fullcov()
        assert conn._normalize_filename_for_id(None) == "attachment"

    def test_url_encoded(self):
        conn = _make_connector_fullcov()
        assert conn._normalize_filename_for_id("my%20file.pdf") == "my file.pdf"

    def test_invalid_chars_replaced(self):
        conn = _make_connector_fullcov()
        result = conn._normalize_filename_for_id("file/with:special*chars")
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result

    def test_whitespace_only_returns_attachment(self):
        conn = _make_connector_fullcov()
        assert conn._normalize_filename_for_id("   ") == "attachment"


# ===================================================================
# _is_embed_platform_url
# ===================================================================

class TestIsEmbedPlatformUrl:
    def test_none_url(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url(None) is False

    def test_empty_url(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("") is False

    def test_youtube(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("https://youtube.com/watch?v=abc") is True

    def test_vimeo(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("https://vimeo.com/123") is True

    def test_direct_mp4(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("https://cdn.com/video.mp4") is False

    def test_direct_mp4_with_query(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("https://cdn.com/video.mp4?token=x") is False

    def test_direct_mp3(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("https://cdn.com/audio.mp3") is False

    def test_unknown_url_defaults_to_embed(self):
        conn = _make_connector_fullcov()
        assert conn._is_embed_platform_url("https://someplatform.com/player") is True


# ===================================================================
# _parse_iso_timestamp / _get_current_iso_time
# ===================================================================

class TestTimestampUtils:
    def test_parse_iso_timestamp_valid(self):
        conn = _make_connector_fullcov()
        with patch("app.connectors.sources.notion.connector.parse_timestamp", return_value=1700000000000):
            result = conn._parse_iso_timestamp("2023-11-14T22:13:20.000Z")
        assert result == 1700000000000

    def test_parse_iso_timestamp_invalid(self):
        conn = _make_connector_fullcov()
        with patch("app.connectors.sources.notion.connector.parse_timestamp", side_effect=ValueError("bad")):
            result = conn._parse_iso_timestamp("not-a-timestamp")
        assert result is None

    def test_get_current_iso_time(self):
        conn = _make_connector_fullcov()
        result = conn._get_current_iso_time()
        assert result.endswith("Z") or result.endswith("+00:00") or "T" in result


# ===================================================================
# _get_fresh_datasource
# ===================================================================

class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        conn = _make_connector_fullcov()
        conn.notion_client = None
        with pytest.raises(Exception, match="not initialized"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        conn = _make_connector_fullcov()
        conn.notion_client = MagicMock()
        conn.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_access_token_raises(self):
        conn = _make_connector_fullcov()
        conn.notion_client = MagicMock()
        conn.config_service.get_config = AsyncMock(return_value={"credentials": {}})
        with pytest.raises(Exception, match="No OAuth"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_token_unchanged(self):
        conn = _make_connector_fullcov()
        internal = MagicMock()
        internal.access_token = "token-abc"
        internal.headers = {"Authorization": "Bearer token-abc"}
        conn.notion_client = MagicMock()
        conn.notion_client.get_client.return_value = internal
        conn.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "token-abc"}}
        )
        ds = await conn._get_fresh_datasource()
        assert ds is not None

    @pytest.mark.asyncio
    async def test_token_updated(self):
        conn = _make_connector_fullcov()
        internal = MagicMock()
        internal.access_token = "old-token"
        internal.headers = {"Authorization": "Bearer old-token"}
        conn.notion_client = MagicMock()
        conn.notion_client.get_client.return_value = internal
        conn.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "new-token"}}
        )
        ds = await conn._get_fresh_datasource()
        assert internal.access_token == "new-token"
        assert internal.headers["Authorization"] == "Bearer new-token"


# ===================================================================
# _process_blocks_recursive
# ===================================================================

class TestProcessBlocksRecursive:
    @pytest.mark.asyncio
    async def test_no_children_returns_empty(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[])
        parser = MagicMock()
        blocks = []
        groups = []
        result = await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_archived_blocks(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[
            {"id": "b1", "type": "paragraph", "archived": True, "has_children": False},
        ])
        parser = MagicMock()
        blocks = []
        groups = []
        result = await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert len(blocks) == 0

    @pytest.mark.asyncio
    async def test_skips_in_trash_blocks(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[
            {"id": "b1", "type": "paragraph", "in_trash": True, "has_children": False},
        ])
        parser = MagicMock()
        blocks = []
        groups = []
        await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert len(blocks) == 0

    @pytest.mark.asyncio
    async def test_skips_unsupported_blocks(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[
            {"id": "b1", "type": "unsupported", "has_children": False},
        ])
        parser = MagicMock()
        blocks = []
        groups = []
        await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert len(blocks) == 0

    @pytest.mark.asyncio
    async def test_parsed_block_without_children(self):
        conn = _make_connector_fullcov()
        mock_block = MagicMock(spec=Block)
        mock_block.data = "text"
        mock_block.format = DataFormat.MARKDOWN

        conn._fetch_block_children_recursive = AsyncMock(return_value=[
            {"id": "b1", "type": "paragraph", "has_children": False, "paragraph": {}},
        ])

        parser = MagicMock()
        parser.parse_block = AsyncMock(return_value=(mock_block, None, None))

        blocks = []
        groups = []
        result = await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert len(blocks) == 1
        assert len(result) == 1
        assert result[0].block_index == 0

    @pytest.mark.asyncio
    async def test_parsed_block_with_children_creates_wrapper_group(self):
        conn = _make_connector_fullcov()
        mock_block = MagicMock(spec=Block)
        mock_block.data = "callout text"
        mock_block.format = DataFormat.MARKDOWN
        mock_block.type = BlockType.TEXT

        conn._fetch_block_children_recursive = AsyncMock(side_effect=[
            [{"id": "b1", "type": "callout", "has_children": True, "callout": {}}],
            [],
        ])

        parser = MagicMock()
        parser.parse_block = AsyncMock(return_value=(mock_block, None, None))

        blocks = []
        groups = []
        result = await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert len(blocks) == 1
        assert len(groups) == 1
        assert groups[0].sub_type == GroupSubType.CALLOUT

    @pytest.mark.asyncio
    async def test_synced_block_reference_fetches_from_original(self):
        conn = _make_connector_fullcov()
        mock_block = MagicMock(spec=Block)
        mock_block.data = "synced"
        mock_block.format = DataFormat.MARKDOWN
        mock_block.type = BlockType.TEXT

        conn._fetch_block_children_recursive = AsyncMock(side_effect=[
            [{
                "id": "synced-1",
                "type": "synced_block",
                "has_children": True,
                "synced_block": {
                    "synced_from": {"type": "block_id", "block_id": "original-1"},
                },
            }],
            [],
        ])

        parser = MagicMock()
        parser.parse_block = AsyncMock(return_value=(mock_block, None, None))

        blocks = []
        groups = []
        await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        calls = conn._fetch_block_children_recursive.call_args_list
        assert calls[1][0][0] == "original-1"

    @pytest.mark.asyncio
    async def test_parsed_group_with_children(self):
        conn = _make_connector_fullcov()
        mock_group = MagicMock(spec=BlockGroup)
        mock_group.children = None

        conn._fetch_block_children_recursive = AsyncMock(side_effect=[
            [{"id": "b1", "type": "toggle", "has_children": True, "toggle": {}}],
            [],
        ])

        parser = MagicMock()
        parser.parse_block = AsyncMock(return_value=(None, mock_group, None))

        blocks = []
        groups = []
        result = await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert len(groups) == 1
        assert result[0].block_group_index == 0

    @pytest.mark.asyncio
    async def test_parsed_group_synced_block_reference(self):
        conn = _make_connector_fullcov()
        mock_group = MagicMock(spec=BlockGroup)
        mock_group.children = None

        conn._fetch_block_children_recursive = AsyncMock(side_effect=[
            [{
                "id": "g1",
                "type": "synced_block",
                "has_children": True,
                "synced_block": {"synced_from": {"type": "block_id", "block_id": "orig-g1"}},
            }],
            [],
        ])

        parser = MagicMock()
        parser.parse_block = AsyncMock(return_value=(None, mock_group, None))

        blocks = []
        groups = []
        await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        calls = conn._fetch_block_children_recursive.call_args_list
        assert calls[1][0][0] == "orig-g1"

    @pytest.mark.asyncio
    async def test_unknown_block_with_children_processes_children(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(side_effect=[
            [{"id": "b1", "type": "unknown_type", "has_children": True, "unknown_type": {}}],
            [],
        ])

        parser = MagicMock()
        parser.parse_block = AsyncMock(return_value=(None, None, None))

        blocks = []
        groups = []
        await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert conn._fetch_block_children_recursive.await_count == 2


# ===================================================================
# _convert_image_blocks_to_base64
# ===================================================================

class TestConvertImageBlocksToBase64:
    @pytest.mark.asyncio
    async def test_no_image_blocks(self):
        conn = _make_connector_fullcov()
        blocks = [
            Block(id="b1", index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="text"),
        ]
        await conn._convert_image_blocks_to_base64(blocks)

    @pytest.mark.asyncio
    async def test_image_without_public_data_link_skipped(self):
        conn = _make_connector_fullcov()
        blocks = [
            Block(id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT, data="img", public_data_link=None),
        ]
        await conn._convert_image_blocks_to_base64(blocks)
        assert blocks[0].format == DataFormat.TXT

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_image_conversion_success(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector_fullcov()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT,
                data="old", public_data_link="https://notion.so/img.png",
                source_id="src-1",
            ),
        ]

        with patch("app.connectors.sources.notion.connector.get_extension_from_mimetype", return_value="png"):
            await conn._convert_image_blocks_to_base64(blocks)

        assert blocks[0].format == DataFormat.BASE64
        assert blocks[0].public_data_link is None
        assert "base64" in blocks[0].data["uri"]

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_image_svg_conversion(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector_fullcov()
        mock_parser_instance = MagicMock()
        mock_parser_instance.svg_base64_to_png_base64.return_value = "cG5nZGF0YQ=="
        mock_parser_cls.return_value = mock_parser_instance

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "image/svg+xml"}
        mock_response.read = AsyncMock(return_value=b"<svg></svg>")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT,
                data="old", public_data_link="https://notion.so/icon.svg",
            ),
        ]
        await conn._convert_image_blocks_to_base64(blocks)
        assert "image/png" in blocks[0].data["uri"]

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_image_fetch_error_raises(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector_fullcov()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(side_effect=Exception("404"))
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT,
                data="old", public_data_link="https://notion.so/missing.png",
            ),
        ]
        with pytest.raises(Exception):
            await conn._convert_image_blocks_to_base64(blocks)

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_image_empty_content_raises(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector_fullcov()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.read = AsyncMock(return_value=b"")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT,
                data="old", public_data_link="https://notion.so/empty.png",
            ),
        ]
        with pytest.raises(Exception):
            await conn._convert_image_blocks_to_base64(blocks)

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_image_no_extension_fallback(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector_fullcov()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_response.read = AsyncMock(return_value=b"\xff\xd8\xff")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT,
                data="old", public_data_link="https://notion.so/image_no_ext",
            ),
        ]
        with patch("app.connectors.sources.notion.connector.get_extension_from_mimetype", return_value=None):
            await conn._convert_image_blocks_to_base64(blocks)
        assert blocks[0].format == DataFormat.BASE64

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_non_image_content_type_raises(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector_fullcov()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.read = AsyncMock(return_value=b"<html></html>")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT,
                data="old", public_data_link="https://notion.so/notimage.html",
            ),
        ]
        with pytest.raises(Exception):
            await conn._convert_image_blocks_to_base64(blocks)


# ===================================================================
# _batch_get_or_create_child_records
# ===================================================================

class TestBatchGetOrCreateChildRecords:
    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        conn = _make_connector_fullcov()
        result = await conn._batch_get_or_create_child_records({})
        assert result == {}

    @pytest.mark.asyncio
    async def test_existing_record_found(self):
        conn = _make_connector_fullcov()
        existing = MagicMock()
        existing.id = "rec-db-1"
        existing.record_name = "Existing Page"
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        result = await conn._batch_get_or_create_child_records({
            "ext-1": ("Test", RecordType.WEBPAGE, None),
        })
        assert "ext-1" in result
        assert result["ext-1"].child_id == "rec-db-1"

    @pytest.mark.asyncio
    async def test_missing_record_created_as_webpage(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        conn.workspace_id = "ws-1"

        result = await conn._batch_get_or_create_child_records({
            "ext-1": ("New Page", RecordType.WEBPAGE, "parent-1"),
        })
        assert "ext-1" in result
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_record_created_as_file(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        result = await conn._batch_get_or_create_child_records({
            "ext-f": ("doc.pdf", RecordType.FILE, "parent-1"),
        })
        assert "ext-f" in result
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_record_created_as_datasource(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        result = await conn._batch_get_or_create_child_records({
            "ext-ds": ("My DS", RecordType.DATASOURCE, "parent-1"),
        })
        assert "ext-ds" in result
        args = conn.data_entities_processor.on_new_records.call_args[0][0]
        rec = args[0][0]
        assert rec.parent_record_type == RecordType.WEBPAGE

    @pytest.mark.asyncio
    async def test_mix_of_existing_and_missing(self):
        conn = _make_connector_fullcov()
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Existing"

        async def side_effect(connector_id, external_id):
            if external_id == "ext-1":
                return existing
            return None

        conn._mock_tx.get_record_by_external_id = AsyncMock(side_effect=side_effect)

        result = await conn._batch_get_or_create_child_records({
            "ext-1": ("Existing", RecordType.WEBPAGE, None),
            "ext-2": ("New", RecordType.WEBPAGE, None),
        })
        assert len(result) == 2
        assert result["ext-1"].child_id == "rec-1"


# ===================================================================
# _resolve_child_reference_blocks
# ===================================================================

class TestResolveChildReferenceBlocks:
    @pytest.mark.asyncio
    async def test_no_child_ref_blocks(self):
        conn = _make_connector_fullcov()
        blocks = [
            Block(id="b1", index=0, type=BlockType.TEXT, sub_type=BlockSubType.PARAGRAPH, format=DataFormat.MARKDOWN, data="text"),
        ]
        await conn._resolve_child_reference_blocks(blocks)

    @pytest.mark.asyncio
    async def test_resolves_non_database_reference(self):
        conn = _make_connector_fullcov()
        child_rec = ChildRecord(child_type=ChildType.RECORD, child_id="rec-1", child_name="Page")
        conn._batch_get_or_create_child_records = AsyncMock(return_value={"ext-1": child_rec})

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.TEXT, sub_type=BlockSubType.CHILD_RECORD,
                format=DataFormat.TXT, data="Child Page",
                source_id="ext-1", source_type="child_page", name="WEBPAGE",
            ),
        ]
        parent = _make_webpage_record()
        await conn._resolve_child_reference_blocks(blocks, parent_record=parent)
        assert blocks[0].table_row_metadata is not None
        assert blocks[0].table_row_metadata.children_records[0].child_id == "rec-1"

    @pytest.mark.asyncio
    async def test_resolves_database_reference(self):
        conn = _make_connector_fullcov()
        child_recs = [ChildRecord(child_type=ChildType.RECORD, child_id="ds-1", child_name="DS")]
        conn._resolve_database_to_data_sources = AsyncMock(return_value=child_recs)

        blocks = [
            Block(
                id="b1", index=0, type=BlockType.TEXT, sub_type=BlockSubType.CHILD_RECORD,
                format=DataFormat.TXT, data="Database Ref",
                source_id="db-1", source_type="link_to_database", name="DATABASE",
            ),
        ]
        await conn._resolve_child_reference_blocks(blocks)
        assert blocks[0].table_row_metadata.children_records[0].child_id == "ds-1"

    @pytest.mark.asyncio
    async def test_skips_already_resolved_blocks(self):
        conn = _make_connector_fullcov()
        existing_meta = TableRowMetadata(children_records=[ChildRecord(child_type=ChildType.RECORD, child_id="r1")])
        blocks = [
            Block(
                id="b1", index=0, type=BlockType.TEXT, sub_type=BlockSubType.CHILD_RECORD,
                format=DataFormat.TXT, data="Already resolved",
                source_id="ext-1", table_row_metadata=existing_meta,
            ),
        ]
        conn._batch_get_or_create_child_records = AsyncMock()
        await conn._resolve_child_reference_blocks(blocks)
        conn._batch_get_or_create_child_records.assert_not_awaited()


# ===================================================================
# _resolve_table_row_children
# ===================================================================

class TestResolveTableRowChildren:
    @pytest.mark.asyncio
    async def test_no_table_row_blocks(self):
        conn = _make_connector_fullcov()
        blocks = [Block(id="b1", index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="x")]
        await conn._resolve_table_row_children(blocks)

    @pytest.mark.asyncio
    async def test_table_rows_without_source_id(self):
        conn = _make_connector_fullcov()
        blocks = [Block(id="b1", index=0, type=BlockType.TABLE_ROW, format=DataFormat.MARKDOWN, data="x")]
        await conn._resolve_table_row_children(blocks)

    @pytest.mark.asyncio
    async def test_resolves_child_pages(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, {
            "results": [
                {"type": "child_page", "id": "child-1", "child_page": {"title": "Row Page"}},
            ],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        child_rec = ChildRecord(child_type=ChildType.RECORD, child_id="db-child-1", child_name="Row Page")
        conn._batch_get_or_create_child_records = AsyncMock(return_value={"child-1": child_rec})

        blocks = [
            Block(id="b1", index=0, type=BlockType.TABLE_ROW, format=DataFormat.MARKDOWN, data="x", source_id="row-1"),
        ]
        await conn._resolve_table_row_children(blocks)
        assert blocks[0].table_row_metadata is not None
        assert blocks[0].table_row_metadata.children_records[0].child_id == "db-child-1"

    @pytest.mark.asyncio
    async def test_no_child_pages_found(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, {"results": [], "has_more": False}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        blocks = [
            Block(id="b1", index=0, type=BlockType.TABLE_ROW, format=DataFormat.MARKDOWN, data="x", source_id="row-1"),
        ]
        await conn._resolve_table_row_children(blocks)
        assert blocks[0].table_row_metadata is None

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        blocks = [
            Block(id="b1", index=0, type=BlockType.TABLE_ROW, format=DataFormat.MARKDOWN, data="x", source_id="row-1"),
        ]
        await conn._resolve_table_row_children(blocks)


# ===================================================================
# _fetch_block_children_recursive
# ===================================================================

class TestFetchBlockChildrenRecursive:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, {
            "results": [{"id": "b1", "type": "paragraph"}],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(False, error="err"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_dict_response(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, "not a dict"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_pagination(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=[
            _api_resp(True, {"results": [{"id": "b1"}], "has_more": True, "next_cursor": "c2"}),
            _api_resp(True, {"results": [{"id": "b2"}], "has_more": False}),
        ])
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_exception_breaks_loop(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=Exception("network"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert result == []


# ===================================================================
# _fetch_comments_for_block
# ===================================================================

class TestFetchCommentsForBlock:
    @pytest.mark.asyncio
    async def test_empty_block_id(self):
        conn = _make_connector_fullcov()
        result = await conn._fetch_comments_for_block("")
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_block_id(self):
        conn = _make_connector_fullcov()
        result = await conn._fetch_comments_for_block("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(return_value=_api_resp(True, {
            "results": [{"id": "c1", "rich_text": []}],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        resp = _api_resp(False, error="unauthorized")
        resp.data = MagicMock()
        resp.data.json.return_value = {"object": "error", "message": "unauthorized"}
        ds.retrieve_comments = AsyncMock(return_value=resp)
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_error_object_in_response(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(return_value=_api_resp(True, {
            "object": "error",
            "code": "object_not_found",
            "message": "Block not found",
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_data_returns_empty(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = None
        ds.retrieve_comments = AsyncMock(return_value=resp)
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(side_effect=Exception("network"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_dict_data_returns_empty(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(return_value=_api_resp(True, "not a dict"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_parse_error_on_failure_response(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.error = "fail"
        resp.data = MagicMock()
        resp.data.json.side_effect = Exception("parse error")
        ds.retrieve_comments = AsyncMock(return_value=resp)
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []


# ===================================================================
# _fetch_comments_for_blocks
# ===================================================================

class TestFetchCommentsForBlocks:
    @pytest.mark.asyncio
    async def test_page_and_block_comments(self):
        conn = _make_connector_fullcov()
        conn._fetch_comments_for_block = AsyncMock(side_effect=[
            [{"id": "pc1"}],
            [{"id": "bc1"}],
        ])
        result = await conn._fetch_comments_for_blocks("page-1", ["block-1"])
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_block_comment_fetch_exception(self):
        conn = _make_connector_fullcov()
        conn._fetch_comments_for_block = AsyncMock(side_effect=[
            [],
            Exception("block error"),
        ])
        result = await conn._fetch_comments_for_blocks("page-1", ["block-1"])
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_overall_exception_returns_partial(self):
        conn = _make_connector_fullcov()
        conn._fetch_comments_for_block = AsyncMock(side_effect=Exception("total failure"))
        result = await conn._fetch_comments_for_blocks("page-1", [])
        assert result == []


# ===================================================================
# _fetch_attachment_blocks_and_block_ids_recursive
# ===================================================================

class TestFetchAttachmentBlocksAndBlockIds:
    @pytest.mark.asyncio
    async def test_file_blocks_collected(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, {
            "results": [
                {"id": "b1", "type": "file", "has_children": False},
                {"id": "b2", "type": "paragraph", "has_children": False},
            ],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 1
        assert "b1" in block_ids
        assert "b2" in block_ids

    @pytest.mark.asyncio
    async def test_child_page_blocks_skipped(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, {
            "results": [
                {"id": "cp1", "type": "child_page", "has_children": True},
            ],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 0
        assert "cp1" not in block_ids

    @pytest.mark.asyncio
    async def test_image_blocks_skipped_as_attachments_but_ids_collected(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, {
            "results": [
                {"id": "img1", "type": "image", "has_children": False},
            ],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 0
        assert "img1" in block_ids

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(False, error="fail"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(Exception, match="Notion API error"):
            await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")

    @pytest.mark.asyncio
    async def test_recurses_into_children(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=[
            _api_resp(True, {
                "results": [{"id": "parent-b", "type": "paragraph", "has_children": True}],
                "has_more": False,
            }),
            _api_resp(True, {
                "results": [{"id": "child-b", "type": "pdf", "has_children": False}],
                "has_more": False,
            }),
        ])
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 1
        assert "child-b" in block_ids

    @pytest.mark.asyncio
    async def test_non_dict_data(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, "not a dict"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert attachments == []
        assert block_ids == []

    @pytest.mark.asyncio
    async def test_image_with_children_recurses(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(side_effect=[
            _api_resp(True, {
                "results": [{"id": "img1", "type": "image", "has_children": True}],
                "has_more": False,
            }),
            _api_resp(True, {
                "results": [{"id": "nested-b", "type": "audio", "has_children": False}],
                "has_more": False,
            }),
        ])
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert len(attachments) == 1
        assert attachments[0]["type"] == "audio"


# ===================================================================
# _transform_to_file_record
# ===================================================================

class TestTransformToFileRecord:
    def test_basic_file_block(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        block = {
            "id": "block-1",
            "type": "file",
            "file": {"type": "file", "file": {"url": "https://s3.aws/doc.pdf"}},
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-02T00:00:00.000Z",
        }
        result = conn._transform_to_file_record(block, "page-1", "https://notion.so/page")
        assert result is not None
        assert result.record_type == RecordType.FILE

    def test_no_block_id_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_file_record({}, "page-1")
        assert result is None

    def test_bookmark_block_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_file_record({"id": "b1", "type": "bookmark"}, "page-1")
        assert result is None

    def test_embed_block_returns_none(self):
        conn = _make_connector_fullcov()
        result = conn._transform_to_file_record({"id": "b1", "type": "embed"}, "page-1")
        assert result is None

    def test_external_video_embed_returns_none(self):
        conn = _make_connector_fullcov()
        block = {
            "id": "b1",
            "type": "video",
            "video": {"type": "external", "external": {"url": "https://youtube.com/watch?v=abc"}},
        }
        result = conn._transform_to_file_record(block, "page-1")
        assert result is None

    def test_external_audio_direct_url_returns_record(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        block = {
            "id": "b1",
            "type": "audio",
            "audio": {"type": "external", "external": {"url": "https://cdn.com/song.mp3"}},
        }
        result = conn._transform_to_file_record(block, "page-1")
        assert result is not None

    def test_unsupported_type_returns_none(self):
        conn = _make_connector_fullcov()
        block = {"id": "b1", "type": "paragraph", "paragraph": {}}
        result = conn._transform_to_file_record(block, "page-1")
        assert result is None

    def test_no_file_url_returns_none(self):
        conn = _make_connector_fullcov()
        block = {"id": "b1", "type": "file", "file": {}}
        result = conn._transform_to_file_record(block, "page-1")
        assert result is None

    def test_pdf_default_name(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        block = {
            "id": "b1", "type": "pdf",
            "pdf": {"file": {"url": "https://s3.aws/"}},
        }
        result = conn._transform_to_file_record(block, "page-1")
        assert result is not None
        assert result.record_name == "document.pdf"

    def test_external_image_record(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        block = {
            "id": "b1", "type": "image",
            "image": {"type": "external", "external": {"url": "https://ext.com/photo.jpg"}},
        }
        result = conn._transform_to_file_record(block, "page-1", "https://notion.so/page")
        assert result is not None
        assert result.mime_type == "image/jpeg"


# ===================================================================
# _transform_to_comment_file_record
# ===================================================================

class TestTransformToCommentFileRecord:
    @pytest.mark.asyncio
    async def test_basic_attachment(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        attachment = {
            "file": {"url": "https://s3.aws/files/report.pdf"},
            "category": "productivity",
        }
        result = await conn._transform_to_comment_file_record(attachment, "comment-1", "page-1", "https://notion.so/page")
        assert result is not None
        assert result.external_record_id.startswith("ca_comment-1_")

    @pytest.mark.asyncio
    async def test_no_file_key_returns_none(self):
        conn = _make_connector_fullcov()
        result = await conn._transform_to_comment_file_record({"name": "x"}, "c1", "p1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_file_url_returns_none(self):
        conn = _make_connector_fullcov()
        result = await conn._transform_to_comment_file_record({"file": {"url": ""}}, "c1", "p1")
        assert result is None

    @pytest.mark.asyncio
    async def test_image_category_mime(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        attachment = {
            "file": {"url": "https://s3.aws/img.png"},
            "category": "image",
        }
        result = await conn._transform_to_comment_file_record(attachment, "c1", "p1")
        assert result.mime_type == MimeTypes.PNG.value

    @pytest.mark.asyncio
    async def test_no_category_guesses_mime(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        attachment = {
            "file": {"url": "https://s3.aws/files/presentation.pptx"},
        }
        result = await conn._transform_to_comment_file_record(attachment, "c1", "p1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_file_not_dict_returns_none(self):
        conn = _make_connector_fullcov()
        result = await conn._transform_to_comment_file_record({"file": "not-a-dict"}, "c1", "p1")
        assert result is None


# ===================================================================
# _extract_comment_attachment_file_records
# ===================================================================

class TestExtractCommentAttachmentFileRecords:
    @pytest.mark.asyncio
    async def test_extracts_file_records(self):
        conn = _make_connector_fullcov()
        fr = _make_file_record()
        conn._transform_to_comment_file_record = AsyncMock(return_value=fr)
        conn._normalize_filename_for_id = MagicMock(return_value="report.pdf")

        comments = {
            "block-1": [
                ({"id": "c1", "attachments": [
                    {"file": {"url": "https://s3.aws/report.pdf"}}
                ]}, "block-1"),
            ],
        }
        result = await conn._extract_comment_attachment_file_records(comments, "page-1", "https://notion.so/page")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_duplicate_filenames(self):
        conn = _make_connector_fullcov()
        fr = _make_file_record()
        conn._transform_to_comment_file_record = AsyncMock(return_value=fr)
        conn._normalize_filename_for_id = MagicMock(return_value="same.pdf")

        comments = {
            "block-1": [
                ({"id": "c1", "attachments": [
                    {"file": {"url": "https://s3.aws/same.pdf"}},
                    {"file": {"url": "https://s3.aws/same.pdf"}},
                ]}, "block-1"),
            ],
        }
        result = await conn._extract_comment_attachment_file_records(comments, "page-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_comment_id_skipped(self):
        conn = _make_connector_fullcov()
        comments = {
            "block-1": [({}, "block-1")],
        }
        result = await conn._extract_comment_attachment_file_records(comments, "page-1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_transform_failure_continues(self):
        conn = _make_connector_fullcov()
        conn._transform_to_comment_file_record = AsyncMock(side_effect=Exception("transform fail"))
        conn._normalize_filename_for_id = MagicMock(return_value="x.pdf")

        comments = {
            "block-1": [
                ({"id": "c1", "attachments": [{"file": {"url": "https://u/x.pdf"}}]}, "block-1"),
            ],
        }
        result = await conn._extract_comment_attachment_file_records(comments, "page-1")
        assert len(result) == 0


# ===================================================================
# _fetch_page_attachments_and_comments
# ===================================================================

class TestFetchPageAttachmentsAndComments:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        conn._fetch_attachment_blocks_and_block_ids_recursive = AsyncMock(return_value=([], ["b1"]))
        conn._transform_to_file_record = MagicMock(return_value=None)
        conn._fetch_comments_for_blocks = AsyncMock(return_value=[])

        files, comments = await conn._fetch_page_attachments_and_comments("page-1", "https://notion.so/page")
        assert files == []
        assert comments == {}

    @pytest.mark.asyncio
    async def test_with_attachments_and_comments(self):
        conn = _make_connector_fullcov()
        fr = _make_file_record()
        conn._fetch_attachment_blocks_and_block_ids_recursive = AsyncMock(
            return_value=([{"id": "f1", "type": "file"}], ["b1"])
        )
        conn._transform_to_file_record = MagicMock(return_value=fr)
        conn._fetch_comments_for_blocks = AsyncMock(
            return_value=[({"id": "c1"}, "b1")]
        )

        files, comments = await conn._fetch_page_attachments_and_comments("page-1", "")
        assert len(files) == 1
        assert "b1" in comments

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        conn = _make_connector_fullcov()
        conn._fetch_attachment_blocks_and_block_ids_recursive = AsyncMock(side_effect=Exception("boom"))
        files, comments = await conn._fetch_page_attachments_and_comments("page-1", "")
        assert files == []
        assert comments == {}


# ===================================================================
# _transform_to_webpage_record
# ===================================================================

class TestTransformToWebpageRecord:
    @pytest.mark.asyncio
    async def test_page_basic(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        data = {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "parent": {"type": "workspace"},
            "properties": {"title": {"type": "title", "title": [{"plain_text": "My Page"}]}},
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-02T00:00:00.000Z",
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result is not None
        assert result.record_name == "My Page"
        assert result.record_type == RecordType.WEBPAGE

    @pytest.mark.asyncio
    async def test_data_source_with_parent(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        data = {
            "id": "ds-1",
            "url": "https://notion.so/ds-1",
            "title": [{"plain_text": "My DB"}],
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-02T00:00:00.000Z",
        }
        result = await conn._transform_to_webpage_record(data, "data_source", database_parent_id="parent-page-1")
        assert result is not None
        assert result.record_type == RecordType.DATASOURCE
        assert result.parent_external_record_id == "parent-page-1"

    @pytest.mark.asyncio
    async def test_page_with_page_parent(self):
        conn = _make_connector_fullcov()
        data = {
            "id": "p2",
            "url": "https://notion.so/p2",
            "parent": {"type": "page_id", "page_id": "parent-p"},
            "properties": {"title": {"type": "title", "title": []}},
            "created_time": None,
            "last_edited_time": None,
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result.parent_external_record_id == "parent-p"
        assert result.parent_record_type == RecordType.WEBPAGE

    @pytest.mark.asyncio
    async def test_page_with_database_parent(self):
        conn = _make_connector_fullcov()
        data = {
            "id": "p3",
            "parent": {"type": "database_id", "database_id": "db-parent"},
            "properties": {},
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result.parent_record_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_page_with_block_parent(self):
        conn = _make_connector_fullcov()
        conn._resolve_block_parent_recursive = AsyncMock(return_value=("resolved-page", RecordType.WEBPAGE))
        data = {
            "id": "p4",
            "parent": {"type": "block_id", "block_id": "blk-1"},
            "properties": {},
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result.parent_external_record_id == "resolved-page"

    @pytest.mark.asyncio
    async def test_page_with_datasource_parent(self):
        conn = _make_connector_fullcov()
        data = {
            "id": "p5",
            "parent": {"type": "data_source_id", "data_source_id": "ds-parent"},
            "properties": {},
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result.parent_record_type == RecordType.DATASOURCE

    @pytest.mark.asyncio
    async def test_database_type(self):
        conn = _make_connector_fullcov()
        data = {
            "id": "db-1",
            "title": [{"plain_text": "My DB"}],
            "parent": {"type": "workspace"},
        }
        result = await conn._transform_to_webpage_record(data, "database")
        assert result.record_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_fullcov()
        conn._extract_page_title = MagicMock(side_effect=Exception("parse error"))
        result = await conn._transform_to_webpage_record({"id": "p1", "parent": {}}, "page")
        assert result is None

    @pytest.mark.asyncio
    async def test_block_parent_resolution_failure(self):
        conn = _make_connector_fullcov()
        conn._resolve_block_parent_recursive = AsyncMock(return_value=(None, None))
        data = {
            "id": "p6",
            "parent": {"type": "block_id", "block_id": "blk-bad"},
            "properties": {},
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result is not None
        assert result.parent_external_record_id is None


# ===================================================================
# _resolve_block_parent_recursive
# ===================================================================

class TestResolveBlockParentRecursive:
    @pytest.mark.asyncio
    async def test_page_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "page_id", "page_id": "page-parent"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id == "page-parent"
        assert parent_type == RecordType.WEBPAGE

    @pytest.mark.asyncio
    async def test_database_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "database_id", "database_id": "db-parent"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id == "db-parent"
        assert parent_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_datasource_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "data_source_id", "data_source_id": "ds-parent"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_type == RecordType.DATASOURCE

    @pytest.mark.asyncio
    async def test_recursive_block_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(side_effect=[
            _api_resp(True, {"parent": {"type": "block_id", "block_id": "block-2"}}),
            _api_resp(True, {"parent": {"type": "page_id", "page_id": "page-final"}}),
        ])
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id == "page-final"

    @pytest.mark.asyncio
    async def test_max_depth_reached(self):
        conn = _make_connector_fullcov()
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1", max_depth=0)
        assert parent_id is None
        assert parent_type is None

    @pytest.mark.asyncio
    async def test_cycle_detection(self):
        conn = _make_connector_fullcov()
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1", visited={"block-1"})
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_workspace_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "workspace"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_fullcov()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_block_parent_with_no_next_block_id(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "block_id", "block_id": None}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None


# ===================================================================
# _get_database_parent_page_id
# ===================================================================

class TestGetDatabaseParentPageId:
    @pytest.mark.asyncio
    async def test_page_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "page_id", "page_id": "page-p"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "page-p"

    @pytest.mark.asyncio
    async def test_database_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "database_id", "database_id": "db-p"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "db-p"

    @pytest.mark.asyncio
    async def test_block_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "block_id", "block_id": "blk-1"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._resolve_block_parent_recursive = AsyncMock(return_value=("resolved-page", RecordType.WEBPAGE))
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "resolved-page"

    @pytest.mark.asyncio
    async def test_data_source_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "data_source_id", "data_source_id": "ds-p"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "ds-p"

    @pytest.mark.asyncio
    async def test_workspace_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "workspace"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        conn = _make_connector_fullcov()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_block_parent_no_block_id(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "block_id"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None


# ===================================================================
# resolve_page_title_by_id / resolve_user_name_by_id
# ===================================================================

class TestResolveHelpers:
    @pytest.mark.asyncio
    async def test_resolve_page_title_from_db(self):
        conn = _make_connector_fullcov()
        record = MagicMock()
        record.record_name = "DB Page"
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        result = await conn.resolve_page_title_by_id("page-1")
        assert result == "DB Page"

    @pytest.mark.asyncio
    async def test_resolve_page_title_from_api(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        ds = MagicMock()
        ds.retrieve_page = AsyncMock(return_value=_api_resp(True, {
            "properties": {"title": {"type": "title", "title": [{"plain_text": "API Page"}]}}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_page_title_by_id("page-1")
        assert result == "API Page"

    @pytest.mark.asyncio
    async def test_resolve_page_title_not_found(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        ds = MagicMock()
        ds.retrieve_page = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_page_title_by_id("page-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_page_title_exception(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))
        result = await conn.resolve_page_title_by_id("page-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_name_person(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_user = AsyncMock(return_value=_api_resp(True, {
            "object": "user", "type": "person", "name": "Alice",
            "person": {"email": "a@b.c"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_user_name_by_id("u1")
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_resolve_user_name_person_email_fallback(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_user = AsyncMock(return_value=_api_resp(True, {
            "object": "user", "type": "person", "name": "",
            "person": {"email": "alice@ex.com"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_user_name_by_id("u1")
        assert result == "alice@ex.com"

    @pytest.mark.asyncio
    async def test_resolve_user_name_bot(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_user = AsyncMock(return_value=_api_resp(True, {
            "object": "user", "type": "bot", "name": "My Bot",
            "bot": {},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_user_name_by_id("b1")
        assert result == "My Bot"

    @pytest.mark.asyncio
    async def test_resolve_user_name_bot_owner_fallback(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_user = AsyncMock(return_value=_api_resp(True, {
            "object": "user", "type": "bot", "name": "",
            "bot": {"owner": {"type": "user", "user": {"name": "Owner"}}},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_user_name_by_id("b1")
        assert result == "Owner"

    @pytest.mark.asyncio
    async def test_resolve_user_name_not_found(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_user = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_user_name_by_id("u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_name_exception(self):
        conn = _make_connector_fullcov()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn.resolve_user_name_by_id("u1")
        assert result is None


# ===================================================================
# get_record_by_external_id / get_record_child_by_external_id / get_user_child_by_external_id
# ===================================================================

class TestRecordAndUserLookups:
    @pytest.mark.asyncio
    async def test_get_record_by_external_id_found(self):
        conn = _make_connector_fullcov()
        record = MagicMock()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        result = await conn.get_record_by_external_id("ext-1")
        assert result is record

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_not_found(self):
        conn = _make_connector_fullcov()
        result = await conn.get_record_by_external_id("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_exception(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))
        result = await conn.get_record_by_external_id("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_record_child_existing(self):
        conn = _make_connector_fullcov()
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Page"
        conn.get_record_by_external_id = AsyncMock(return_value=record)
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result.child_id == "rec-1"

    @pytest.mark.asyncio
    async def test_get_record_child_not_exists_with_parent(self):
        conn = _make_connector_fullcov()
        conn.get_record_by_external_id = AsyncMock(return_value=None)
        conn.resolve_page_title_by_id = AsyncMock(return_value="Resolved Title")
        result = await conn.get_record_child_by_external_id("ext-1", parent_data_source_id="ds-1")
        assert result is not None
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_record_child_not_exists_no_parent(self):
        conn = _make_connector_fullcov()
        conn.get_record_by_external_id = AsyncMock(return_value=None)
        conn.resolve_page_title_by_id = AsyncMock(return_value="Resolved")
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result is not None
        assert result.child_name == "Resolved"

    @pytest.mark.asyncio
    async def test_get_record_child_not_exists_no_title(self):
        conn = _make_connector_fullcov()
        conn.get_record_by_external_id = AsyncMock(return_value=None)
        conn.resolve_page_title_by_id = AsyncMock(return_value=None)
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result is not None
        assert "ext-1" in result.child_name

    @pytest.mark.asyncio
    async def test_get_record_child_exception(self):
        conn = _make_connector_fullcov()
        conn.get_record_by_external_id = AsyncMock(side_effect=Exception("fail"))
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_child_from_db(self):
        conn = _make_connector_fullcov()
        user = MagicMock()
        user.id = "user-db-1"
        user.full_name = "Alice"
        user.email = "alice@ex.com"
        conn._mock_tx.get_user_by_source_id = AsyncMock(return_value=user)
        result = await conn.get_user_child_by_external_id("u1")
        assert result.child_id == "user-db-1"
        assert result.child_type == ChildType.USER

    @pytest.mark.asyncio
    async def test_get_user_child_not_in_db(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        conn.resolve_user_name_by_id = AsyncMock(return_value="Bob")
        result = await conn.get_user_child_by_external_id("u1")
        assert result.child_id == "u1"
        assert result.child_name == "Bob"

    @pytest.mark.asyncio
    async def test_get_user_child_not_in_db_no_name(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        conn.resolve_user_name_by_id = AsyncMock(return_value=None)
        result = await conn.get_user_child_by_external_id("u1")
        assert "u1" in result.child_name

    @pytest.mark.asyncio
    async def test_get_user_child_exception(self):
        conn = _make_connector_fullcov()
        conn._mock_tx.get_user_by_source_id = AsyncMock(side_effect=Exception("db"))
        result = await conn.get_user_child_by_external_id("u1")
        assert result is None


# ===================================================================
# _create_workspace_record_group
# ===================================================================

class TestCreateWorkspaceRecordGroup:
    @pytest.mark.asyncio
    async def test_no_workspace_id(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = None
        conn.workspace_name = "WS"
        await conn._create_workspace_record_group()
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_workspace_name(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        conn.workspace_name = None
        await conn._create_workspace_record_group()
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_record_group(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "My Workspace"
        await conn._create_workspace_record_group()
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_raised(self):
        conn = _make_connector_fullcov()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "WS"
        conn.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=Exception("db"))
        with pytest.raises(Exception):
            await conn._create_workspace_record_group()


# ===================================================================
# _resolve_author_name
# ===================================================================

class TestResolveAuthorName:
    @pytest.mark.asyncio
    async def test_resolves_author(self):
        conn = _make_connector_fullcov()
        child = ChildRecord(child_type=ChildType.USER, child_id="u1", child_name="Alice")
        conn.get_user_child_by_external_id = AsyncMock(return_value=child)
        result = await conn._resolve_author_name({"created_by": {"id": "u1"}})
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_no_author_id(self):
        conn = _make_connector_fullcov()
        result = await conn._resolve_author_name({"created_by": {}})
        assert result is None

    @pytest.mark.asyncio
    async def test_no_created_by(self):
        conn = _make_connector_fullcov()
        result = await conn._resolve_author_name({})
        assert result is None

    @pytest.mark.asyncio
    async def test_user_lookup_returns_none(self):
        conn = _make_connector_fullcov()
        conn.get_user_child_by_external_id = AsyncMock(return_value=None)
        result = await conn._resolve_author_name({"created_by": {"id": "u1"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_fullcov()
        conn.get_user_child_by_external_id = AsyncMock(side_effect=Exception("fail"))
        result = await conn._resolve_author_name({"created_by": {"id": "u1"}})
        assert result is None


# ===================================================================
# _process_comment_attachments
# ===================================================================

class TestProcessCommentAttachments:
    @pytest.mark.asyncio
    async def test_basic_attachment(self):
        conn = _make_connector_fullcov()
        fr = _make_file_record(record_name="doc.pdf")
        conn._transform_to_comment_file_record = AsyncMock(return_value=fr)
        conn._normalize_filename_for_id = MagicMock(return_value="doc.pdf")

        comment = {"attachments": [{"file": {"url": "https://s3/doc.pdf"}}]}
        file_records, attachments = await conn._process_comment_attachments(comment, "c1", "p1", "url")
        assert len(file_records) == 1
        assert len(attachments) == 1

    @pytest.mark.asyncio
    async def test_no_file_url_skipped(self):
        conn = _make_connector_fullcov()
        comment = {"attachments": [{"file": {}}]}
        file_records, attachments = await conn._process_comment_attachments(comment, "c1", "p1", None)
        assert len(file_records) == 0

    @pytest.mark.asyncio
    async def test_duplicate_filenames_skipped(self):
        conn = _make_connector_fullcov()
        fr = _make_file_record()
        conn._transform_to_comment_file_record = AsyncMock(return_value=fr)
        conn._normalize_filename_for_id = MagicMock(return_value="same.pdf")

        comment = {"attachments": [
            {"file": {"url": "https://s3/a.pdf"}},
            {"file": {"url": "https://s3/b.pdf"}},
        ]}
        file_records, attachments = await conn._process_comment_attachments(comment, "c1", "p1", None)
        assert len(file_records) == 1

    @pytest.mark.asyncio
    async def test_transform_error_continues(self):
        conn = _make_connector_fullcov()
        conn._transform_to_comment_file_record = AsyncMock(side_effect=Exception("fail"))
        conn._normalize_filename_for_id = MagicMock(return_value="x.pdf")

        comment = {"attachments": [{"file": {"url": "https://s3/x.pdf"}}]}
        file_records, attachments = await conn._process_comment_attachments(comment, "c1", "p1", None)
        assert len(file_records) == 0


# ===================================================================
# _create_block_comment_from_notion_comment
# ===================================================================

class TestCreateBlockComment:
    @pytest.mark.asyncio
    async def test_no_comment_id(self):
        conn = _make_connector_fullcov()
        result, files = await conn._create_block_comment_from_notion_comment({}, "p1", MagicMock())
        assert result is None
        assert files == []

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        conn._resolve_author_name = AsyncMock(return_value="Alice")
        conn._process_comment_attachments = AsyncMock(return_value=([], []))
        mock_parser = MagicMock()
        mock_comment = MagicMock(spec=BlockComment)
        mock_parser.parse_notion_comment_to_block_comment.return_value = mock_comment

        result, files = await conn._create_block_comment_from_notion_comment(
            {"id": "c1", "rich_text": [], "discussion_id": "d1"},
            "p1",
            mock_parser,
            page_url="https://notion.so/p1",
        )
        assert result is mock_comment

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_fullcov()
        conn._resolve_author_name = AsyncMock(side_effect=Exception("fail"))
        result, files = await conn._create_block_comment_from_notion_comment(
            {"id": "c1"},
            "p1",
            MagicMock(),
        )
        assert result is None
        assert files == []


# ===================================================================
# _extract_block_text_content
# ===================================================================

class TestExtractBlockTextContent:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "hello"}]}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()
        parser.extract_rich_text_from_block_data.return_value = [{"plain_text": "hello"}]
        parser.extract_rich_text.return_value = "hello"

        result = await conn._extract_block_text_content("b1", parser)
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()
        result = await conn._extract_block_text_content("b1", parser)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_rich_text(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {"type": "divider", "divider": {}}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()
        parser.extract_rich_text_from_block_data.return_value = None
        result = await conn._extract_block_text_content("b1", parser)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_fullcov()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn._extract_block_text_content("b1", MagicMock())
        assert result is None


# ===================================================================
# _resolve_database_to_data_sources
# ===================================================================

class TestResolveDatabaseToDataSources:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "data_sources": [{"id": "ds-1", "name": "DS 1"}],
            "parent": {"type": "page_id", "page_id": "parent-p"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        child_rec = ChildRecord(child_type=ChildType.RECORD, child_id="rec-ds-1", child_name="DS 1")
        conn._batch_get_or_create_child_records = AsyncMock(return_value={"ds-1": child_rec})

        result = await conn._resolve_database_to_data_sources("db-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_data_sources(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "data_sources": [],
            "parent": {"type": "workspace"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._resolve_database_to_data_sources("db-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._resolve_database_to_data_sources("db-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self):
        conn = _make_connector_fullcov()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn._resolve_database_to_data_sources("db-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_block_parent_resolved(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "data_sources": [{"id": "ds-1", "name": "DS"}],
            "parent": {"type": "block_id", "block_id": "blk-1"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._resolve_block_parent_recursive = AsyncMock(return_value=("resolved-p", RecordType.WEBPAGE))
        conn._batch_get_or_create_child_records = AsyncMock(return_value={"ds-1": ChildRecord(child_type=ChildType.RECORD, child_id="r1")})
        result = await conn._resolve_database_to_data_sources("db-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_datasource_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "data_sources": [{"id": "ds-1", "name": "DS"}],
            "parent": {"type": "data_source_id", "data_source_id": "ds-parent"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._batch_get_or_create_child_records = AsyncMock(return_value={"ds-1": ChildRecord(child_type=ChildType.RECORD, child_id="r1")})
        result = await conn._resolve_database_to_data_sources("db-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_database_parent(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "data_sources": [{"id": "ds-1", "name": "DS"}],
            "parent": {"type": "database_id", "database_id": "db-parent"},
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._batch_get_or_create_child_records = AsyncMock(return_value={"ds-1": ChildRecord(child_type=ChildType.RECORD, child_id="r1")})
        result = await conn._resolve_database_to_data_sources("db-1")
        assert len(result) == 1


# ===================================================================
# _attach_comments_to_blocks
# ===================================================================

class TestAttachCommentsToBlocks:
    @pytest.mark.asyncio
    async def test_block_level_comments_attached(self):
        conn = _make_connector_fullcov()
        mock_comment = MagicMock(spec=BlockComment)
        mock_comment.thread_id = "thread-1"
        conn._create_block_comment_from_notion_comment = AsyncMock(return_value=(mock_comment, []))
        conn._extract_block_text_content = AsyncMock(return_value="block text")
        conn._create_page_level_comment_groups = AsyncMock(return_value=[])

        block = Block(
            id="b1", index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN,
            data="text", source_id="notion-block-1",
        )
        blocks = [block]
        groups = []
        comments_by_block = {
            "notion-block-1": [({"id": "c1", "discussion_id": "thread-1"}, "notion-block-1")],
        }

        await conn._attach_comments_to_blocks(blocks, groups, comments_by_block, "page-1", "url", MagicMock())
        assert block.comments is not None
        assert len(block.comments) == 1

    @pytest.mark.asyncio
    async def test_page_level_comments_create_groups(self):
        conn = _make_connector_fullcov()
        conn._extract_block_text_content = AsyncMock(return_value=None)
        conn._create_page_level_comment_groups = AsyncMock(return_value=[_make_file_record()])

        blocks = []
        groups = []
        comments_by_block = {
            "page-1": [({"id": "c1", "discussion_id": "d1"}, "page-1")],
        }

        result = await conn._attach_comments_to_blocks(blocks, groups, comments_by_block, "page-1", "url", MagicMock())
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_comment_creation_error_continues(self):
        conn = _make_connector_fullcov()
        conn._create_block_comment_from_notion_comment = AsyncMock(side_effect=Exception("fail"))
        conn._extract_block_text_content = AsyncMock(return_value=None)
        conn._create_page_level_comment_groups = AsyncMock(return_value=[])

        block = Block(
            id="b1", index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN,
            data="text", source_id="blk-1",
        )
        blocks = [block]
        comments_by_block = {"blk-1": [({"id": "c1"}, "blk-1")]}

        await conn._attach_comments_to_blocks(blocks, [], comments_by_block, "page-1", "url", MagicMock())
        assert block.comments == []


# ===================================================================
# _sync_objects_by_type (pages/data_sources)
# ===================================================================

class TestSyncObjectsByType:
    @pytest.mark.asyncio
    async def test_no_objects_found(self):
        conn = _make_connector_fullcov()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=True)
        conn.pages_sync_point = MagicMock()
        conn.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.search = AsyncMock(return_value=_api_resp(True, {"results": [], "has_more": False}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        await conn._sync_objects_by_type("page")

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        conn = _make_connector_fullcov()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=True)
        conn.pages_sync_point = MagicMock()
        conn.pages_sync_point.read_sync_point = AsyncMock(return_value=None)

        ds = MagicMock()
        ds.search = AsyncMock(return_value=_api_resp(False, error="api error"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        with pytest.raises(Exception, match="Notion API error"):
            await conn._sync_objects_by_type("page")

    @pytest.mark.asyncio
    async def test_pages_synced_with_indexing_disabled(self):
        conn = _make_connector_fullcov()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        conn.pages_sync_point = MagicMock()
        conn.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.search = AsyncMock(return_value=_api_resp(True, {
            "results": [{
                "id": "p1",
                "object": "page",
                "last_edited_time": "2024-01-01T00:00:00.000Z",
                "url": "https://notion.so/p1",
                "parent": {"type": "workspace"},
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Test"}]}},
            }],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))

        await conn._sync_objects_by_type("page")
        conn.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_archived_pages_skipped(self):
        conn = _make_connector_fullcov()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=True)
        conn.pages_sync_point = MagicMock()
        conn.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.search = AsyncMock(return_value=_api_resp(True, {
            "results": [{"id": "p1", "archived": True, "last_edited_time": "2024-01-01T00:00:00.000Z"}],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        await conn._sync_objects_by_type("page")
        conn.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delta_sync_stops_at_threshold(self):
        conn = _make_connector_fullcov()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=True)
        conn.pages_sync_point = MagicMock()
        conn.pages_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2024-06-01T00:00:00.000Z"})
        conn.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.search = AsyncMock(return_value=_api_resp(True, {
            "results": [{
                "id": "old-p",
                "last_edited_time": "2024-05-01T00:00:00.000Z",
                "parent": {"type": "workspace"},
                "properties": {},
            }],
            "has_more": False,
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        await conn._sync_objects_by_type("page")
        conn.data_entities_processor.on_new_records.assert_not_awaited()


# ===================================================================
# create_connector factory
# ===================================================================

class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_factory_method(self):
        with patch(
            "app.connectors.sources.notion.connector.DataSourceEntitiesProcessor"
        ) as mock_dep_cls:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            mock_dep.org_id = "org-1"
            mock_dep_cls.return_value = mock_dep

            logger = MagicMock()
            dsp = MagicMock()
            mock_tx = MagicMock()
            mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
            mock_tx.__aexit__ = AsyncMock(return_value=None)
            dsp.transaction.return_value = mock_tx
            cs = AsyncMock()

            connector = await NotionConnector.create_connector(logger, dsp, cs, "conn-1")
            assert isinstance(connector, NotionConnector)
            mock_dep.initialize.assert_awaited_once()


# ===================================================================
# run_sync
# ===================================================================

class TestRunSync:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_fullcov()
        with patch("app.connectors.sources.notion.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (MagicMock(), MagicMock())
            conn._sync_users = AsyncMock()
            conn._sync_objects_by_type = AsyncMock()
            await conn.run_sync()
            conn._sync_users.assert_awaited_once()
            assert conn._sync_objects_by_type.await_count == 2

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        conn = _make_connector_fullcov()
        with patch("app.connectors.sources.notion.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (MagicMock(), MagicMock())
            conn._sync_users = AsyncMock(side_effect=Exception("sync fail"))
            with pytest.raises(Exception, match="sync fail"):
                await conn.run_sync()


# ===================================================================
# _fetch_page_as_blocks / _fetch_data_source_as_blocks
# ===================================================================

class TestFetchAsBlocks:
    @pytest.mark.asyncio
    async def test_fetch_page_as_blocks(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[])

        parser = MagicMock()
        parser.post_process_blocks = MagicMock()

        conn._convert_image_blocks_to_base64 = AsyncMock()

        result = await conn._fetch_page_as_blocks("page-1", parser)
        assert result is not None
        parser.post_process_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_page_as_blocks_with_comments(self):
        conn = _make_connector_fullcov()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[])

        parser = MagicMock()
        parser.post_process_blocks = MagicMock()

        conn._convert_image_blocks_to_base64 = AsyncMock()
        conn._attach_comments_to_blocks = AsyncMock(return_value=[])

        comments = {"block-1": [({"id": "c1"}, "block-1")]}
        result = await conn._fetch_page_as_blocks("page-1", parser, comments_by_block=comments)
        conn._attach_comments_to_blocks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_data_source_as_blocks(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_data_source_by_id = AsyncMock(return_value=_api_resp(True, {"properties": {}}))
        ds.query_data_source_by_id = AsyncMock(return_value=_api_resp(True, {"results": [], "has_more": False}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        parser = MagicMock()
        parser.parse_data_source_to_blocks = AsyncMock(return_value=([], []))

        result = await conn._fetch_data_source_as_blocks("ds-1", parser)
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_data_source_metadata_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_data_source_by_id = AsyncMock(return_value=_api_resp(False, error="not found"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()

        result = await conn._fetch_data_source_as_blocks("ds-1", parser)
        assert result.blocks == []

    @pytest.mark.asyncio
    async def test_fetch_data_source_query_failure(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_data_source_by_id = AsyncMock(return_value=_api_resp(True, {"properties": {}}))
        ds.query_data_source_by_id = AsyncMock(return_value=_api_resp(False, error="query fail"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        parser = MagicMock()
        parser.parse_data_source_to_blocks = AsyncMock(return_value=([], []))

        result = await conn._fetch_data_source_as_blocks("ds-1", parser)
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_data_source_query_exception(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_data_source_by_id = AsyncMock(return_value=_api_resp(True, {"properties": {}}))
        ds.query_data_source_by_id = AsyncMock(side_effect=Exception("db error"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        parser = MagicMock()
        parser.parse_data_source_to_blocks = AsyncMock(return_value=([], []))

        result = await conn._fetch_data_source_as_blocks("ds-1", parser)
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_data_source_pagination(self):
        conn = _make_connector_fullcov()
        ds = MagicMock()
        ds.retrieve_data_source_by_id = AsyncMock(return_value=_api_resp(True, {"properties": {}}))
        ds.query_data_source_by_id = AsyncMock(side_effect=[
            _api_resp(True, {"results": [{"id": "r1"}], "has_more": True, "next_cursor": "c2"}),
            _api_resp(True, {"results": [{"id": "r2"}], "has_more": False}),
        ])
        conn._get_fresh_datasource = AsyncMock(return_value=ds)

        parser = MagicMock()
        parser.parse_data_source_to_blocks = AsyncMock(return_value=([], []))

        result = await conn._fetch_data_source_as_blocks("ds-1", parser)
        assert result is not None
