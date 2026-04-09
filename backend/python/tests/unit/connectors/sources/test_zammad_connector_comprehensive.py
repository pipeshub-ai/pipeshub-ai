"""Comprehensive tests for Zammad connector - covering untested methods."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus, RecordRelations
from app.connectors.sources.zammad.connector import (
    ATTACHMENT_ID_PARTS_COUNT,
    KB_ANSWER_ATTACHMENT_PARTS_COUNT,
    ZAMMAD_LINK_OBJECT_MAP,
    ZAMMAD_LINK_TYPE_MAP,
    ZammadConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    MimeTypes,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.zammad.comprehensive")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-zm-comp"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_new_app_roles = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": "https://zammad.example.com",
            "token": "test-zammad-token",
        },
    })
    return svc


@pytest.fixture()
def zammad_connector(mock_logger, mock_data_entities_processor,
                     mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="zm-comp-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


def _make_response(success=True, data=None, error=None, message=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    resp.message = message
    return resp


# ===========================================================================
# _determine_visibility
# ===========================================================================
class TestDetermineVisibility:
    def test_published_returns_public(self, zammad_connector):
        assert zammad_connector._determine_visibility({"published_at": "2024-01-01"}) == "PUBLIC"

    def test_internal_returns_internal(self, zammad_connector):
        assert zammad_connector._determine_visibility({"internal_at": "2024-01-01"}) == "INTERNAL"

    def test_archived_returns_archived(self, zammad_connector):
        assert zammad_connector._determine_visibility({"archived_at": "2024-01-01"}) == "ARCHIVED"

    def test_none_returns_draft(self, zammad_connector):
        assert zammad_connector._determine_visibility({}) == "DRAFT"

    def test_published_takes_priority(self, zammad_connector):
        result = zammad_connector._determine_visibility({
            "published_at": "2024-01-01",
            "internal_at": "2024-01-01",
            "archived_at": "2024-01-01",
        })
        assert result == "PUBLIC"

    def test_internal_over_archived(self, zammad_connector):
        result = zammad_connector._determine_visibility({
            "internal_at": "2024-01-01",
            "archived_at": "2024-01-01",
        })
        assert result == "INTERNAL"


# ===========================================================================
# _create_answer_with_permissions
# ===========================================================================
class TestCreateAnswerWithPermissions:
    def test_public_answer(self, zammad_connector):
        zammad_connector.base_url = "https://zammad.example.com"
        answer_data = {
            "id": 10,
            "translations": [{"title": "Test Answer", "body": "Body"}],
            "created_at": "2024-06-01T10:00:00Z",
            "updated_at": "2024-06-02T15:00:00Z",
        }
        record, permissions = zammad_connector._create_answer_with_permissions(
            answer_data=answer_data,
            category_id=5,
            visibility="PUBLIC",
            editor_role_ids=[1, 2],
            category_map={},
            existing_record=None,
        )
        assert record is not None
        assert record.external_record_id == "kb_answer_10"
        assert record.record_name == "Test Answer"
        assert record.inherit_permissions is False
        # PUBLIC: gets ORG permission
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG

    def test_internal_answer_inherits(self, zammad_connector):
        zammad_connector.base_url = "https://zammad.example.com"
        answer_data = {"id": 20, "translations": [], "created_at": "", "updated_at": ""}
        record, permissions = zammad_connector._create_answer_with_permissions(
            answer_data=answer_data,
            category_id=5,
            visibility="INTERNAL",
            editor_role_ids=[1],
            category_map={},
        )
        assert record is not None
        assert record.inherit_permissions is True
        assert len(permissions) == 0  # INTERNAL inherits from category

    def test_draft_answer_editors_only(self, zammad_connector):
        zammad_connector.base_url = "https://zammad.example.com"
        answer_data = {"id": 30, "translations": [], "created_at": "", "updated_at": ""}
        record, permissions = zammad_connector._create_answer_with_permissions(
            answer_data=answer_data,
            category_id=5,
            visibility="DRAFT",
            editor_role_ids=[10, 20],
            category_map={},
        )
        assert record is not None
        assert record.inherit_permissions is False
        assert len(permissions) == 2
        for p in permissions:
            assert p.entity_type == EntityType.ROLE
            assert p.type == PermissionType.WRITE

    def test_archived_answer_editors_only(self, zammad_connector):
        zammad_connector.base_url = "https://zammad.example.com"
        answer_data = {"id": 40, "translations": [], "created_at": "", "updated_at": ""}
        record, permissions = zammad_connector._create_answer_with_permissions(
            answer_data=answer_data,
            category_id=5,
            visibility="ARCHIVED",
            editor_role_ids=[99],
            category_map={},
        )
        assert len(permissions) == 1
        assert permissions[0].external_id == "99"

    def test_no_answer_id_returns_none(self, zammad_connector):
        record, permissions = zammad_connector._create_answer_with_permissions(
            answer_data={}, category_id=1, visibility="PUBLIC",
            editor_role_ids=[], category_map={},
        )
        assert record is None
        assert permissions == []

    def test_existing_record_version_increments(self, zammad_connector):
        zammad_connector.base_url = "https://zammad.example.com"
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1000
        answer_data = {"id": 50, "translations": [], "created_at": "", "updated_at": "2024-06-05T00:00:00Z"}
        record, _ = zammad_connector._create_answer_with_permissions(
            answer_data=answer_data, category_id=1, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=existing,
        )
        assert record.id == "existing-id"
        assert record.version == 4

    def test_answer_with_category_parent(self, zammad_connector):
        zammad_connector.base_url = "https://zammad.example.com"
        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_2"
        answer_data = {"id": 60, "translations": [], "created_at": "", "updated_at": ""}
        record, _ = zammad_connector._create_answer_with_permissions(
            answer_data=answer_data, category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map={5: cat_rg},
        )
        assert record is not None
        assert "knowledge_base/2" in record.weburl


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    async def test_stream_ticket_record(self, zammad_connector):
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "1"
        zammad_connector._process_ticket_blockgroups_for_streaming = AsyncMock(return_value=b'{"blocks": []}')
        result = await zammad_connector.stream_record(record)
        assert result is not None

    async def test_stream_webpage_record(self, zammad_connector):
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "kb_answer_1"
        zammad_connector._process_kb_answer_blockgroups_for_streaming = AsyncMock(return_value=b'{"blocks": []}')
        result = await zammad_connector.stream_record(record)
        assert result is not None

    async def test_stream_file_record(self, zammad_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "1_2_3"
        zammad_connector._process_file_for_streaming = AsyncMock(return_value=b'file content')
        result = await zammad_connector.stream_record(record)
        assert result is not None

    async def test_stream_unsupported_type_raises(self, zammad_connector):
        record = MagicMock()
        record.record_type = RecordType.PROJECT
        with pytest.raises(ValueError, match="Unsupported record type"):
            await zammad_connector.stream_record(record)

    async def test_stream_error_raises(self, zammad_connector):
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.id = "test-id"
        zammad_connector._process_ticket_blockgroups_for_streaming = AsyncMock(
            side_effect=Exception("API down")
        )
        with pytest.raises(Exception, match="API down"):
            await zammad_connector.stream_record(record)


# ===========================================================================
# _process_file_for_streaming
# ===========================================================================
class TestProcessFileForStreaming:
    async def test_ticket_attachment(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "42_1_99"
        mock_ds = MagicMock()
        mock_ds.get_ticket_attachment = AsyncMock(return_value=_make_response(
            success=True, data=b"file bytes"
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector._process_file_for_streaming(record)
        assert result == b"file bytes"

    async def test_ticket_attachment_string_content(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "42_1_99"
        mock_ds = MagicMock()
        mock_ds.get_ticket_attachment = AsyncMock(return_value=_make_response(
            success=True, data="string content"
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector._process_file_for_streaming(record)
        assert result == b"string content"

    async def test_ticket_attachment_other_type(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "42_1_99"
        mock_ds = MagicMock()
        mock_ds.get_ticket_attachment = AsyncMock(return_value=_make_response(
            success=True, data=12345
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector._process_file_for_streaming(record)
        assert result == b"12345"

    async def test_kb_answer_attachment(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "kb_answer_10_attachment_99"
        mock_ds = MagicMock()
        mock_ds.get_kb_answer_attachment = AsyncMock(return_value=_make_response(
            success=True, data=b"kb attachment"
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector._process_file_for_streaming(record)
        assert result == b"kb attachment"

    async def test_kb_attachment_failed_response(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "kb_answer_10_attachment_99"
        mock_ds = MagicMock()
        mock_ds.get_kb_answer_attachment = AsyncMock(return_value=_make_response(
            success=False, message="Not found"
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(Exception, match="Failed to download KB answer attachment"):
            await zammad_connector._process_file_for_streaming(record)

    async def test_ticket_attachment_failed_response(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "42_1_99"
        mock_ds = MagicMock()
        mock_ds.get_ticket_attachment = AsyncMock(return_value=_make_response(
            success=False, message="Not found"
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(Exception, match="Failed to download attachment"):
            await zammad_connector._process_file_for_streaming(record)

    async def test_invalid_ticket_attachment_format(self, zammad_connector):
        record = MagicMock()
        record.external_record_id = "invalid_format"
        mock_ds = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(ValueError, match="Invalid attachment ID format"):
            await zammad_connector._process_file_for_streaming(record)

    async def test_invalid_kb_attachment_format(self, zammad_connector):
        """KB answer attachment with extra _attachment_ segments raises ValueError."""
        record = MagicMock()
        # This produces 3 parts when split by "_attachment_" instead of the expected 2
        record.external_record_id = "kb_answer_10_attachment_20_attachment_30"
        mock_ds = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(ValueError, match="Invalid KB answer attachment ID format"):
            await zammad_connector._process_file_for_streaming(record)


# ===========================================================================
# get_filter_options
# ===========================================================================
class TestGetFilterOptions:
    async def test_group_ids_filter(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True, data=[
                {"id": 1, "name": "Support", "active": True},
                {"id": 2, "name": "Sales", "active": True},
                {"id": 3, "name": "Inactive", "active": False},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        from app.connectors.core.registry.filters import SyncFilterKey
        result = await zammad_connector.get_filter_options(SyncFilterKey.GROUP_IDS.value)
        assert result.success is True
        assert len(result.options) == 2  # active only

    async def test_group_ids_with_search(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True, data=[
                {"id": 1, "name": "Support", "active": True},
                {"id": 2, "name": "Sales", "active": True},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        from app.connectors.core.registry.filters import SyncFilterKey
        result = await zammad_connector.get_filter_options(SyncFilterKey.GROUP_IDS.value, search="Sup")
        assert len(result.options) == 1
        assert result.options[0].label == "Support"

    async def test_group_ids_pagination(self, zammad_connector):
        mock_ds = MagicMock()
        groups = [{"id": i, "name": f"Group {i}", "active": True} for i in range(1, 30)]
        mock_ds.list_groups = AsyncMock(return_value=_make_response(success=True, data=groups))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        from app.connectors.core.registry.filters import SyncFilterKey
        result = await zammad_connector.get_filter_options(SyncFilterKey.GROUP_IDS.value, page=1, limit=10)
        assert len(result.options) == 10
        assert result.has_more is True
        assert result.page == 1

    async def test_unknown_filter_key(self, zammad_connector):
        result = await zammad_connector.get_filter_options("unknown_key")
        assert result.success is True
        assert len(result.options) == 0


# ===========================================================================
# Abstract methods
# ===========================================================================
class TestAbstractMethods:
    async def test_run_incremental_sync(self, zammad_connector):
        zammad_connector.run_sync = AsyncMock()
        await zammad_connector.run_incremental_sync()
        zammad_connector.run_sync.assert_awaited_once()

    async def test_test_connection_and_access_success(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(success=True))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector.test_connection_and_access()
        assert result is True

    async def test_test_connection_and_access_failure(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(success=False, message="Auth failed"))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector.test_connection_and_access()
        assert result is False

    async def test_test_connection_and_access_exception(self, zammad_connector):
        zammad_connector._get_fresh_datasource = AsyncMock(side_effect=Exception("Network error"))
        result = await zammad_connector.test_connection_and_access()
        assert result is False

    async def test_get_signed_url(self, zammad_connector):
        result = await zammad_connector.get_signed_url(MagicMock())
        assert result == ""

    async def test_handle_webhook_notification(self, zammad_connector):
        await zammad_connector.handle_webhook_notification({})

    async def test_cleanup_with_client(self, zammad_connector):
        mock_client = MagicMock()
        internal = MagicMock()
        internal.close = AsyncMock()
        mock_client.get_client.return_value = internal
        zammad_connector.external_client = mock_client
        await zammad_connector.cleanup()
        internal.close.assert_awaited_once()

    async def test_cleanup_no_client(self, zammad_connector):
        zammad_connector.external_client = None
        await zammad_connector.cleanup()

    async def test_cleanup_close_error(self, zammad_connector):
        mock_client = MagicMock()
        internal = MagicMock()
        internal.close = AsyncMock(side_effect=Exception("Already closed"))
        mock_client.get_client.return_value = internal
        zammad_connector.external_client = mock_client
        # Should not raise
        await zammad_connector.cleanup()


# ===========================================================================
# _fetch_kb_answer_attachments
# ===========================================================================
class TestFetchKbAnswerAttachments:
    async def test_no_answer_id(self, zammad_connector):
        result = await zammad_connector._fetch_kb_answer_attachments(
            {}, MagicMock(), []
        )
        assert result == []

    async def test_no_attachments(self, zammad_connector):
        result = await zammad_connector._fetch_kb_answer_attachments(
            {"id": 1}, MagicMock(), []
        )
        assert result == []

    async def test_with_attachments(self, zammad_connector):
        from app.connectors.core.registry.filters import FilterCollection
        zammad_connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "parent-id"
        parent.external_record_id = "kb_answer_10"
        parent.external_record_group_id = "cat_5"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = "https://zammad.example.com/#knowledge_base/1/locale/en-us/answer/10"
        parent.source_created_at = 1000
        parent.source_updated_at = 2000
        parent.inherit_permissions = False

        answer_data = {
            "id": 10,
            "attachments": [
                {"id": 1, "filename": "doc.pdf", "size": 1024, "preferences": {"Content-Type": "application/pdf"}},
            ],
            "translations": [],
        }
        result = await zammad_connector._fetch_kb_answer_attachments(
            answer_data, parent, [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        )
        assert len(result) == 1
        file_record, perms = result[0]
        assert file_record.record_name == "doc.pdf"
        assert len(perms) == 1

    async def test_translation_attachments_merged(self, zammad_connector):
        from app.connectors.core.registry.filters import FilterCollection
        zammad_connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "parent-id"
        parent.external_record_id = "kb_answer_10"
        parent.external_record_group_id = "cat_5"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0
        parent.inherit_permissions = True

        answer_data = {
            "id": 10,
            "attachments": [],
            "translations": [
                {"attachments": [
                    {"id": 50, "filename": "trans.pdf", "size": 512, "preferences": {"Content-Type": "application/pdf"}},
                ]},
            ],
        }
        result = await zammad_connector._fetch_kb_answer_attachments(answer_data, parent, [])
        assert len(result) == 1


# ===========================================================================
# _convert_html_images_to_base64
# ===========================================================================
class TestConvertHtmlImagesToBase64:
    async def test_empty_content(self, zammad_connector):
        result = await zammad_connector._convert_html_images_to_base64("")
        assert result == ""

    async def test_no_images(self, zammad_connector):
        html = "<p>Hello world</p>"
        result = await zammad_connector._convert_html_images_to_base64(html)
        assert "Hello world" in result

    async def test_non_attachment_image_skipped(self, zammad_connector):
        html = '<img src="https://example.com/image.png">'
        result = await zammad_connector._convert_html_images_to_base64(html)
        assert "example.com" in result

    async def test_attachment_image_converted(self, zammad_connector):
        html = '<img src="/api/v1/attachments/42">'
        mock_ds = MagicMock()
        mock_ds.get_kb_answer_attachment = AsyncMock(
            return_value=_make_response(success=True, data=b'\x89PNG fake png data')
        )
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        zammad_connector.data_source = MagicMock()
        result = await zammad_connector._convert_html_images_to_base64(html)
        assert "data:image/png;base64," in result

    async def test_attachment_download_failure_skipped(self, zammad_connector):
        html = '<img src="/api/v1/attachments/42">'
        mock_ds = MagicMock()
        mock_ds.get_kb_answer_attachment = AsyncMock(
            return_value=_make_response(success=False)
        )
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        zammad_connector.data_source = MagicMock()
        result = await zammad_connector._convert_html_images_to_base64(html)
        # Should keep original src
        assert "/api/v1/attachments/42" in result


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    async def test_empty_records(self, zammad_connector):
        await zammad_connector.reindex_records([])
        zammad_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_updated_records(self, zammad_connector):
        zammad_connector.data_source = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock()
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.TICKET
        record.external_record_id = "42"
        updated_record = MagicMock()
        zammad_connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(updated_record, [])
        )
        await zammad_connector.reindex_records([record])
        zammad_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_non_updated_records_reindexed(self, zammad_connector):
        zammad_connector.data_source = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock()
        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        record.record_type = RecordType.TICKET
        type(record).__name__ = "TicketRecord"
        zammad_connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await zammad_connector.reindex_records([record])
        zammad_connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    async def test_base_record_class_skipped(self, zammad_connector):
        zammad_connector.data_source = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock()
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.TICKET
        type(record).__name__ = "Record"
        zammad_connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await zammad_connector.reindex_records([record])
        zammad_connector.data_entities_processor.reindex_existing_records.assert_not_awaited()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    async def test_ticket_type(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.TICKET
        zammad_connector._check_and_fetch_updated_ticket = AsyncMock(return_value=("updated", []))
        result = await zammad_connector._check_and_fetch_updated_record(record)
        assert result == ("updated", [])

    async def test_webpage_type(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.WEBPAGE
        zammad_connector._check_and_fetch_updated_kb_answer = AsyncMock(return_value=("updated", []))
        result = await zammad_connector._check_and_fetch_updated_record(record)
        assert result == ("updated", [])

    async def test_file_type_returns_none(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.FILE
        result = await zammad_connector._check_and_fetch_updated_record(record)
        assert result is None

    async def test_unknown_type_returns_none(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.PROJECT
        result = await zammad_connector._check_and_fetch_updated_record(record)
        assert result is None

    async def test_exception_returns_none(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.TICKET
        zammad_connector._check_and_fetch_updated_ticket = AsyncMock(
            side_effect=Exception("Error")
        )
        result = await zammad_connector._check_and_fetch_updated_record(record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_ticket
# ===========================================================================
class TestCheckAndFetchUpdatedTicket:
    async def test_ticket_not_changed(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "42"
        record.source_updated_at = 1000
        mock_ds = MagicMock()
        mock_ds.get_ticket = AsyncMock(return_value=_make_response(
            success=True, data={"updated_at": "1970-01-01T00:00:01Z"}
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector._check_and_fetch_updated_ticket(record)
        assert result is None

    async def test_ticket_not_found(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "42"
        mock_ds = MagicMock()
        mock_ds.get_ticket = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await zammad_connector._check_and_fetch_updated_ticket(record)
        assert result is None

    async def test_ticket_changed(self, zammad_connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "42"
        record.source_updated_at = 1000
        mock_ds = MagicMock()
        mock_ds.get_ticket = AsyncMock(return_value=_make_response(
            success=True, data={"id": 42, "title": "Updated", "updated_at": "2024-06-15T12:00:00Z"}
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        zammad_connector._transform_ticket_to_ticket_record = AsyncMock(
            return_value=MagicMock()
        )
        result = await zammad_connector._check_and_fetch_updated_ticket(record)
        assert result is not None


# ===========================================================================
# _get_fresh_datasource edge case
# ===========================================================================
class TestGetFreshDatasourceUnsupportedAuth:
    async def test_unsupported_auth_type_raises(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        zammad_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH2"}
        })
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await zammad_connector._get_fresh_datasource()


# ===========================================================================
# Constants
# ===========================================================================
class TestConstants:
    def test_attachment_id_parts_count(self):
        assert ATTACHMENT_ID_PARTS_COUNT == 3

    def test_kb_answer_attachment_parts_count(self):
        assert KB_ANSWER_ATTACHMENT_PARTS_COUNT == 2
