"""Comprehensive tests for the Notion connector."""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import aiohttp
import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.sources.notion.connector import NotionConnector
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


def _make_connector():
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
        scope="personal",
        created_by="test-user-id",
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
        conn = _make_connector()
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
        conn = _make_connector()
        with patch(
            "app.connectors.sources.notion.connector.NotionClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth error"),
        ):
            result = await conn.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_no_client(self):
        conn = _make_connector()
        conn.notion_client = None
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        conn = _make_connector()
        conn.notion_client = MagicMock()
        ds = MagicMock()
        ds.retrieve_bot_user = AsyncMock(return_value=_api_resp(True, {"object": "user"}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        conn = _make_connector()
        conn.notion_client = MagicMock()
        ds = MagicMock()
        ds.retrieve_bot_user = AsyncMock(return_value=_api_resp(False, error="unauthorized"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        conn = _make_connector()
        conn.notion_client = MagicMock()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("network"))
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup(self):
        conn = _make_connector()
        conn.notion_client = MagicMock()
        conn.data_source = MagicMock()
        await conn.cleanup()
        assert conn.notion_client is None
        assert conn.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_exception_logged(self):
        conn = _make_connector()
        conn.logger = MagicMock()
        del conn.notion_client
        del conn.data_source
        await conn.cleanup()

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        conn = _make_connector()
        await conn.handle_webhook_notification({"type": "update"})
        conn.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates(self):
        conn = _make_connector()
        conn.run_sync = AsyncMock()
        await conn.run_incremental_sync()
        conn.run_sync.assert_awaited_once()


# ===================================================================
# get_signed_url
# ===================================================================

class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_no_datasource_returns_none(self):
        conn = _make_connector()
        conn.data_source = None
        record = _make_file_record(external_record_id="block-1")
        result = await conn.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_comment_attachment_prefix_ca(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn._get_comment_attachment_url = AsyncMock(return_value="https://signed.url/file")
        record = _make_file_record(external_record_id="ca_comment1_file.pdf")
        result = await conn.get_signed_url(record)
        assert result == "https://signed.url/file"

    @pytest.mark.asyncio
    async def test_comment_attachment_prefix_full(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn._get_comment_attachment_url = AsyncMock(return_value="https://x.url")
        record = _make_file_record(external_record_id="comment_attachment_xyz")
        result = await conn.get_signed_url(record)
        assert result == "https://x.url"

    @pytest.mark.asyncio
    async def test_block_file_url(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn._get_block_file_url = AsyncMock(return_value="https://block.url/f")
        record = _make_file_record(external_record_id="block-abc")
        result = await conn.get_signed_url(record)
        assert result == "https://block.url/f"

    @pytest.mark.asyncio
    async def test_get_signed_url_exception_raised(self):
        conn = _make_connector()
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
        conn = _make_connector()
        record = _make_file_record(external_record_id="invalid_format")
        with pytest.raises(ValueError, match="Invalid comment attachment"):
            await conn._get_comment_attachment_url(record)

    @pytest.mark.asyncio
    async def test_no_comment_id_raises(self):
        conn = _make_connector()
        record = _make_file_record(external_record_id="ca_")
        with pytest.raises(ValueError, match="Failed to extract comment_id"):
            await conn._get_comment_attachment_url(record)

    @pytest.mark.asyncio
    async def test_no_normalized_filename_returns_signed_url(self):
        conn = _make_connector()
        record = _make_file_record(external_record_id="ca_commentid123", signed_url="https://old.url")
        result = await conn._get_comment_attachment_url(record)
        assert result == "https://old.url"

    @pytest.mark.asyncio
    async def test_api_failure_returns_signed_url(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        record = _make_file_record(external_record_id="")
        with pytest.raises(ValueError):
            await conn._get_block_file_url(record)

    @pytest.mark.asyncio
    async def test_api_failure_returns_signed_url(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        conn.data_source = None
        record = _make_webpage_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_file_record_no_signed_url_raises_404(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.get_signed_url = AsyncMock(return_value=None)
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_file_record_returns_streaming_response(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.get_signed_url = AsyncMock(return_value="https://signed.url/file.pdf")
        record = _make_file_record(mime_type="application/pdf")
        response = await conn.stream_record(record)
        assert response.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_file_record_no_mimetype_defaults(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.get_signed_url = AsyncMock(return_value="https://signed.url/file")
        record = _make_file_record(mime_type="")
        response = await conn.stream_record(record)
        assert response.media_type == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_datasource_record_streams_blocks(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = _make_webpage_record(record_type=RecordType.DATABASE)
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_generic_exception_wraps_in_500(self):
        conn = _make_connector()
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
        conn = _make_connector()
        await conn.reindex_records([])
        conn.logger.info.assert_any_call("No records to reindex")

    @pytest.mark.asyncio
    async def test_non_empty_list(self):
        conn = _make_connector()
        record = _make_webpage_record()
        await conn.reindex_records([record])
        conn.logger.info.assert_any_call("Starting reindex for 1 Notion records")

    @pytest.mark.asyncio
    async def test_exception_is_raised(self):
        conn = _make_connector()
        conn.logger.info = MagicMock(side_effect=Exception("boom"))
        with pytest.raises(Exception):
            await conn.reindex_records([_make_webpage_record()])


# ===================================================================
# get_filter_options
# ===================================================================

class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        conn = _make_connector()
        with pytest.raises(NotImplementedError):
            await conn.get_filter_options("some_key")


# ===================================================================
# _sync_users
# ===================================================================

class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_sync_users_no_users(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(True, {"results": [], "has_more": False}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_api_failure_raises(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.list_users = AsyncMock(return_value=_api_resp(False, error="rate limit"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(Exception, match="Notion API error"):
            await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_api_none_response_raises(self):
        conn = _make_connector()
        conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(
            list_users=AsyncMock(return_value=None)
        ))
        with pytest.raises(Exception, match="Notion API error"):
            await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_with_person_and_bot(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        conn.workspace_id = None
        await conn._add_users_to_workspace_permissions(["a@b.com"])
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_emails_returns(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        await conn._add_users_to_workspace_permissions([])
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_record_group_if_not_exists(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "My Workspace"
        existing_rg = MagicMock()
        conn._mock_tx.get_record_group_by_external_id = AsyncMock(return_value=existing_rg)
        await conn._add_users_to_workspace_permissions(["bob@ex.com"])
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_raised(self):
        conn = _make_connector()
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
        conn = _make_connector()
        data = {
            "id": "u1", "type": "person", "name": "Alice",
            "person": {"email": "alice@ex.com"},
        }
        result = conn._transform_to_app_user(data)
        assert result is not None
        assert result.email == "alice@ex.com"

    def test_non_person_returns_none(self):
        conn = _make_connector()
        result = conn._transform_to_app_user({"id": "b1", "type": "bot"})
        assert result is None

    def test_no_id_returns_none(self):
        conn = _make_connector()
        result = conn._transform_to_app_user({"type": "person", "person": {"email": "a@b.c"}})
        assert result is None

    def test_no_email_returns_none(self):
        conn = _make_connector()
        result = conn._transform_to_app_user({"id": "u1", "type": "person", "person": {}})
        assert result is None

    def test_empty_email_returns_none(self):
        conn = _make_connector()
        result = conn._transform_to_app_user({"id": "u1", "type": "person", "person": {"email": "  "}})
        assert result is None

    def test_none_person_data(self):
        conn = _make_connector()
        result = conn._transform_to_app_user({"id": "u1", "type": "person", "person": None})
        assert result is None


# ===================================================================
# _extract_page_title
# ===================================================================

class TestExtractPageTitle:
    def test_standard_title(self):
        conn = _make_connector()
        data = {"properties": {"title": {"type": "title", "title": [{"plain_text": "My Page"}]}}}
        assert conn._extract_page_title(data) == "My Page"

    def test_name_property(self):
        conn = _make_connector()
        data = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Named"}]}}}
        assert conn._extract_page_title(data) == "Named"

    def test_fallback_title_type(self):
        conn = _make_connector()
        data = {"properties": {"custom_prop": {"type": "title", "title": [{"plain_text": "Custom"}]}}}
        assert conn._extract_page_title(data) == "Custom"

    def test_no_title_returns_untitled(self):
        conn = _make_connector()
        data = {"properties": {}}
        assert conn._extract_page_title(data) == "Untitled"

    def test_empty_title_array_returns_untitled(self):
        conn = _make_connector()
        data = {"properties": {"title": {"type": "title", "title": []}}}
        assert conn._extract_page_title(data) == "Untitled"


# ===================================================================
# _normalize_filename_for_id
# ===================================================================

class TestNormalizeFilenameForId:
    def test_basic(self):
        conn = _make_connector()
        assert conn._normalize_filename_for_id("report.pdf") == "report.pdf"

    def test_empty_returns_attachment(self):
        conn = _make_connector()
        assert conn._normalize_filename_for_id("") == "attachment"

    def test_none_returns_attachment(self):
        conn = _make_connector()
        assert conn._normalize_filename_for_id(None) == "attachment"

    def test_url_encoded(self):
        conn = _make_connector()
        assert conn._normalize_filename_for_id("my%20file.pdf") == "my file.pdf"

    def test_invalid_chars_replaced(self):
        conn = _make_connector()
        result = conn._normalize_filename_for_id("file/with:special*chars")
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result

    def test_whitespace_only_returns_attachment(self):
        conn = _make_connector()
        assert conn._normalize_filename_for_id("   ") == "attachment"


# ===================================================================
# _is_embed_platform_url
# ===================================================================

class TestIsEmbedPlatformUrl:
    def test_none_url(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url(None) is False

    def test_empty_url(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("") is False

    def test_youtube(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("https://youtube.com/watch?v=abc") is True

    def test_vimeo(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("https://vimeo.com/123") is True

    def test_direct_mp4(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("https://cdn.com/video.mp4") is False

    def test_direct_mp4_with_query(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("https://cdn.com/video.mp4?token=x") is False

    def test_direct_mp3(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("https://cdn.com/audio.mp3") is False

    def test_unknown_url_defaults_to_embed(self):
        conn = _make_connector()
        assert conn._is_embed_platform_url("https://someplatform.com/player") is True


# ===================================================================
# _parse_iso_timestamp / _get_current_iso_time
# ===================================================================

class TestTimestampUtils:
    def test_parse_iso_timestamp_valid(self):
        conn = _make_connector()
        with patch("app.connectors.sources.notion.connector.parse_timestamp", return_value=1700000000000):
            result = conn._parse_iso_timestamp("2023-11-14T22:13:20.000Z")
        assert result == 1700000000000

    def test_parse_iso_timestamp_invalid(self):
        conn = _make_connector()
        with patch("app.connectors.sources.notion.connector.parse_timestamp", side_effect=ValueError("bad")):
            result = conn._parse_iso_timestamp("not-a-timestamp")
        assert result is None

    def test_get_current_iso_time(self):
        conn = _make_connector()
        result = conn._get_current_iso_time()
        assert result.endswith("Z") or result.endswith("+00:00") or "T" in result


# ===================================================================
# _get_fresh_datasource
# ===================================================================

class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        conn = _make_connector()
        conn.notion_client = None
        with pytest.raises(Exception, match="not initialized"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        conn = _make_connector()
        conn.notion_client = MagicMock()
        conn.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_access_token_raises(self):
        conn = _make_connector()
        conn.notion_client = MagicMock()
        conn.config_service.get_config = AsyncMock(return_value={"credentials": {}})
        with pytest.raises(Exception, match="No OAuth"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_token_unchanged(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[])
        parser = MagicMock()
        blocks = []
        groups = []
        result = await conn._process_blocks_recursive("page-1", parser, blocks, groups, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_archived_blocks(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        blocks = [
            Block(id="b1", index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="text"),
        ]
        await conn._convert_image_blocks_to_base64(blocks)

    @pytest.mark.asyncio
    async def test_image_without_public_data_link_skipped(self):
        conn = _make_connector()
        blocks = [
            Block(id="b1", index=0, type=BlockType.IMAGE, format=DataFormat.TXT, data="img", public_data_link=None),
        ]
        await conn._convert_image_blocks_to_base64(blocks)
        assert blocks[0].format == DataFormat.TXT

    @pytest.mark.asyncio
    @patch("app.connectors.sources.notion.connector.ImageParser")
    @patch("app.connectors.sources.notion.connector.aiohttp.ClientSession")
    async def test_image_conversion_success(self, mock_session_cls, mock_parser_cls):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()

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
        conn = _make_connector()

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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        result = await conn._batch_get_or_create_child_records({})
        assert result == {}

    @pytest.mark.asyncio
    async def test_existing_record_found(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        conn.workspace_id = "ws-1"

        result = await conn._batch_get_or_create_child_records({
            "ext-1": ("New Page", RecordType.WEBPAGE, "parent-1"),
        })
        assert "ext-1" in result
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_record_created_as_file(self):
        conn = _make_connector()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        result = await conn._batch_get_or_create_child_records({
            "ext-f": ("doc.pdf", RecordType.FILE, "parent-1"),
        })
        assert "ext-f" in result
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_record_created_as_datasource(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        blocks = [
            Block(id="b1", index=0, type=BlockType.TEXT, sub_type=BlockSubType.PARAGRAPH, format=DataFormat.MARKDOWN, data="text"),
        ]
        await conn._resolve_child_reference_blocks(blocks)

    @pytest.mark.asyncio
    async def test_resolves_non_database_reference(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        blocks = [Block(id="b1", index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="x")]
        await conn._resolve_table_row_children(blocks)

    @pytest.mark.asyncio
    async def test_table_rows_without_source_id(self):
        conn = _make_connector()
        blocks = [Block(id="b1", index=0, type=BlockType.TABLE_ROW, format=DataFormat.MARKDOWN, data="x")]
        await conn._resolve_table_row_children(blocks)

    @pytest.mark.asyncio
    async def test_resolves_child_pages(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(False, error="err"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_dict_response(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, "not a dict"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_block_children_recursive("page-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_pagination(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        result = await conn._fetch_comments_for_block("")
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_block_id(self):
        conn = _make_connector()
        result = await conn._fetch_comments_for_block("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(side_effect=Exception("network"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_dict_data_returns_empty(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_comments = AsyncMock(return_value=_api_resp(True, "not a dict"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._fetch_comments_for_block("block-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_parse_error_on_failure_response(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn._fetch_comments_for_block = AsyncMock(side_effect=[
            [{"id": "pc1"}],
            [{"id": "bc1"}],
        ])
        result = await conn._fetch_comments_for_blocks("page-1", ["block-1"])
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_block_comment_fetch_exception(self):
        conn = _make_connector()
        conn._fetch_comments_for_block = AsyncMock(side_effect=[
            [],
            Exception("block error"),
        ])
        result = await conn._fetch_comments_for_blocks("page-1", ["block-1"])
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_overall_exception_returns_partial(self):
        conn = _make_connector()
        conn._fetch_comments_for_block = AsyncMock(side_effect=Exception("total failure"))
        result = await conn._fetch_comments_for_blocks("page-1", [])
        assert result == []


# ===================================================================
# _fetch_attachment_blocks_and_block_ids_recursive
# ===================================================================

class TestFetchAttachmentBlocksAndBlockIds:
    @pytest.mark.asyncio
    async def test_file_blocks_collected(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(False, error="fail"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(Exception, match="Notion API error"):
            await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")

    @pytest.mark.asyncio
    async def test_recurses_into_children(self):
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block_children = AsyncMock(return_value=_api_resp(True, "not a dict"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        attachments, block_ids = await conn._fetch_attachment_blocks_and_block_ids_recursive("page-1")
        assert attachments == []
        assert block_ids == []

    @pytest.mark.asyncio
    async def test_image_with_children_recurses(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        result = conn._transform_to_file_record({}, "page-1")
        assert result is None

    def test_bookmark_block_returns_none(self):
        conn = _make_connector()
        result = conn._transform_to_file_record({"id": "b1", "type": "bookmark"}, "page-1")
        assert result is None

    def test_embed_block_returns_none(self):
        conn = _make_connector()
        result = conn._transform_to_file_record({"id": "b1", "type": "embed"}, "page-1")
        assert result is None

    def test_external_video_embed_returns_none(self):
        conn = _make_connector()
        block = {
            "id": "b1",
            "type": "video",
            "video": {"type": "external", "external": {"url": "https://youtube.com/watch?v=abc"}},
        }
        result = conn._transform_to_file_record(block, "page-1")
        assert result is None

    def test_external_audio_direct_url_returns_record(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        block = {
            "id": "b1",
            "type": "audio",
            "audio": {"type": "external", "external": {"url": "https://cdn.com/song.mp3"}},
        }
        result = conn._transform_to_file_record(block, "page-1")
        assert result is not None

    def test_unsupported_type_returns_none(self):
        conn = _make_connector()
        block = {"id": "b1", "type": "paragraph", "paragraph": {}}
        result = conn._transform_to_file_record(block, "page-1")
        assert result is None

    def test_no_file_url_returns_none(self):
        conn = _make_connector()
        block = {"id": "b1", "type": "file", "file": {}}
        result = conn._transform_to_file_record(block, "page-1")
        assert result is None

    def test_pdf_default_name(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        block = {
            "id": "b1", "type": "pdf",
            "pdf": {"file": {"url": "https://s3.aws/"}},
        }
        result = conn._transform_to_file_record(block, "page-1")
        assert result is not None
        assert result.record_name == "document.pdf"

    def test_external_image_record(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        result = await conn._transform_to_comment_file_record({"name": "x"}, "c1", "p1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_file_url_returns_none(self):
        conn = _make_connector()
        result = await conn._transform_to_comment_file_record({"file": {"url": ""}}, "c1", "p1")
        assert result is None

    @pytest.mark.asyncio
    async def test_image_category_mime(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        attachment = {
            "file": {"url": "https://s3.aws/img.png"},
            "category": "image",
        }
        result = await conn._transform_to_comment_file_record(attachment, "c1", "p1")
        assert result.mime_type == MimeTypes.PNG.value

    @pytest.mark.asyncio
    async def test_no_category_guesses_mime(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        attachment = {
            "file": {"url": "https://s3.aws/files/presentation.pptx"},
        }
        result = await conn._transform_to_comment_file_record(attachment, "c1", "p1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_file_not_dict_returns_none(self):
        conn = _make_connector()
        result = await conn._transform_to_comment_file_record({"file": "not-a-dict"}, "c1", "p1")
        assert result is None


# ===================================================================
# _extract_comment_attachment_file_records
# ===================================================================

class TestExtractCommentAttachmentFileRecords:
    @pytest.mark.asyncio
    async def test_extracts_file_records(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        comments = {
            "block-1": [({}, "block-1")],
        }
        result = await conn._extract_comment_attachment_file_records(comments, "page-1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_transform_failure_continues(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn._fetch_attachment_blocks_and_block_ids_recursive = AsyncMock(return_value=([], ["b1"]))
        conn._transform_to_file_record = MagicMock(return_value=None)
        conn._fetch_comments_for_blocks = AsyncMock(return_value=[])

        files, comments = await conn._fetch_page_attachments_and_comments("page-1", "https://notion.so/page")
        assert files == []
        assert comments == {}

    @pytest.mark.asyncio
    async def test_with_attachments_and_comments(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        data = {
            "id": "p3",
            "parent": {"type": "database_id", "database_id": "db-parent"},
            "properties": {},
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result.parent_record_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_page_with_block_parent(self):
        conn = _make_connector()
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
        conn = _make_connector()
        data = {
            "id": "p5",
            "parent": {"type": "data_source_id", "data_source_id": "ds-parent"},
            "properties": {},
        }
        result = await conn._transform_to_webpage_record(data, "page")
        assert result.parent_record_type == RecordType.DATASOURCE

    @pytest.mark.asyncio
    async def test_database_type(self):
        conn = _make_connector()
        data = {
            "id": "db-1",
            "title": [{"plain_text": "My DB"}],
            "parent": {"type": "workspace"},
        }
        result = await conn._transform_to_webpage_record(data, "database")
        assert result.record_type == RecordType.DATABASE

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        conn._extract_page_title = MagicMock(side_effect=Exception("parse error"))
        result = await conn._transform_to_webpage_record({"id": "p1", "parent": {}}, "page")
        assert result is None

    @pytest.mark.asyncio
    async def test_block_parent_resolution_failure(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "data_source_id", "data_source_id": "ds-parent"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_type == RecordType.DATASOURCE

    @pytest.mark.asyncio
    async def test_recursive_block_parent(self):
        conn = _make_connector()
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
        conn = _make_connector()
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1", max_depth=0)
        assert parent_id is None
        assert parent_type is None

    @pytest.mark.asyncio
    async def test_cycle_detection(self):
        conn = _make_connector()
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1", visited={"block-1"})
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_workspace_parent(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "workspace"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        parent_id, parent_type = await conn._resolve_block_parent_recursive("block-1")
        assert parent_id is None

    @pytest.mark.asyncio
    async def test_block_parent_with_no_next_block_id(self):
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "page_id", "page_id": "page-p"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "page-p"

    @pytest.mark.asyncio
    async def test_database_parent(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "database_id", "database_id": "db-p"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "db-p"

    @pytest.mark.asyncio
    async def test_block_parent(self):
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "data_source_id", "data_source_id": "ds-p"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result == "ds-p"

    @pytest.mark.asyncio
    async def test_workspace_parent(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(True, {
            "parent": {"type": "workspace"}
        }))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        conn = _make_connector()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn._get_database_parent_page_id("db-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_block_parent_no_block_id(self):
        conn = _make_connector()
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
        conn = _make_connector()
        record = MagicMock()
        record.record_name = "DB Page"
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        result = await conn.resolve_page_title_by_id("page-1")
        assert result == "DB Page"

    @pytest.mark.asyncio
    async def test_resolve_page_title_from_api(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        ds = MagicMock()
        ds.retrieve_page = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_page_title_by_id("page-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_page_title_exception(self):
        conn = _make_connector()
        conn._mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))
        result = await conn.resolve_page_title_by_id("page-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_name_person(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_user = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn.resolve_user_name_by_id("u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_name_exception(self):
        conn = _make_connector()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn.resolve_user_name_by_id("u1")
        assert result is None


# ===================================================================
# get_record_by_external_id / get_record_child_by_external_id / get_user_child_by_external_id
# ===================================================================

class TestRecordAndUserLookups:
    @pytest.mark.asyncio
    async def test_get_record_by_external_id_found(self):
        conn = _make_connector()
        record = MagicMock()
        conn._mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        result = await conn.get_record_by_external_id("ext-1")
        assert result is record

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_not_found(self):
        conn = _make_connector()
        result = await conn.get_record_by_external_id("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_exception(self):
        conn = _make_connector()
        conn._mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))
        result = await conn.get_record_by_external_id("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_record_child_existing(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Page"
        conn.get_record_by_external_id = AsyncMock(return_value=record)
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result.child_id == "rec-1"

    @pytest.mark.asyncio
    async def test_get_record_child_not_exists_with_parent(self):
        conn = _make_connector()
        conn.get_record_by_external_id = AsyncMock(return_value=None)
        conn.resolve_page_title_by_id = AsyncMock(return_value="Resolved Title")
        result = await conn.get_record_child_by_external_id("ext-1", parent_data_source_id="ds-1")
        assert result is not None
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_record_child_not_exists_no_parent(self):
        conn = _make_connector()
        conn.get_record_by_external_id = AsyncMock(return_value=None)
        conn.resolve_page_title_by_id = AsyncMock(return_value="Resolved")
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result is not None
        assert result.child_name == "Resolved"

    @pytest.mark.asyncio
    async def test_get_record_child_not_exists_no_title(self):
        conn = _make_connector()
        conn.get_record_by_external_id = AsyncMock(return_value=None)
        conn.resolve_page_title_by_id = AsyncMock(return_value=None)
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result is not None
        assert "ext-1" in result.child_name

    @pytest.mark.asyncio
    async def test_get_record_child_exception(self):
        conn = _make_connector()
        conn.get_record_by_external_id = AsyncMock(side_effect=Exception("fail"))
        result = await conn.get_record_child_by_external_id("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_child_from_db(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn._mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        conn.resolve_user_name_by_id = AsyncMock(return_value="Bob")
        result = await conn.get_user_child_by_external_id("u1")
        assert result.child_id == "u1"
        assert result.child_name == "Bob"

    @pytest.mark.asyncio
    async def test_get_user_child_not_in_db_no_name(self):
        conn = _make_connector()
        conn._mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        conn.resolve_user_name_by_id = AsyncMock(return_value=None)
        result = await conn.get_user_child_by_external_id("u1")
        assert "u1" in result.child_name

    @pytest.mark.asyncio
    async def test_get_user_child_exception(self):
        conn = _make_connector()
        conn._mock_tx.get_user_by_source_id = AsyncMock(side_effect=Exception("db"))
        result = await conn.get_user_child_by_external_id("u1")
        assert result is None


# ===================================================================
# _create_workspace_record_group
# ===================================================================

class TestCreateWorkspaceRecordGroup:
    @pytest.mark.asyncio
    async def test_no_workspace_id(self):
        conn = _make_connector()
        conn.workspace_id = None
        conn.workspace_name = "WS"
        await conn._create_workspace_record_group()
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_workspace_name(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        conn.workspace_name = None
        await conn._create_workspace_record_group()
        conn.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_record_group(self):
        conn = _make_connector()
        conn.workspace_id = "ws-1"
        conn.workspace_name = "My Workspace"
        await conn._create_workspace_record_group()
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_raised(self):
        conn = _make_connector()
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
        conn = _make_connector()
        child = ChildRecord(child_type=ChildType.USER, child_id="u1", child_name="Alice")
        conn.get_user_child_by_external_id = AsyncMock(return_value=child)
        result = await conn._resolve_author_name({"created_by": {"id": "u1"}})
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_no_author_id(self):
        conn = _make_connector()
        result = await conn._resolve_author_name({"created_by": {}})
        assert result is None

    @pytest.mark.asyncio
    async def test_no_created_by(self):
        conn = _make_connector()
        result = await conn._resolve_author_name({})
        assert result is None

    @pytest.mark.asyncio
    async def test_user_lookup_returns_none(self):
        conn = _make_connector()
        conn.get_user_child_by_external_id = AsyncMock(return_value=None)
        result = await conn._resolve_author_name({"created_by": {"id": "u1"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        conn.get_user_child_by_external_id = AsyncMock(side_effect=Exception("fail"))
        result = await conn._resolve_author_name({"created_by": {"id": "u1"}})
        assert result is None


# ===================================================================
# _process_comment_attachments
# ===================================================================

class TestProcessCommentAttachments:
    @pytest.mark.asyncio
    async def test_basic_attachment(self):
        conn = _make_connector()
        fr = _make_file_record(record_name="doc.pdf")
        conn._transform_to_comment_file_record = AsyncMock(return_value=fr)
        conn._normalize_filename_for_id = MagicMock(return_value="doc.pdf")

        comment = {"attachments": [{"file": {"url": "https://s3/doc.pdf"}}]}
        file_records, attachments = await conn._process_comment_attachments(comment, "c1", "p1", "url")
        assert len(file_records) == 1
        assert len(attachments) == 1

    @pytest.mark.asyncio
    async def test_no_file_url_skipped(self):
        conn = _make_connector()
        comment = {"attachments": [{"file": {}}]}
        file_records, attachments = await conn._process_comment_attachments(comment, "c1", "p1", None)
        assert len(file_records) == 0

    @pytest.mark.asyncio
    async def test_duplicate_filenames_skipped(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        result, files = await conn._create_block_comment_from_notion_comment({}, "p1", MagicMock())
        assert result is None
        assert files == []

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()
        result = await conn._extract_block_text_content("b1", parser)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_rich_text(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_block = AsyncMock(return_value=_api_resp(True, {"type": "divider", "divider": {}}))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()
        parser.extract_rich_text_from_block_data.return_value = None
        result = await conn._extract_block_text_content("b1", parser)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn._extract_block_text_content("b1", MagicMock())
        assert result is None


# ===================================================================
# _resolve_database_to_data_sources
# ===================================================================

class TestResolveDatabaseToDataSources:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_database = AsyncMock(return_value=_api_resp(False))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await conn._resolve_database_to_data_sources("db-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self):
        conn = _make_connector()
        conn._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        result = await conn._resolve_database_to_data_sources("db-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_block_parent_resolved(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        with patch("app.connectors.sources.notion.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (MagicMock(), MagicMock())
            conn._sync_users = AsyncMock()
            conn._sync_objects_by_type = AsyncMock()
            await conn.run_sync()
            conn._sync_users.assert_awaited_once()
            assert conn._sync_objects_by_type.await_count == 2

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        conn = _make_connector()
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
        conn = _make_connector()
        conn._fetch_block_children_recursive = AsyncMock(return_value=[])

        parser = MagicMock()
        parser.post_process_blocks = MagicMock()

        conn._convert_image_blocks_to_base64 = AsyncMock()

        result = await conn._fetch_page_as_blocks("page-1", parser)
        assert result is not None
        parser.post_process_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_page_as_blocks_with_comments(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
        ds = MagicMock()
        ds.retrieve_data_source_by_id = AsyncMock(return_value=_api_resp(False, error="not found"))
        conn._get_fresh_datasource = AsyncMock(return_value=ds)
        parser = MagicMock()

        result = await conn._fetch_data_source_as_blocks("ds-1", parser)
        assert result.blocks == []

    @pytest.mark.asyncio
    async def test_fetch_data_source_query_failure(self):
        conn = _make_connector()
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
        conn = _make_connector()
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
        conn = _make_connector()
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
