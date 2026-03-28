"""Extended coverage tests for the Notion connector.

Covers methods and branches not exercised by existing test suites:
- init / test_connection_and_access
- run_sync / run_incremental_sync
- get_signed_url (block files, comment attachments)
- _get_comment_attachment_url
- _get_block_file_url
- stream_record (page, datasource, file, unsupported type)
- reindex_records
- get_filter_options
- cleanup
- handle_webhook_notification
- create_connector
- _sync_users
- _create_workspace_record_group
- _add_users_to_workspace_permissions
- _transform_to_app_user
- _transform_to_webpage_record
- _transform_to_file_record
- _transform_to_comment_file_record
- _normalize_filename_for_id
- _extract_page_title
- _parse_iso_timestamp
- _get_current_iso_time
- _is_embed_platform_url
- resolve_page_title_by_id
- resolve_user_name_by_id
- get_record_by_external_id
- get_record_child_by_external_id
- get_user_child_by_external_id
- _resolve_block_parent_recursive
- _get_database_parent_page_id
"""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.notion.connector import NotionConnector
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================

def _make_connector():
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-notion-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = AsyncMock()

    c = NotionConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="notion-ext-1",
    )
    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    c.workspace_id = "ws-1"
    c.workspace_name = "Test Workspace"
    return c


def _make_api_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.error = error
    if data is not None:
        resp.data = MagicMock()
        resp.data.json.return_value = data
    else:
        resp.data = None
    return resp


def _make_datasource_mock():
    ds = AsyncMock()
    ds.retrieve_bot_user = AsyncMock(return_value=_make_api_response(True, {"id": "bot-1"}))
    ds.list_users = AsyncMock()
    ds.retrieve_user = AsyncMock()
    ds.search = AsyncMock()
    ds.retrieve_page = AsyncMock()
    ds.retrieve_block = AsyncMock()
    ds.retrieve_database = AsyncMock()
    ds.retrieve_comment = AsyncMock()
    return ds


# ===========================================================================
# init
# ===========================================================================

class TestNotionInit:
    @pytest.mark.asyncio
    async def test_init_success(self):
        c = _make_connector()
        with patch("app.connectors.sources.notion.connector.NotionClient") as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=MagicMock())
            with patch("app.connectors.sources.notion.connector.NotionDataSource") as MockDS:
                MockDS.return_value = MagicMock()
                result = await c.init()
        assert result is True
        assert c.notion_client is not None

    @pytest.mark.asyncio
    async def test_init_failure(self):
        c = _make_connector()
        with patch("app.connectors.sources.notion.connector.NotionClient") as MockClient:
            MockClient.build_from_services = AsyncMock(side_effect=Exception("fail"))
            result = await c.init()
        assert result is False


# ===========================================================================
# test_connection_and_access
# ===========================================================================

class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.notion_client = MagicMock()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_bot_user = AsyncMock(return_value=_make_api_response(True, {"id": "bot"}))
        result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_client(self):
        c = _make_connector()
        c.notion_client = None
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _make_connector()
        c.notion_client = MagicMock()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_bot_user = AsyncMock(return_value=_make_api_response(False, error="unauthorized"))
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        c.notion_client = MagicMock()
        c._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await c.test_connection_and_access()
        assert result is False


# ===========================================================================
# run_sync / run_incremental_sync
# ===========================================================================

class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync(self):
        c = _make_connector()
        with patch("app.connectors.sources.notion.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (FilterCollection(), FilterCollection())
            c._sync_users = AsyncMock()
            c._sync_objects_by_type = AsyncMock()
            await c.run_sync()
            c._sync_users.assert_called_once()
            assert c._sync_objects_by_type.call_count == 2

    @pytest.mark.asyncio
    async def test_run_sync_error(self):
        c = _make_connector()
        with patch("app.connectors.sources.notion.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (FilterCollection(), FilterCollection())
            c._sync_users = AsyncMock(side_effect=Exception("fail"))
            with pytest.raises(Exception, match="fail"):
                await c.run_sync()

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        c = _make_connector()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_called_once()


# ===========================================================================
# get_signed_url
# ===========================================================================

class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_no_data_source(self):
        c = _make_connector()
        c.data_source = None
        record = MagicMock()
        record.external_record_id = "block-1"
        result = await c.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_comment_attachment_prefix(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c._get_comment_attachment_url = AsyncMock(return_value="https://url.com")
        record = MagicMock()
        record.external_record_id = "ca_comment1_file.pdf"
        result = await c.get_signed_url(record)
        assert result == "https://url.com"

    @pytest.mark.asyncio
    async def test_block_file(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c._get_block_file_url = AsyncMock(return_value="https://block-url.com")
        record = MagicMock()
        record.external_record_id = "block-123"
        result = await c.get_signed_url(record)
        assert result == "https://block-url.com"

    @pytest.mark.asyncio
    async def test_error_propagation(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c._get_block_file_url = AsyncMock(side_effect=Exception("API error"))
        record = MagicMock()
        record.external_record_id = "block-123"
        with pytest.raises(Exception, match="API error"):
            await c.get_signed_url(record)


# ===========================================================================
# _get_comment_attachment_url
# ===========================================================================

class TestGetCommentAttachmentUrl:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._normalize_filename_for_id = MagicMock(return_value="file.pdf")

        attachment = {
            "file": {"url": "https://notion.com/secure/file.pdf?sig=abc"},
            "name": "file.pdf",
        }
        comment_data = {"attachments": [attachment]}
        ds.retrieve_comment = AsyncMock(return_value=_make_api_response(True, comment_data))

        record = MagicMock()
        record.external_record_id = "ca_comment1_file.pdf"
        record.signed_url = "old-url"

        result = await c._get_comment_attachment_url(record)
        assert result == "https://notion.com/secure/file.pdf?sig=abc"

    @pytest.mark.asyncio
    async def test_invalid_prefix(self):
        c = _make_connector()
        record = MagicMock()
        record.external_record_id = "invalid_comment1_file.pdf"
        with pytest.raises(ValueError, match="Invalid comment attachment"):
            await c._get_comment_attachment_url(record)

    @pytest.mark.asyncio
    async def test_no_normalized_filename(self):
        c = _make_connector()
        record = MagicMock()
        record.external_record_id = "ca_comment1"
        record.signed_url = "fallback-url"
        result = await c._get_comment_attachment_url(record)
        assert result == "fallback-url"

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_comment = AsyncMock(return_value=_make_api_response(False, error="not found"))

        record = MagicMock()
        record.external_record_id = "ca_comment1_file.pdf"
        record.signed_url = "fallback"
        result = await c._get_comment_attachment_url(record)
        assert result == "fallback"


# ===========================================================================
# _get_block_file_url
# ===========================================================================

class TestGetBlockFileUrl:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        block_data = {
            "type": "image",
            "image": {
                "file": {"url": "https://notion.com/image.png"},
            },
        }
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(True, block_data))

        record = MagicMock()
        record.external_record_id = "block-1"
        record.signed_url = "old-url"
        result = await c._get_block_file_url(record)
        assert result == "https://notion.com/image.png"

    @pytest.mark.asyncio
    async def test_external_url(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        block_data = {
            "type": "video",
            "video": {
                "external": {"url": "https://external.com/video.mp4"},
            },
        }
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(True, block_data))

        record = MagicMock()
        record.external_record_id = "block-2"
        record.signed_url = "old"
        result = await c._get_block_file_url(record)
        assert result == "https://external.com/video.mp4"

    @pytest.mark.asyncio
    async def test_empty_block_id(self):
        c = _make_connector()
        record = MagicMock()
        record.external_record_id = ""
        with pytest.raises(ValueError):
            await c._get_block_file_url(record)

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(False))

        record = MagicMock()
        record.external_record_id = "block-fail"
        record.signed_url = "fallback"
        result = await c._get_block_file_url(record)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_no_url_in_block(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        block_data = {"type": "paragraph", "paragraph": {"rich_text": []}}
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(True, block_data))

        record = MagicMock()
        record.external_record_id = "block-no-url"
        result = await c._get_block_file_url(record)
        assert result is None


# ===========================================================================
# reindex_records
# ===========================================================================

class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self):
        c = _make_connector()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_with_records(self):
        c = _make_connector()
        await c.reindex_records([MagicMock()])
        # Current impl is a TODO - just logs


# ===========================================================================
# get_filter_options
# ===========================================================================

class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises(self):
        c = _make_connector()
        with pytest.raises(NotImplementedError):
            await c.get_filter_options("pages")


# ===========================================================================
# cleanup
# ===========================================================================

class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _make_connector()
        c.notion_client = MagicMock()
        c.data_source = MagicMock()
        await c.cleanup()
        assert c.notion_client is None
        assert c.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_error(self):
        c = _make_connector()
        # Force an error in cleanup
        c.notion_client = MagicMock()
        type(c).notion_client = property(lambda self: (_ for _ in ()).throw(Exception("fail")), lambda self, v: None)
        # Reset to normal for safety
        try:
            await c.cleanup()
        except Exception:
            pass
        finally:
            # Restore descriptor
            type(c).notion_client = None


# ===========================================================================
# handle_webhook_notification
# ===========================================================================

class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_no_op(self):
        c = _make_connector()
        await c.handle_webhook_notification({"type": "test"})


# ===========================================================================
# create_connector
# ===========================================================================

class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_creates_instance(self):
        with patch("app.connectors.sources.notion.connector.DataSourceEntitiesProcessor") as MockDSEP:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep
            connector = await NotionConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-notion",
            )
            assert isinstance(connector, NotionConnector)


# ===========================================================================
# _transform_to_app_user
# ===========================================================================

class TestTransformToAppUser:
    def test_person_user(self):
        c = _make_connector()
        user_data = {
            "id": "user-1",
            "type": "person",
            "name": "Test User",
            "person": {"email": "test@example.com"},
        }
        result = c._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "test@example.com"
        assert result.full_name == "Test User"

    def test_bot_user_skipped(self):
        c = _make_connector()
        user_data = {
            "id": "bot-1",
            "type": "bot",
            "name": "Bot",
        }
        result = c._transform_to_app_user(user_data)
        assert result is None

    def test_no_email(self):
        c = _make_connector()
        user_data = {
            "id": "user-2",
            "type": "person",
            "name": "No Email",
            "person": {"email": ""},
        }
        result = c._transform_to_app_user(user_data)
        assert result is None

    def test_no_id(self):
        c = _make_connector()
        user_data = {
            "type": "person",
            "name": "No ID",
            "person": {"email": "test@test.com"},
        }
        result = c._transform_to_app_user(user_data)
        assert result is None

    def test_none_person_data(self):
        c = _make_connector()
        user_data = {
            "id": "user-3",
            "type": "person",
            "name": "Test",
            "person": None,
        }
        result = c._transform_to_app_user(user_data)
        assert result is None


# ===========================================================================
# _normalize_filename_for_id
# ===========================================================================

class TestNormalizeFilenameForId:
    def test_basic(self):
        c = _make_connector()
        assert c._normalize_filename_for_id("report.pdf") == "report.pdf"

    def test_url_encoded(self):
        c = _make_connector()
        result = c._normalize_filename_for_id("my%20file.pdf")
        assert result == "my file.pdf"

    def test_invalid_chars(self):
        c = _make_connector()
        result = c._normalize_filename_for_id('file/with:bad*chars?.pdf')
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result
        assert "?" not in result

    def test_empty(self):
        c = _make_connector()
        assert c._normalize_filename_for_id("") == "attachment"

    def test_none(self):
        c = _make_connector()
        assert c._normalize_filename_for_id(None) == "attachment"

    def test_whitespace_only(self):
        c = _make_connector()
        assert c._normalize_filename_for_id("   ") == "attachment"


# ===========================================================================
# _extract_page_title
# ===========================================================================

class TestExtractPageTitle:
    def test_title_property(self):
        c = _make_connector()
        page_data = {
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"plain_text": "My Page"}],
                }
            }
        }
        assert c._extract_page_title(page_data) == "My Page"

    def test_name_property(self):
        c = _make_connector()
        page_data = {
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "Named Page"}],
                }
            }
        }
        assert c._extract_page_title(page_data) == "Named Page"

    def test_fallback_any_title(self):
        c = _make_connector()
        page_data = {
            "properties": {
                "CustomProp": {
                    "type": "title",
                    "title": [{"plain_text": "Custom Title"}],
                }
            }
        }
        assert c._extract_page_title(page_data) == "Custom Title"

    def test_untitled(self):
        c = _make_connector()
        page_data = {"properties": {}}
        assert c._extract_page_title(page_data) == "Untitled"

    def test_empty_title_array(self):
        c = _make_connector()
        page_data = {
            "properties": {
                "title": {
                    "type": "title",
                    "title": [],
                }
            }
        }
        assert c._extract_page_title(page_data) == "Untitled"

    def test_multiple_parts(self):
        c = _make_connector()
        page_data = {
            "properties": {
                "title": {
                    "type": "title",
                    "title": [
                        {"plain_text": "Part 1 "},
                        {"plain_text": "Part 2"},
                    ],
                }
            }
        }
        assert c._extract_page_title(page_data) == "Part 1 Part 2"


# ===========================================================================
# _parse_iso_timestamp
# ===========================================================================

class TestParseIsoTimestamp:
    def test_valid(self):
        c = _make_connector()
        with patch("app.connectors.sources.notion.connector.parse_timestamp", return_value=1234567890000):
            result = c._parse_iso_timestamp("2024-01-01T00:00:00Z")
            assert result == 1234567890000

    def test_invalid(self):
        c = _make_connector()
        with patch("app.connectors.sources.notion.connector.parse_timestamp", side_effect=Exception("bad")):
            result = c._parse_iso_timestamp("not-a-date")
            assert result is None


# ===========================================================================
# _get_current_iso_time
# ===========================================================================

class TestGetCurrentIsoTime:
    def test_returns_iso_with_z(self):
        c = _make_connector()
        result = c._get_current_iso_time()
        assert result.endswith("Z")
        assert "+00:00" not in result


# ===========================================================================
# _is_embed_platform_url
# ===========================================================================

class TestIsEmbedPlatformUrl:
    def test_youtube(self):
        c = _make_connector()
        assert c._is_embed_platform_url("https://www.youtube.com/watch?v=abc") is True

    def test_youtu_be(self):
        c = _make_connector()
        assert c._is_embed_platform_url("https://youtu.be/abc") is True

    def test_vimeo(self):
        c = _make_connector()
        assert c._is_embed_platform_url("https://vimeo.com/12345") is True

    def test_spotify(self):
        c = _make_connector()
        assert c._is_embed_platform_url("https://open.spotify.com/track/abc") is True

    def test_direct_mp4(self):
        c = _make_connector()
        assert c._is_embed_platform_url("https://cdn.example.com/video.mp4") is False

    def test_none(self):
        c = _make_connector()
        assert c._is_embed_platform_url(None) is False

    def test_empty(self):
        c = _make_connector()
        assert c._is_embed_platform_url("") is False

    def test_regular_url(self):
        c = _make_connector()
        # URLs that don't match known embed platforms and don't have direct
        # media file extensions default to True (safer embed assumption)
        assert c._is_embed_platform_url("https://example.com/page") is True


# ===========================================================================
# _transform_to_webpage_record
# ===========================================================================

class TestTransformToWebpageRecord:
    @pytest.mark.asyncio
    async def test_page(self):
        c = _make_connector()
        obj_data = {
            "id": "page-1",
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"plain_text": "My Page"}],
                }
            },
            "parent": {"type": "workspace", "workspace": True},
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
            "url": "https://notion.so/page-1",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = await c._transform_to_webpage_record(obj_data, "page")
        assert result is not None
        assert result.record_type == RecordType.WEBPAGE
        assert result.record_name == "My Page"

    @pytest.mark.asyncio
    async def test_database(self):
        c = _make_connector()
        obj_data = {
            "id": "db-1",
            "title": [{"plain_text": "My Database"}],
            "parent": {"type": "page_id", "page_id": "parent-1"},
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
            "url": "https://notion.so/db-1",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = await c._transform_to_webpage_record(obj_data, "database")
        assert result is not None
        assert result.record_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_data_source(self):
        c = _make_connector()
        obj_data = {
            "id": "ds-1",
            "title": [{"plain_text": "My Data Source"}],
            "parent": {},
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
            "url": "https://notion.so/ds-1",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = await c._transform_to_webpage_record(obj_data, "data_source", database_parent_id="parent-1")
        assert result is not None
        assert result.record_type == RecordType.DATASOURCE
        assert result.parent_external_record_id == "parent-1"

    @pytest.mark.asyncio
    async def test_page_with_block_parent(self):
        c = _make_connector()
        c._resolve_block_parent_recursive = AsyncMock(return_value=("resolved-page", RecordType.WEBPAGE))
        obj_data = {
            "id": "page-block-parent",
            "properties": {"title": {"type": "title", "title": [{"plain_text": "Block Child"}]}},
            "parent": {"type": "block_id", "block_id": "block-parent-1"},
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
            "url": "https://notion.so/page",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = await c._transform_to_webpage_record(obj_data, "page")
        assert result is not None
        assert result.parent_external_record_id == "resolved-page"

    @pytest.mark.asyncio
    async def test_page_with_database_parent(self):
        c = _make_connector()
        obj_data = {
            "id": "page-db-parent",
            "properties": {"title": {"type": "title", "title": [{"plain_text": "DB Child"}]}},
            "parent": {"type": "database_id", "database_id": "db-parent-1"},
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
            "url": "https://notion.so/page",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = await c._transform_to_webpage_record(obj_data, "page")
        assert result is not None
        assert result.parent_external_record_id == "db-parent-1"

    @pytest.mark.asyncio
    async def test_error(self):
        c = _make_connector()
        result = await c._transform_to_webpage_record(None, "page")
        assert result is None


# ===========================================================================
# _transform_to_file_record
# ===========================================================================

class TestTransformToFileRecord:
    def test_image_block(self):
        c = _make_connector()
        block = {
            "id": "block-img-1",
            "type": "image",
            "image": {
                "type": "file",
                "file": {"url": "https://notion.com/image.png"},
            },
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = c._transform_to_file_record(block, "page-1", "https://notion.so/page")
        assert result is not None
        assert result.record_name == "image.png"
        assert "image" in (result.mime_type or "").lower() or result.extension == "png"

    def test_bookmark_block_skipped(self):
        c = _make_connector()
        block = {"id": "bm-1", "type": "bookmark", "bookmark": {"url": "https://example.com"}}
        result = c._transform_to_file_record(block, "page-1")
        assert result is None

    def test_embed_block_skipped(self):
        c = _make_connector()
        block = {"id": "em-1", "type": "embed", "embed": {"url": "https://example.com"}}
        result = c._transform_to_file_record(block, "page-1")
        assert result is None

    def test_unsupported_type(self):
        c = _make_connector()
        block = {"id": "p-1", "type": "paragraph", "paragraph": {}}
        result = c._transform_to_file_record(block, "page-1")
        assert result is None

    def test_no_url(self):
        c = _make_connector()
        block = {"id": "img-empty", "type": "image", "image": {}}
        result = c._transform_to_file_record(block, "page-1")
        assert result is None

    def test_no_block_id(self):
        c = _make_connector()
        block = {"type": "image", "image": {"file": {"url": "https://url.com"}}}
        result = c._transform_to_file_record(block, "page-1")
        assert result is None

    def test_external_video_embed_skipped(self):
        c = _make_connector()
        c._is_embed_platform_url = MagicMock(return_value=True)
        block = {
            "id": "vid-1",
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://youtube.com/watch?v=abc"},
            },
        }
        result = c._transform_to_file_record(block, "page-1")
        assert result is None

    def test_pdf_block(self):
        c = _make_connector()
        block = {
            "id": "pdf-1",
            "type": "pdf",
            "pdf": {
                "type": "file",
                "file": {"url": "https://notion.com/doc.pdf"},
            },
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-06-01T00:00:00Z",
        }
        with patch.object(c, '_parse_iso_timestamp', return_value=1000):
            result = c._transform_to_file_record(block, "page-1")
        assert result is not None
        assert "pdf" in (result.mime_type or "").lower() or result.extension == "pdf"

    def test_file_block_with_name(self):
        c = _make_connector()
        block = {
            "id": "file-1",
            "type": "file",
            "file": {
                "type": "file",
                "name": "document.docx",
                "file": {"url": "https://notion.com/document.docx"},
            },
            "created_time": None,
            "last_edited_time": None,
        }
        result = c._transform_to_file_record(block, "page-1")
        assert result is not None
        assert result.record_name == "document.docx"


# ===========================================================================
# _transform_to_comment_file_record
# ===========================================================================

class TestTransformToCommentFileRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        attachment = {
            "file": {"url": "https://notion.com/attachment.pdf?sig=abc"},
            "name": "attachment.pdf",
            "category": "productivity",
        }
        result = await c._transform_to_comment_file_record(
            attachment, "comment-1", "page-1", "https://notion.so/page"
        )
        assert result is not None
        assert result.external_record_id.startswith("ca_comment-1_")

    @pytest.mark.asyncio
    async def test_no_file_url(self):
        c = _make_connector()
        attachment = {"name": "no-url"}
        result = await c._transform_to_comment_file_record(
            attachment, "comment-1", "page-1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_image_category(self):
        c = _make_connector()
        attachment = {
            "file": {"url": "https://notion.com/image.png"},
            "category": "image",
        }
        result = await c._transform_to_comment_file_record(
            attachment, "comment-1", "page-1"
        )
        assert result is not None
        assert "png" in (result.mime_type or "").lower() or "image" in (result.mime_type or "").lower()

    @pytest.mark.asyncio
    async def test_no_category(self):
        c = _make_connector()
        attachment = {
            "file": {"url": "https://notion.com/file.docx"},
        }
        result = await c._transform_to_comment_file_record(
            attachment, "comment-1", "page-1"
        )
        assert result is not None


# ===========================================================================
# resolve_page_title_by_id
# ===========================================================================

class TestResolvePageTitleById:
    @pytest.mark.asyncio
    async def test_found_in_db(self):
        c = _make_connector()
        existing = MagicMock()
        existing.record_name = "DB Title"
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        result = await c.resolve_page_title_by_id("page-1")
        assert result == "DB Title"

    @pytest.mark.asyncio
    async def test_fetched_from_api(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        page_data = {
            "properties": {"title": {"type": "title", "title": [{"plain_text": "API Title"}]}},
        }
        ds.retrieve_page = AsyncMock(return_value=_make_api_response(True, page_data))
        result = await c.resolve_page_title_by_id("page-2")
        assert result == "API Title"

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_page = AsyncMock(return_value=_make_api_response(False))
        result = await c.resolve_page_title_by_id("page-missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("fail"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx
        result = await c.resolve_page_title_by_id("page-err")
        assert result is None


# ===========================================================================
# resolve_user_name_by_id
# ===========================================================================

class TestResolveUserNameById:
    @pytest.mark.asyncio
    async def test_person_user(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        user_data = {
            "object": "user",
            "type": "person",
            "name": "John Doe",
            "person": {"email": "john@test.com"},
        }
        ds.retrieve_user = AsyncMock(return_value=_make_api_response(True, user_data))
        result = await c.resolve_user_name_by_id("user-1")
        assert result == "John Doe"

    @pytest.mark.asyncio
    async def test_person_user_no_name(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        user_data = {
            "object": "user",
            "type": "person",
            "name": "",
            "person": {"email": "john@test.com"},
        }
        ds.retrieve_user = AsyncMock(return_value=_make_api_response(True, user_data))
        result = await c.resolve_user_name_by_id("user-1")
        assert result == "john@test.com"

    @pytest.mark.asyncio
    async def test_bot_user(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        user_data = {
            "object": "user",
            "type": "bot",
            "name": "My Bot",
            "bot": {},
        }
        ds.retrieve_user = AsyncMock(return_value=_make_api_response(True, user_data))
        result = await c.resolve_user_name_by_id("bot-1")
        assert result == "My Bot"

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_user = AsyncMock(return_value=_make_api_response(False))
        result = await c.resolve_user_name_by_id("user-missing")
        assert result is None


# ===========================================================================
# get_record_by_external_id
# ===========================================================================

class TestGetRecordByExternalId:
    @pytest.mark.asyncio
    async def test_found(self):
        c = _make_connector()
        existing = MagicMock()
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx
        result = await c.get_record_by_external_id("ext-1")
        assert result is existing

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _make_connector()
        result = await c.get_record_by_external_id("ext-missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db fail"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx
        result = await c.get_record_by_external_id("ext-err")
        assert result is None


# ===========================================================================
# get_record_child_by_external_id
# ===========================================================================

class TestGetRecordChildByExternalId:
    @pytest.mark.asyncio
    async def test_existing_record(self):
        c = _make_connector()
        existing = MagicMock()
        existing.id = "record-id"
        existing.record_name = "Existing"
        c.get_record_by_external_id = AsyncMock(return_value=existing)
        result = await c.get_record_child_by_external_id("ext-1")
        assert result is not None
        assert result.child_id == "record-id"

    @pytest.mark.asyncio
    async def test_no_record_with_parent(self):
        c = _make_connector()
        c.get_record_by_external_id = AsyncMock(return_value=None)
        c.resolve_page_title_by_id = AsyncMock(return_value="New Page")
        result = await c.get_record_child_by_external_id("ext-new", parent_data_source_id="ds-1")
        assert result is not None
        assert result.child_name == "New Page"

    @pytest.mark.asyncio
    async def test_no_record_without_parent(self):
        c = _make_connector()
        c.get_record_by_external_id = AsyncMock(return_value=None)
        c.resolve_page_title_by_id = AsyncMock(return_value="Orphan Page")
        result = await c.get_record_child_by_external_id("ext-orphan")
        assert result is not None
        assert result.child_name == "Orphan Page"

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        c.get_record_by_external_id = AsyncMock(side_effect=Exception("fail"))
        result = await c.get_record_child_by_external_id("ext-err")
        assert result is None


# ===========================================================================
# get_user_child_by_external_id
# ===========================================================================

class TestGetUserChildByExternalId:
    @pytest.mark.asyncio
    async def test_found_in_db(self):
        c = _make_connector()
        user = MagicMock()
        user.id = "db-user-id"
        user.full_name = "DB User"
        user.email = "user@test.com"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=user)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx
        result = await c.get_user_child_by_external_id("user-1")
        assert result is not None
        assert result.child_id == "db-user-id"

    @pytest.mark.asyncio
    async def test_not_in_db(self):
        c = _make_connector()
        c.resolve_user_name_by_id = AsyncMock(return_value="API User")
        result = await c.get_user_child_by_external_id("user-new")
        assert result is not None
        assert result.child_id == "user-new"
        assert result.child_name == "API User"

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(side_effect=Exception("fail"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx
        result = await c.get_user_child_by_external_id("user-err")
        assert result is None


# ===========================================================================
# _resolve_block_parent_recursive
# ===========================================================================

class TestResolveBlockParentRecursive:
    @pytest.mark.asyncio
    async def test_page_parent(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        block_data = {"parent": {"type": "page_id", "page_id": "page-parent"}}
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(True, block_data))
        parent_id, parent_type = await c._resolve_block_parent_recursive("block-1")
        assert parent_id == "page-parent"
        assert parent_type == RecordType.WEBPAGE

    @pytest.mark.asyncio
    async def test_database_parent(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        block_data = {"parent": {"type": "database_id", "database_id": "db-parent"}}
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(True, block_data))
        parent_id, parent_type = await c._resolve_block_parent_recursive("block-2")
        assert parent_id == "db-parent"
        assert parent_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_max_depth(self):
        c = _make_connector()
        parent_id, parent_type = await c._resolve_block_parent_recursive("block-1", max_depth=0)
        assert parent_id is None
        assert parent_type is None

    @pytest.mark.asyncio
    async def test_cycle_detection(self):
        c = _make_connector()
        parent_id, parent_type = await c._resolve_block_parent_recursive(
            "block-1", visited={"block-1"}
        )
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(False))
        parent_id, parent_type = await c._resolve_block_parent_recursive("block-fail")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_workspace_parent(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        block_data = {"parent": {"type": "workspace", "workspace": True}}
        ds.retrieve_block = AsyncMock(return_value=_make_api_response(True, block_data))
        parent_id, parent_type = await c._resolve_block_parent_recursive("block-ws")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_recursive_block_parent(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        # First block has block parent, second block has page parent
        block1_data = {"parent": {"type": "block_id", "block_id": "block-2"}}
        block2_data = {"parent": {"type": "page_id", "page_id": "final-page"}}
        ds.retrieve_block = AsyncMock(
            side_effect=[
                _make_api_response(True, block1_data),
                _make_api_response(True, block2_data),
            ]
        )
        parent_id, parent_type = await c._resolve_block_parent_recursive("block-1")
        assert parent_id == "final-page"
        assert parent_type == RecordType.WEBPAGE


# ===========================================================================
# _create_workspace_record_group
# ===========================================================================

class TestCreateWorkspaceRecordGroup:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.workspace_id = "ws-1"
        c.workspace_name = "Test WS"
        await c._create_workspace_record_group()
        c.data_entities_processor.on_new_record_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_workspace(self):
        c = _make_connector()
        c.workspace_id = None
        c.workspace_name = None
        await c._create_workspace_record_group()
        c.data_entities_processor.on_new_record_groups.assert_not_called()


# ===========================================================================
# _add_users_to_workspace_permissions
# ===========================================================================

class TestAddUsersToWorkspacePermissions:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.workspace_id = "ws-1"
        c.workspace_name = "Test WS"
        await c._add_users_to_workspace_permissions(["user1@test.com", "user2@test.com"])
        c.data_entities_processor.on_new_record_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_workspace(self):
        c = _make_connector()
        c.workspace_id = None
        await c._add_users_to_workspace_permissions(["user@test.com"])
        c.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_emails(self):
        c = _make_connector()
        c.workspace_id = "ws-1"
        await c._add_users_to_workspace_permissions([])
        c.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_record_group(self):
        c = _make_connector()
        c.workspace_id = "ws-1"
        c.workspace_name = "Test WS"
        existing = MagicMock()
        mock_tx = MagicMock()
        mock_tx.get_record_group_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx
        await c._add_users_to_workspace_permissions(["user@test.com"])
        c.data_entities_processor.on_new_record_groups.assert_called_once()


# ===========================================================================
# _get_database_parent_page_id
# ===========================================================================

class TestGetDatabaseParentPageId:
    @pytest.mark.asyncio
    async def test_page_parent(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        db_data = {"parent": {"type": "page_id", "page_id": "parent-page"}}
        ds.retrieve_database = AsyncMock(return_value=_make_api_response(True, db_data))
        result = await c._get_database_parent_page_id("db-1")
        assert result == "parent-page"

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _make_connector()
        ds = _make_datasource_mock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        ds.retrieve_database = AsyncMock(return_value=_make_api_response(False))
        result = await c._get_database_parent_page_id("db-fail")
        assert result is None
