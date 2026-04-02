"""Comprehensive coverage tests for Notion connector - additional untested methods."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_deps():
    logger = logging.getLogger("test.notion.comp")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-notion-comp"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)

    class FakeTx:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTx())
    config_service = AsyncMock()

    return {
        "logger": logger,
        "data_entities_processor": data_entities_processor,
        "data_store_provider": data_store_provider,
        "config_service": config_service,
        "mock_tx_store": mock_tx_store,
    }


@pytest.fixture()
def connector(mock_deps):
    with patch("app.connectors.sources.notion.connector.NotionApp"):
        c = NotionConnector(
            logger=mock_deps["logger"],
            data_entities_processor=mock_deps["data_entities_processor"],
            data_store_provider=mock_deps["data_store_provider"],
            config_service=mock_deps["config_service"],
            connector_id="notion-comp-1",
        )
    c.workspace_id = "ws-1"
    return c


# ===========================================================================
# _transform_to_app_user
# ===========================================================================
class TestTransformToAppUser:
    def test_person_user_success(self, connector):
        user_data = {
            "id": "user-1",
            "type": "person",
            "name": "Alice Smith",
            "person": {"email": "alice@example.com"},
        }
        result = connector._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "alice@example.com"
        assert result.full_name == "Alice Smith"
        assert result.source_user_id == "user-1"

    def test_bot_user_skipped(self, connector):
        user_data = {"id": "bot-1", "type": "bot", "name": "Bot"}
        result = connector._transform_to_app_user(user_data)
        assert result is None

    def test_no_email_skipped(self, connector):
        user_data = {"id": "user-2", "type": "person", "name": "No Email", "person": {}}
        result = connector._transform_to_app_user(user_data)
        assert result is None

    def test_no_id_skipped(self, connector):
        user_data = {"type": "person", "name": "No ID", "person": {"email": "a@b.com"}}
        result = connector._transform_to_app_user(user_data)
        assert result is None

    def test_none_person_data(self, connector):
        user_data = {"id": "user-3", "type": "person", "name": "Test", "person": None}
        result = connector._transform_to_app_user(user_data)
        assert result is None

    def test_whitespace_email_trimmed(self, connector):
        user_data = {"id": "user-4", "type": "person", "name": "Trim", "person": {"email": "  bob@example.com  "}}
        result = connector._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "bob@example.com"


# ===========================================================================
# _normalize_filename_for_id
# ===========================================================================
class TestNormalizeFilenameForId:
    def test_basic(self, connector):
        assert connector._normalize_filename_for_id("document.pdf") == "document.pdf"

    def test_url_encoded(self, connector):
        result = connector._normalize_filename_for_id("my%20file.pdf")
        assert result == "my file.pdf"

    def test_invalid_chars_replaced(self, connector):
        result = connector._normalize_filename_for_id("file:name/with\\invalid.pdf")
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result

    def test_empty(self, connector):
        assert connector._normalize_filename_for_id("") == "attachment"

    def test_none(self, connector):
        assert connector._normalize_filename_for_id(None) == "attachment"

    def test_whitespace_only(self, connector):
        assert connector._normalize_filename_for_id("   ") == "attachment"

    def test_all_invalid_chars(self, connector):
        result = connector._normalize_filename_for_id('/:*?"<>|')
        # All chars replaced with underscores, resulting in "________"
        assert "_" in result
        assert "/" not in result


# ===========================================================================
# _extract_page_title
# ===========================================================================
class TestExtractPageTitle:
    def test_title_property(self, connector):
        page_data = {
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"plain_text": "My Page"}]
                }
            }
        }
        assert connector._extract_page_title(page_data) == "My Page"

    def test_name_property(self, connector):
        page_data = {
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "Named Page"}]
                }
            }
        }
        assert connector._extract_page_title(page_data) == "Named Page"

    def test_fallback_any_title(self, connector):
        page_data = {
            "properties": {
                "custom_prop": {
                    "type": "title",
                    "title": [{"plain_text": "Custom Title"}]
                }
            }
        }
        assert connector._extract_page_title(page_data) == "Custom Title"

    def test_untitled(self, connector):
        page_data = {"properties": {}}
        assert connector._extract_page_title(page_data) == "Untitled"

    def test_empty_title_array(self, connector):
        page_data = {
            "properties": {
                "title": {"type": "title", "title": []}
            }
        }
        assert connector._extract_page_title(page_data) == "Untitled"

    def test_multiple_parts(self, connector):
        page_data = {
            "properties": {
                "title": {
                    "type": "title",
                    "title": [
                        {"plain_text": "Hello "},
                        {"plain_text": "World"}
                    ]
                }
            }
        }
        assert connector._extract_page_title(page_data) == "Hello World"


# ===========================================================================
# _is_embed_platform_url
# ===========================================================================
class TestIsEmbedPlatformUrl:
    def test_youtube(self, connector):
        assert connector._is_embed_platform_url("https://www.youtube.com/watch?v=abc") is True

    def test_youtu_be(self, connector):
        assert connector._is_embed_platform_url("https://youtu.be/abc") is True

    def test_vimeo(self, connector):
        assert connector._is_embed_platform_url("https://vimeo.com/12345") is True

    def test_spotify(self, connector):
        assert connector._is_embed_platform_url("https://open.spotify.com/track/xyz") is True

    def test_soundcloud(self, connector):
        assert connector._is_embed_platform_url("https://soundcloud.com/artist/track") is True

    def test_direct_mp4(self, connector):
        assert connector._is_embed_platform_url("https://example.com/video.mp4") is False

    def test_direct_mp3(self, connector):
        assert connector._is_embed_platform_url("https://example.com/audio.mp3") is False

    def test_direct_wav(self, connector):
        assert connector._is_embed_platform_url("https://example.com/audio.wav") is False

    def test_mp4_with_query_params(self, connector):
        assert connector._is_embed_platform_url("https://example.com/video.mp4?token=abc") is False

    def test_none_url(self, connector):
        assert connector._is_embed_platform_url(None) is False

    def test_empty_url(self, connector):
        assert connector._is_embed_platform_url("") is False

    def test_unknown_url_defaults_to_embed(self, connector):
        # Unknown URL without file extension defaults to embed (safer)
        assert connector._is_embed_platform_url("https://example.com/stream/123") is True

    def test_twitch(self, connector):
        assert connector._is_embed_platform_url("https://www.twitch.tv/channel") is True


# ===========================================================================
# _parse_iso_timestamp
# ===========================================================================
class TestParseIsoTimestamp:
    def test_valid_timestamp(self, connector):
        result = connector._parse_iso_timestamp("2024-06-15T12:30:00.000Z")
        assert result is not None
        assert result > 0

    def test_invalid_timestamp(self, connector):
        result = connector._parse_iso_timestamp("not-a-date")
        assert result is None


# ===========================================================================
# _get_current_iso_time
# ===========================================================================
class TestGetCurrentIsoTime:
    def test_returns_z_suffix(self, connector):
        result = connector._get_current_iso_time()
        assert result.endswith("Z")
        assert "+00:00" not in result


# ===========================================================================
# _transform_to_file_record
# ===========================================================================
class TestTransformToFileRecord:
    def test_image_block(self, connector):
        block = {
            "id": "block-1",
            "type": "image",
            "image": {
                "type": "file",
                "file": {"url": "https://notion.so/image.png"},
            },
            "created_time": "2024-01-01T00:00:00.000Z",
            "last_edited_time": "2024-01-02T00:00:00.000Z",
        }
        result = connector._transform_to_file_record(block, "page-1", "https://notion.so/page-1")
        assert result is not None
        assert result.record_name == "image.png"
        assert result.extension == "png"

    def test_pdf_block(self, connector):
        block = {
            "id": "block-2",
            "type": "pdf",
            "pdf": {
                "type": "file",
                "file": {"url": "https://notion.so/doc.pdf"},
            },
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is not None
        assert "pdf" in result.mime_type.lower()

    def test_bookmark_skipped(self, connector):
        block = {
            "id": "block-3",
            "type": "bookmark",
            "bookmark": {"url": "https://example.com"},
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is None

    def test_embed_skipped(self, connector):
        block = {
            "id": "block-4",
            "type": "embed",
            "embed": {"url": "https://youtube.com/embed/xyz"},
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is None

    def test_no_block_id(self, connector):
        block = {"type": "image", "image": {"type": "file", "file": {"url": "https://x.com/i.png"}}}
        result = connector._transform_to_file_record(block, "page-1")
        assert result is None

    def test_external_youtube_video_skipped(self, connector):
        block = {
            "id": "block-5",
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://www.youtube.com/watch?v=abc"},
            },
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is None

    def test_external_direct_video_kept(self, connector):
        block = {
            "id": "block-6",
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://example.com/video.mp4"},
            },
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is not None

    def test_unsupported_block_type(self, connector):
        block = {"id": "block-7", "type": "paragraph", "paragraph": {}}
        result = connector._transform_to_file_record(block, "page-1")
        assert result is None

    def test_no_file_url(self, connector):
        block = {
            "id": "block-8",
            "type": "image",
            "image": {"type": "file", "file": {"url": ""}},
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is None

    def test_external_image(self, connector):
        block = {
            "id": "block-9",
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": "https://example.com/photo.jpg"},
            },
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is not None
        assert result.extension == "jpg"

    def test_pdf_default_name(self, connector):
        """PDF block with no name gets default name document.pdf."""
        block = {
            "id": "block-10",
            "type": "pdf",
            "pdf": {
                "type": "file",
                "file": {"url": "https://notion.so/file?download"},
                "name": "",
            },
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is not None
        assert result.record_name == "document.pdf"

    def test_named_file(self, connector):
        block = {
            "id": "block-11",
            "type": "file",
            "file": {
                "type": "file",
                "file": {"url": "https://notion.so/upload/abc"},
                "name": "report.xlsx",
            },
        }
        result = connector._transform_to_file_record(block, "page-1")
        assert result is not None
        assert result.record_name == "report.xlsx"
        assert result.extension == "xlsx"


# ===========================================================================
# _resolve_block_parent_recursive
# ===========================================================================
class TestResolveBlockParentRecursive:
    async def test_page_parent(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {"parent": {"type": "page_id", "page_id": "parent-page"}}
        mock_response.data = mock_data
        mock_ds.retrieve_block = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        parent_id, parent_type = await connector._resolve_block_parent_recursive("block-1")
        assert parent_id == "parent-page"
        assert parent_type == RecordType.WEBPAGE

    async def test_database_parent(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {"parent": {"type": "database_id", "database_id": "db-1"}}
        mock_response.data = mock_data
        mock_ds.retrieve_block = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        parent_id, parent_type = await connector._resolve_block_parent_recursive("block-2")
        assert parent_id == "db-1"
        assert parent_type == RecordType.DATABASE

    async def test_max_depth_reached(self, connector):
        parent_id, parent_type = await connector._resolve_block_parent_recursive("block-3", max_depth=0)
        assert parent_id is None
        assert parent_type is None

    async def test_cycle_detection(self, connector):
        parent_id, parent_type = await connector._resolve_block_parent_recursive(
            "block-4", visited={"block-4"}
        )
        assert parent_id is None
        assert parent_type is None

    async def test_api_failure(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Not found"
        mock_ds.retrieve_block = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        parent_id, parent_type = await connector._resolve_block_parent_recursive("block-5")
        assert parent_id is None

    async def test_workspace_parent(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {"parent": {"type": "workspace"}}
        mock_response.data = mock_data
        mock_ds.retrieve_block = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        parent_id, parent_type = await connector._resolve_block_parent_recursive("block-6")
        assert parent_id is None

    async def test_exception_returns_none(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("Error"))
        parent_id, parent_type = await connector._resolve_block_parent_recursive("block-7")
        assert parent_id is None


# ===========================================================================
# _get_database_parent_page_id
# ===========================================================================
class TestGetDatabaseParentPageId:
    async def test_page_parent(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {"parent": {"type": "page_id", "page_id": "parent-page"}}
        mock_response.data = mock_data
        mock_ds.retrieve_database = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_database_parent_page_id("db-1")
        assert result == "parent-page"

    async def test_workspace_parent_returns_none(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {"parent": {"type": "workspace"}}
        mock_response.data = mock_data
        mock_ds.retrieve_database = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_database_parent_page_id("db-2")
        assert result is None

    async def test_api_failure_returns_none(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Not found"
        mock_ds.retrieve_database = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_database_parent_page_id("db-3")
        assert result is None

    async def test_exception_returns_none(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("Error"))
        result = await connector._get_database_parent_page_id("db-4")
        assert result is None

    async def test_database_parent(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {"parent": {"type": "database_id", "database_id": "parent-db"}}
        mock_response.data = mock_data
        mock_ds.retrieve_database = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_database_parent_page_id("db-5")
        assert result == "parent-db"


# ===========================================================================
# resolve_page_title_by_id
# ===========================================================================
class TestResolvePageTitleById:
    async def test_from_db(self, connector):
        mock_record = MagicMock()
        mock_record.record_name = "Cached Title"

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=mock_record)

        class FakeTx:
            async def __aenter__(self):
                return mock_tx
            async def __aexit__(self, *args):
                pass

        connector.data_store_provider.transaction = MagicMock(return_value=FakeTx())
        result = await connector.resolve_page_title_by_id("page-1")
        assert result == "Cached Title"

    async def test_from_api(self, connector):
        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        class FakeTx:
            async def __aenter__(self):
                return mock_tx
            async def __aexit__(self, *args):
                pass

        connector.data_store_provider.transaction = MagicMock(return_value=FakeTx())

        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {
            "properties": {"title": {"type": "title", "title": [{"plain_text": "API Title"}]}}
        }
        mock_response.data = mock_data
        mock_ds.retrieve_page = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.resolve_page_title_by_id("page-2")
        assert result == "API Title"

    async def test_exception_returns_none(self, connector):
        connector.data_store_provider.transaction = MagicMock(
            side_effect=Exception("DB error")
        )
        result = await connector.resolve_page_title_by_id("page-3")
        assert result is None


# ===========================================================================
# resolve_user_name_by_id
# ===========================================================================
class TestResolveUserNameById:
    async def test_person_user(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_data = MagicMock()
        mock_data.json.return_value = {
            "object": "user",
            "type": "person",
            "name": "Alice Smith",
            "person": {"email": "alice@example.com"},
        }
        mock_response.data = mock_data
        mock_ds.retrieve_user = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.resolve_user_name_by_id("user-1")
        assert result == "Alice Smith"

    async def test_api_failure(self, connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_ds.retrieve_user = AsyncMock(return_value=mock_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.resolve_user_name_by_id("user-2")
        assert result is None

    async def test_exception_returns_none(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("Error"))
        result = await connector.resolve_user_name_by_id("user-3")
        assert result is None
