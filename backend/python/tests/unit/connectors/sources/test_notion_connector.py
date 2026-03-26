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
