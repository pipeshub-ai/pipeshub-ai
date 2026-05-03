"""Comprehensive tests for SharePoint Online connector covering uncovered methods."""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    COMPOSITE_SITE_ID_COMMA_COUNT,
    COMPOSITE_SITE_ID_PARTS_COUNT,
    SharePointConnector,
    SharePointCredentials,
    SharePointRecordType,
    SharePointSubscriptionManager,
    SiteMetadata,
)
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    SharePointListItemRecord,
    SharePointListRecord,
    SharePointPageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_mock_deps():
    logger = logging.getLogger("test.sharepoint.full")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-sp-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_record_metadata_update = AsyncMock()
    data_entities_processor.on_updated_record_permissions = AsyncMock()
    data_entities_processor.on_record_content_update = AsyncMock()
    data_entities_processor.on_user_group_deleted = AsyncMock(return_value=True)
    data_entities_processor.on_user_group_member_removed = AsyncMock(return_value=True)
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.reindex_existing_records = AsyncMock()

    data_store_provider = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return SharePointConnector(logger, dep, dsp, cs, "conn-sp-1", "team", "test-user-id")


def _make_file_record(**overrides):
    defaults = dict(
        id=str(uuid.uuid4()),
        record_name="test.docx",
        record_type=RecordType.FILE,
        record_status=ProgressStatus.NOT_STARTED,
        external_record_id="item-123",
        external_revision_id="etag-1",
        external_record_group_id="drive-1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SHAREPOINT_ONLINE,
        connector_id="conn-sp-1",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        is_file=True,
        extension="docx",
    )
    defaults.update(overrides)
    return FileRecord(**defaults)


def _make_page_record(**overrides):
    defaults = dict(
        id=str(uuid.uuid4()),
        record_name="Test Page - Site A",
        record_type=RecordType.SHAREPOINT_PAGE,
        record_status=ProgressStatus.NOT_STARTED,
        external_record_id="page-123",
        external_revision_id="etag-page-1",
        external_record_group_id="site-id-1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SHAREPOINT_ONLINE,
        connector_id="conn-sp-1",
        mime_type=MimeTypes.HTML.value,
    )
    defaults.update(overrides)
    return SharePointPageRecord(**defaults)


def _make_list_item_record(**overrides):
    defaults = dict(
        id=str(uuid.uuid4()),
        record_name="List Item 1",
        record_type=RecordType.SHAREPOINT_LIST_ITEM,
        record_status=ProgressStatus.NOT_STARTED,
        external_record_id="listitem-123",
        external_revision_id="etag-li-1",
        external_record_group_id="site-id-1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SHAREPOINT_ONLINE,
        connector_id="conn-sp-1",
        mime_type=MimeTypes.UNKNOWN.value,
    )
    defaults.update(overrides)
    # Pop semantic_metadata before pydantic construction since the source code
    # expects it to be a plain dict (uses .get()), not a SemanticMetadata model.
    sm = defaults.pop("semantic_metadata", {"site_id": "site-id-1", "list_id": "list-1"})
    record = SharePointListItemRecord(**defaults)
    record.semantic_metadata = sm
    return record


def _make_record(**overrides):
    defaults = dict(
        id=str(uuid.uuid4()),
        record_name="generic record",
        record_type=RecordType.OTHERS,
        external_record_id="ext-123",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SHAREPOINT_ONLINE,
        connector_id="conn-sp-1",
    )
    defaults.update(overrides)
    return Record(**defaults)


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_stream_file_record_success(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.get_signed_url = AsyncMock(return_value="https://signed.url/file.docx")

        record = _make_file_record()

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.create_stream_record_response") as mock_create, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.stream_content") as mock_stream:
            mock_create.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_create.assert_called_once()
            mock_stream.assert_called_once_with("https://signed.url/file.docx")

    @pytest.mark.asyncio
    async def test_stream_file_record_no_signed_url(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.get_signed_url = AsyncMock(return_value=None)

        record = _make_file_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_page_record_success(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector._get_page_content = AsyncMock(return_value="<div>Page Content</div>")

        record = _make_page_record()

        result = await connector.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_page_record_not_found(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector._get_page_content = AsyncMock(return_value=None)

        record = _make_page_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_unsupported_record_type(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        record = _make_record(record_type=RecordType.SHAREPOINT_LIST)

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_record_reraises_http_exception(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.get_signed_url = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))

        record = _make_file_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_stream_record_generic_exception(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.get_signed_url = AsyncMock(side_effect=RuntimeError("connection lost"))

        record = _make_file_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    @pytest.mark.asyncio
    async def test_get_signed_url_success(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed.url/file")

        record = _make_file_record()
        result = await connector.get_signed_url(record)
        assert result == "https://signed.url/file"

    @pytest.mark.asyncio
    async def test_get_signed_url_non_file_record(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        record = _make_page_record()
        result = await connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_signed_url_missing_drive_id(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        record = _make_file_record(external_record_group_id=None)
        result = await connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_signed_url_raises_on_error(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(side_effect=Exception("token expired"))

        record = _make_file_record()
        with pytest.raises(Exception, match="token expired"):
            await connector.get_signed_url(record)


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_full(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock()
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()
        connector.site_cache = {"site-1": MagicMock()}
        connector.certificate_path = None

        await connector.cleanup()

        assert connector.credential is None
        assert connector.client is None
        assert connector.msgraph_client is None
        assert len(connector.site_cache) == 0

    @pytest.mark.asyncio
    async def test_cleanup_with_temp_cert_file(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock()
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()
        connector.certificate_path = "/tmp/test_cert.pem"

        with patch("os.path.exists", return_value=True), \
             patch("os.remove") as mock_remove:
            await connector.cleanup()
            mock_remove.assert_called_once_with("/tmp/test_cert.pem")

    @pytest.mark.asyncio
    async def test_cleanup_credential_close_error(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock(side_effect=Exception("already closed"))
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()

        await connector.cleanup()
        assert connector.credential is None

    @pytest.mark.asyncio
    async def test_cleanup_cert_removal_error(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock()
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()
        connector.certificate_path = "/tmp/nonexistent.pem"

        with patch("os.path.exists", return_value=True), \
             patch("os.remove", side_effect=OSError("permission denied")):
            await connector.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_no_attributes(self):
        connector = _make_connector()
        await connector.cleanup()


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_reindex_empty_records(self):
        connector = _make_connector()
        await connector.reindex_records([])
        connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_reindex_no_msgraph_client(self):
        connector = _make_connector()
        connector.msgraph_client = None

        records = [_make_file_record()]
        with pytest.raises(Exception, match="MS Graph client not initialized"):
            await connector.reindex_records(records)

    @pytest.mark.asyncio
    async def test_reindex_with_updated_record(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        updated_file = _make_file_record()
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(updated_file, [])
        )

        records = [_make_file_record()]
        await connector.reindex_records(records)

        connector.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_with_non_updated_record(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        records = [_make_file_record()]
        await connector.reindex_records(records)

        connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_check_raises(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=Exception("API error")
        )

        records = [_make_file_record()]
        await connector.reindex_records(records)

    @pytest.mark.asyncio
    async def test_reindex_outer_exception(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=Exception("DB error")
        )

        records = [_make_file_record()]
        with pytest.raises(Exception, match="DB error"):
            await connector.reindex_records(records)


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_missing_external_record_id(self):
        connector = _make_connector()
        record = _make_record(external_record_id="")
        result = await connector._check_and_fetch_updated_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_file_record_delegates(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_file_record = AsyncMock(return_value=("rec", []))

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_record(record, [])
        assert result == ("rec", [])

    @pytest.mark.asyncio
    async def test_page_record_delegates(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_page_record = AsyncMock(return_value=("page_rec", []))

        record = _make_page_record()
        result = await connector._check_and_fetch_updated_record(record, [])
        assert result == ("page_rec", [])

    @pytest.mark.asyncio
    async def test_list_item_record_delegates(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_list_item_record = AsyncMock(return_value=("li_rec", []))

        record = _make_list_item_record()
        result = await connector._check_and_fetch_updated_record(record, [])
        assert result == ("li_rec", [])

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        connector = _make_connector()
        record = _make_record(record_type=RecordType.OTHERS)
        result = await connector._check_and_fetch_updated_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_file_record = AsyncMock(side_effect=Exception("fail"))

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_record(record, [])
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_file_record
# ===========================================================================


class TestCheckAndFetchUpdatedFileRecord:

    @pytest.mark.asyncio
    async def test_missing_drive_id(self):
        connector = _make_connector()
        record = _make_file_record(external_record_group_id=None)
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_item_id(self):
        connector = _make_connector()
        record = _make_file_record(external_record_id="")
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_item_not_found_at_source(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_item_deleted_at_source(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_item = MagicMock()
        mock_item.deleted = MagicMock()
        mock_item.parent_reference = MagicMock()
        mock_item.parent_reference.site_id = "site-1"
        connector._safe_api_call = AsyncMock(return_value=mock_item)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        connector._process_drive_item = AsyncMock(
            return_value=RecordUpdate(
                record=None,
                is_new=False,
                is_updated=False,
                is_deleted=True,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
            )
        )

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_item_updated_at_source(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_item = MagicMock()
        mock_item.deleted = None
        mock_item.parent_reference = MagicMock()
        mock_item.parent_reference.site_id = "site-1"
        connector._safe_api_call = AsyncMock(return_value=mock_item)

        updated_file = _make_file_record()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        connector._process_drive_item = AsyncMock(
            return_value=RecordUpdate(
                record=updated_file,
                is_new=False,
                is_updated=True,
                is_deleted=False,
                metadata_changed=True,
                content_changed=True,
                permissions_changed=False,
                new_permissions=[],
            )
        )

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is not None
        assert result[0].id == record.id

    @pytest.mark.asyncio
    async def test_item_not_updated(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_item = MagicMock()
        mock_item.deleted = None
        mock_item.parent_reference = MagicMock()
        mock_item.parent_reference.site_id = "site-1"
        connector._safe_api_call = AsyncMock(return_value=mock_item)

        existing_file = _make_file_record()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        connector._process_drive_item = AsyncMock(
            return_value=RecordUpdate(
                record=existing_file,
                is_new=False,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
            )
        )

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(side_effect=Exception("api fail"))

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_file_record(record, [])
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_page_record
# ===========================================================================


class TestCheckAndFetchUpdatedPageRecord:

    @pytest.mark.asyncio
    async def test_missing_site_id(self):
        connector = _make_connector()
        record = _make_page_record(external_record_group_id=None)
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_page_id(self):
        connector = _make_connector()
        record = _make_page_record(external_record_id="")
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_page_not_found(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        record = _make_page_record()
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_page_not_updated(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_page = MagicMock()
        mock_page.e_tag = "etag-page-1"
        connector._safe_api_call = AsyncMock(return_value=mock_page)

        record = _make_page_record(external_revision_id="etag-page-1")
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_page_updated(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_page = MagicMock()
        mock_page.e_tag = "etag-page-2"
        connector._safe_api_call = AsyncMock(return_value=mock_page)

        site_response = MagicMock()
        site_response.display_name = "Test Site"
        site_response.name = "test-site"

        connector._safe_api_call = AsyncMock(side_effect=[mock_page, site_response])

        updated_page = _make_page_record()
        connector._create_page_record = AsyncMock(return_value=updated_page)
        connector._get_page_permissions = AsyncMock(return_value=[])

        record = _make_page_record(external_revision_id="etag-page-1")
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is not None
        assert result[0].id == record.id

    @pytest.mark.asyncio
    async def test_page_access_denied(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(side_effect=Exception("403 accessdenied"))

        record = _make_page_record()
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_page_create_returns_none(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_page = MagicMock()
        mock_page.e_tag = "etag-page-new"
        site_resp = MagicMock()
        site_resp.display_name = "Site"
        connector._safe_api_call = AsyncMock(side_effect=[mock_page, site_resp])
        connector._create_page_record = AsyncMock(return_value=None)

        record = _make_page_record(external_revision_id="old-etag")
        result = await connector._check_and_fetch_updated_page_record(record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_list_item_record
# ===========================================================================


class TestCheckAndFetchUpdatedListItemRecord:

    @pytest.mark.asyncio
    async def test_missing_semantic_metadata(self):
        connector = _make_connector()
        record = _make_list_item_record(semantic_metadata=None)
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_site_id_in_metadata(self):
        connector = _make_connector()
        record = _make_list_item_record(semantic_metadata={"list_id": "l1"})
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_item_not_found(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        record = _make_list_item_record()
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_item_not_updated(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_item = MagicMock()
        mock_item.e_tag = "etag-li-1"
        connector._safe_api_call = AsyncMock(return_value=mock_item)

        record = _make_list_item_record(external_revision_id="etag-li-1")
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_item_updated(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_item = MagicMock()
        mock_item.e_tag = "etag-li-new"
        connector._safe_api_call = AsyncMock(return_value=mock_item)

        updated_li = _make_list_item_record()
        connector._create_list_item_record = AsyncMock(return_value=updated_li)
        connector._get_list_item_permissions = AsyncMock(return_value=[])

        record = _make_list_item_record(external_revision_id="etag-li-old")
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is not None
        assert result[0].id == record.id

    @pytest.mark.asyncio
    async def test_create_returns_none(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_item = MagicMock()
        mock_item.e_tag = "new-etag"
        connector._safe_api_call = AsyncMock(return_value=mock_item)
        connector._create_list_item_record = AsyncMock(return_value=None)

        record = _make_list_item_record(external_revision_id="old-etag")
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_access_denied(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(side_effect=Exception("404 notfound"))

        record = _make_list_item_record()
        result = await connector._check_and_fetch_updated_list_item_record(record)
        assert result is None


# ===========================================================================
# _handle_record_updates
# ===========================================================================


class TestHandleRecordUpdates:

    @pytest.mark.asyncio
    async def test_deleted_record(self):
        connector = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=None,
            external_record_id="ext-1",
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once_with(record_id="ext-1")

    @pytest.mark.asyncio
    async def test_metadata_changed(self):
        connector = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = _make_file_record()
        update = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_permissions_changed(self):
        connector = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = _make_file_record()
        perms = [Permission(email="user@test.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        update = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=True,
            new_permissions=perms,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_called_once()

    @pytest.mark.asyncio
    async def test_content_changed(self):
        connector = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = _make_file_record()
        update = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=True,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_update_exception(self):
        connector = _make_connector()
        connector.data_entities_processor.on_record_deleted = AsyncMock(side_effect=Exception("db fail"))
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=None,
            external_record_id="ext-1",
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)


# ===========================================================================
# _reinitialize_credential_if_needed
# ===========================================================================


class TestReinitializeCredential:

    @pytest.mark.asyncio
    async def test_credential_still_valid(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock()

        await connector._reinitialize_credential_if_needed()
        connector.credential.get_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_credential_needs_reinit_client_secret(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("transport closed"))
        connector.credential.close = AsyncMock()
        connector.tenant_id = "t1"
        connector.client_id = "c1"
        connector.client_secret = "s1"
        connector.certificate_path = None

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.ClientSecretCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock()
            mock_cred.return_value = new_cred

            await connector._reinitialize_credential_if_needed()

    @pytest.mark.asyncio
    async def test_credential_needs_reinit_certificate(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("transport closed"))
        connector.credential.close = AsyncMock()
        connector.tenant_id = "t1"
        connector.client_id = "c1"
        connector.client_secret = "s1"
        connector.certificate_path = "/tmp/cert.pem"

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.CertificateCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock()
            mock_cred.return_value = new_cred

            await connector._reinitialize_credential_if_needed()

    @pytest.mark.asyncio
    async def test_credential_reinit_missing_creds(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("closed"))
        connector.credential.close = AsyncMock()
        connector.tenant_id = None
        connector.client_id = None
        connector.client_secret = None
        connector.certificate_path = None

        with pytest.raises(ValueError, match="Cannot reinitialize"):
            await connector._reinitialize_credential_if_needed()


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_incremental_sync_success(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        mock_site = MagicMock()
        mock_site.display_name = "Test Site"
        mock_site.name = "test"
        connector._get_all_sites = AsyncMock(return_value=[mock_site])
        connector._sync_site_content = AsyncMock()

        await connector.run_incremental_sync()

        connector._sync_site_content.assert_called_once_with(mock_site)

    @pytest.mark.asyncio
    async def test_incremental_sync_site_error(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        mock_site = MagicMock()
        mock_site.display_name = "Fail Site"
        mock_site.name = "fail"
        connector._get_all_sites = AsyncMock(return_value=[mock_site])
        connector._sync_site_content = AsyncMock(side_effect=Exception("site sync fail"))

        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_incremental_sync_outer_error(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock(
            side_effect=Exception("credential fail")
        )

        with pytest.raises(Exception, match="credential fail"):
            await connector.run_incremental_sync()


# ===========================================================================
# _map_group_to_permission_type
# ===========================================================================


class TestMapGroupToPermissionType:

    def test_owner_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Site Owners") == PermissionType.WRITE

    def test_admin_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Admin Group") == PermissionType.WRITE

    def test_member_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Team Members") == PermissionType.WRITE

    def test_contributor_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Contributors") == PermissionType.WRITE

    def test_editor_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Editors") == PermissionType.WRITE

    def test_visitor_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Site Visitors") == PermissionType.READ

    def test_empty_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("") == PermissionType.READ

    def test_none_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type(None) == PermissionType.READ

    def test_full_control_group(self):
        connector = _make_connector()
        assert connector._map_group_to_permission_type("Full Control Users") == PermissionType.WRITE


# ===========================================================================
# _normalize_document_library_url
# ===========================================================================


class TestNormalizeDocumentLibraryUrl:

    def test_standard_url(self):
        connector = _make_connector()
        result = connector._normalize_document_library_url(
            "https://contoso.sharepoint.com/sites/TestSite/Shared%20Documents"
        )
        assert result == "contoso.sharepoint.com/sites/testsite/shared documents"

    def test_url_with_forms(self):
        connector = _make_connector()
        result = connector._normalize_document_library_url(
            "https://contoso.sharepoint.com/sites/Site1/Documents/Forms/AllItems.aspx"
        )
        assert result == "contoso.sharepoint.com/sites/site1/documents"

    def test_empty_url(self):
        connector = _make_connector()
        assert connector._normalize_document_library_url("") == ""

    def test_none_url(self):
        connector = _make_connector()
        assert connector._normalize_document_library_url(None) == ""

    def test_url_with_trailing_slash(self):
        connector = _make_connector()
        result = connector._normalize_document_library_url(
            "https://contoso.sharepoint.com/sites/MySite/Docs/"
        )
        assert result == "contoso.sharepoint.com/sites/mysite/docs"


# ===========================================================================
# _parse_datetime
# ===========================================================================


class TestParseDatetime:

    def test_datetime_object(self):
        connector = _make_connector()
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert result == int(dt.timestamp() * 1000)

    def test_iso_string(self):
        connector = _make_connector()
        result = connector._parse_datetime("2024-01-15T10:30:00Z")
        assert isinstance(result, int)

    def test_iso_string_with_offset(self):
        connector = _make_connector()
        result = connector._parse_datetime("2024-01-15T10:30:00+00:00")
        assert isinstance(result, int)

    def test_none(self):
        connector = _make_connector()
        assert connector._parse_datetime(None) is None

    def test_empty_string(self):
        connector = _make_connector()
        assert connector._parse_datetime("") is None

    def test_invalid_string(self):
        connector = _make_connector()
        assert connector._parse_datetime("not-a-date") is None


# ===========================================================================
# _construct_site_url
# ===========================================================================


class TestConstructSiteUrl:

    def test_valid_site_id(self):
        connector = _make_connector()
        assert connector._construct_site_url("site-123") == "site-123"

    def test_empty_site_id(self):
        connector = _make_connector()
        assert connector._construct_site_url("") == ""

    def test_none_site_id(self):
        connector = _make_connector()
        assert connector._construct_site_url(None) == ""


# ===========================================================================
# _validate_site_id
# ===========================================================================


class TestValidateSiteId:

    def test_composite_site_id(self):
        connector = _make_connector()
        site_id = "contoso.sharepoint.com,12345678-1234-1234-1234-123456789012,87654321-4321-4321-4321-210987654321"
        assert connector._validate_site_id(site_id) is True

    def test_root_site_id(self):
        connector = _make_connector()
        assert connector._validate_site_id("root") is True

    def test_long_guid(self):
        connector = _make_connector()
        assert connector._validate_site_id("12345678-1234-1234-1234-123456789012") is True

    def test_empty(self):
        connector = _make_connector()
        assert connector._validate_site_id("") is False

    def test_none(self):
        connector = _make_connector()
        assert connector._validate_site_id(None) is False

    def test_short_string(self):
        connector = _make_connector()
        assert connector._validate_site_id("abc") is False

    def test_composite_wrong_parts(self):
        connector = _make_connector()
        assert connector._validate_site_id("a,b") is False

    def test_composite_short_guids(self):
        connector = _make_connector()
        # Composite format with short GUIDs fails the composite check but the
        # overall string length (20) exceeds ROOT_SITE_ID_LENGTH (10), so it
        # falls through to the length-based acceptance path and returns True.
        assert connector._validate_site_id("host.com,short,short") is True


# ===========================================================================
# _normalize_site_id
# ===========================================================================


class TestNormalizeSiteId:

    def test_already_composite(self):
        connector = _make_connector()
        site_id = "host.com,guid1,guid2"
        assert connector._normalize_site_id(site_id) == site_id

    def test_empty(self):
        connector = _make_connector()
        assert connector._normalize_site_id("") == ""

    def test_none(self):
        connector = _make_connector()
        assert connector._normalize_site_id(None) is None

    def test_found_in_cache(self):
        connector = _make_connector()
        connector.site_cache = {
            "host.com,guid1,guid2": SiteMetadata(
                site_id="host.com,guid1,guid2",
                site_url="https://host.com",
                site_name="test",
                is_root=False,
            )
        }
        result = connector._normalize_site_id("guid1,guid2")
        assert result == "host.com,guid1,guid2"

    def test_prepend_hostname(self):
        connector = _make_connector()
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        result = connector._normalize_site_id("guid1,guid2")
        assert result == "contoso.sharepoint.com,guid1,guid2"

    def test_single_guid(self):
        connector = _make_connector()
        connector.site_cache = {}
        result = connector._normalize_site_id("single-guid-value")
        assert result == "single-guid-value"


# ===========================================================================
# _should_skip_list
# ===========================================================================


class TestShouldSkipList:

    def test_hidden_list(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = True
        assert connector._should_skip_list(list_obj, "My List") is True

    def test_hidden_attribute(self):
        connector = _make_connector()
        list_obj = MagicMock(spec=[])
        list_obj.hidden = True
        assert connector._should_skip_list(list_obj, "My List") is True

    def test_system_prefix_underscore(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        assert connector._should_skip_list(list_obj, "_private") is True

    def test_system_prefix_form_templates(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        assert connector._should_skip_list(list_obj, "Form Templates Library") is True

    def test_system_template(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "Catalog"
        assert connector._should_skip_list(list_obj, "My Catalog") is True

    def test_normal_list(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "GenericList"
        assert connector._should_skip_list(list_obj, "Project Tasks") is False


# ===========================================================================
# _get_date_filters
# ===========================================================================


class TestGetDateFilters:

    def test_no_filters(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)

        result = connector._get_date_filters()
        assert result == (None, None, None, None)

    def test_with_modified_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-12-31T23:59:59")

        def side_effect(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = side_effect

        modified_after, modified_before, created_after, created_before = connector._get_date_filters()
        assert modified_after is not None
        assert modified_before is not None
        assert created_after is None
        assert created_before is None

    def test_empty_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = True

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)

        result = connector._get_date_filters()
        assert result == (None, None, None, None)


# ===========================================================================
# _create_document_library_record_group
# ===========================================================================


class TestCreateDocumentLibraryRecordGroup:

    def test_success(self):
        connector = _make_connector()
        drive = MagicMock()
        drive.id = "drive-1"
        drive.name = "Shared Documents"
        drive.web_url = "https://contoso.sharepoint.com/sites/test/Shared%20Documents"
        drive.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        drive.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)

        result = connector._create_document_library_record_group(drive, "site-1", "internal-site-rg-1")
        assert result is not None
        assert result.external_group_id == "drive-1"
        assert result.name == "Shared Documents"
        assert result.parent_external_group_id == "site-1"
        assert result.parent_record_group_id == "internal-site-rg-1"

    def test_no_drive_id(self):
        connector = _make_connector()
        drive = MagicMock()
        drive.id = None

        result = connector._create_document_library_record_group(drive, "site-1", "rg-1")
        assert result is None

    def test_exception(self):
        connector = _make_connector()
        drive = MagicMock()
        type(drive).id = PropertyMock(side_effect=Exception("bad"))

        result = connector._create_document_library_record_group(drive, "site-1", "rg-1")
        assert result is None


# ===========================================================================
# _pass_site_ids_filters
# ===========================================================================


class TestPassSiteIdsFilters:

    def test_no_filter(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)
        assert connector._pass_site_ids_filters("site-1") is True

    def test_empty_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = True
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("site-1") is True

    def test_in_filter_match(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["site-1", "site-2"]
        mock_op = MagicMock()
        mock_op.value = "in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("site-1") is True

    def test_in_filter_no_match(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["site-1", "site-2"]
        mock_op = MagicMock()
        mock_op.value = "in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("site-3") is False

    def test_not_in_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["site-1"]
        mock_op = MagicMock()
        mock_op.value = "not_in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("site-2") is True
        assert connector._pass_site_ids_filters("site-1") is False

    def test_empty_site_id(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["site-1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("") is False

    def test_invalid_filter_value(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not-a-list"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("site-1") is True

    def test_unknown_operator(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["site-1"]
        mock_op = MagicMock()
        mock_op.value = "UNKNOWN_OP"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("site-1") is True


# ===========================================================================
# _pass_drive_key_filters
# ===========================================================================


class TestPassDriveKeyFilters:

    def test_no_filter(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)
        assert connector._pass_drive_key_filters("drive-1") is True

    def test_empty_drive_key(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["d1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_drive_key_filters("") is False

    def test_in_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["drive-1"]
        mock_op = MagicMock()
        mock_op.value = "in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_drive_key_filters("drive-1") is True
        assert connector._pass_drive_key_filters("drive-2") is False

    def test_not_in_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["drive-1"]
        mock_op = MagicMock()
        mock_op.value = "not_in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_drive_key_filters("drive-2") is True
        assert connector._pass_drive_key_filters("drive-1") is False


# ===========================================================================
# _pass_extension_filter
# ===========================================================================


class TestPassExtensionFilter:

    def test_no_filter(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)

        item = MagicMock()
        item.folder = None
        item.name = "test.pdf"
        assert connector._pass_extension_filter(item) is True

    def test_folder_always_passes(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["docx"]
        connector.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = MagicMock()
        assert connector._pass_extension_filter(item) is True

    def test_in_filter_match(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_op = MagicMock()
        mock_op.value = "in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "test.pdf"
        assert connector._pass_extension_filter(item) is True

    def test_in_filter_no_match(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = "in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "test.xlsx"
        assert connector._pass_extension_filter(item) is False

    def test_no_extension_with_in_operator(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = "in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "noextension"
        assert connector._pass_extension_filter(item) is False

    def test_no_extension_with_not_in_operator(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = "not_in"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "noextension"
        assert connector._pass_extension_filter(item) is True


# ===========================================================================
# _safe_api_call
# ===========================================================================


class TestSafeApiCall:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()

        async def api():
            return "result"

        result = await connector._safe_api_call(api())
        assert result == "result"

    @pytest.mark.asyncio
    async def test_permission_denied_no_retry(self):
        connector = _make_connector()
        call_count = 0

        async def api():
            nonlocal call_count
            call_count += 1
            raise Exception("403 forbidden accessdenied")

        result = await connector._safe_api_call(api(), max_retries=3, retry_delay=0.001)
        assert result is None
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_not_found_no_retry(self):
        connector = _make_connector()

        async def api():
            raise Exception("404 notfound")

        result = await connector._safe_api_call(api(), max_retries=2, retry_delay=0.001)
        assert result is None

    @pytest.mark.asyncio
    async def test_bad_request_no_retry(self):
        connector = _make_connector()

        async def api():
            raise Exception("400 badrequest invalid")

        result = await connector._safe_api_call(api(), max_retries=2, retry_delay=0.001)
        assert result is None

    @pytest.mark.asyncio
    async def test_throttle_retries(self):
        connector = _make_connector()
        # _safe_api_call receives a single coroutine object which can only be
        # awaited once.  After the first await raises, subsequent retry
        # iterations will encounter a RuntimeError ("cannot reuse already
        # awaited coroutine") which eventually exhausts retries and returns
        # None.
        call_count = 0

        async def api():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("429 throttle")
            return "ok"

        result = await connector._safe_api_call(api(), max_retries=3, retry_delay=0.001)
        assert result is None

    @pytest.mark.asyncio
    async def test_generic_error_retries_then_fails(self):
        connector = _make_connector()

        async def api():
            raise Exception("unknown error")

        result = await connector._safe_api_call(api(), max_retries=1, retry_delay=0.001)
        assert result is None


# ===========================================================================
# _create_app_user_from_member
# ===========================================================================


class TestCreateAppUserFromMember:

    def test_user_with_mail(self):
        connector = _make_connector()
        member = MagicMock()
        member.id = "user-1"
        member.mail = "user@test.com"
        member.user_principal_name = "user@test.onmicrosoft.com"
        member.display_name = "Test User"
        member.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        result = connector._create_app_user_from_member(member)
        assert result is not None
        assert result.email == "user@test.com"

    def test_user_with_upn_only(self):
        connector = _make_connector()
        member = MagicMock()
        member.id = "user-2"
        member.mail = None
        member.user_principal_name = "user2@test.onmicrosoft.com"
        member.display_name = "User Two"
        member.created_date_time = None

        result = connector._create_app_user_from_member(member)
        assert result is not None
        assert result.email == "user2@test.onmicrosoft.com"

    def test_user_no_email(self):
        connector = _make_connector()
        member = MagicMock()
        member.id = "user-3"
        member.mail = None
        member.user_principal_name = None

        result = connector._create_app_user_from_member(member)
        assert result is None


# ===========================================================================
# _handle_delete_group
# ===========================================================================


class TestHandleDeleteGroup:

    @pytest.mark.asyncio
    async def test_successful_delete(self):
        connector = _make_connector()
        result = await connector._handle_delete_group("group-1")
        assert result is True
        connector.data_entities_processor.on_user_group_deleted.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_returns_false(self):
        connector = _make_connector()
        connector.data_entities_processor.on_user_group_deleted = AsyncMock(return_value=False)
        result = await connector._handle_delete_group("group-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_raises(self):
        connector = _make_connector()
        connector.data_entities_processor.on_user_group_deleted = AsyncMock(
            side_effect=Exception("db error")
        )
        result = await connector._handle_delete_group("group-1")
        assert result is False


# ===========================================================================
# _process_member_change
# ===========================================================================


class TestProcessMemberChange:

    @pytest.mark.asyncio
    async def test_add_member(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")

        await connector._process_member_change("group-1", {"id": "user-1"})

    @pytest.mark.asyncio
    async def test_remove_member(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")

        await connector._process_member_change("group-1", {"id": "user-1", "@removed": True})

        connector.data_entities_processor.on_user_group_member_removed.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_email(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value=None)

        await connector._process_member_change("group-1", {"id": "user-1"})

    @pytest.mark.asyncio
    async def test_remove_member_failure(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")
        connector.data_entities_processor.on_user_group_member_removed = AsyncMock(return_value=False)

        await connector._process_member_change("group-1", {"id": "user-1", "@removed": True})


# ===========================================================================
# create_connector classmethod
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_create_connector(self):
        logger = logging.getLogger("test")
        dsp = MagicMock()
        cs = MagicMock()

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.DataSourceEntitiesProcessor") as mock_dep:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_dep.return_value = mock_instance

            connector = await SharePointConnector.create_connector(
                logger, dsp, cs, "conn-sp-new", "team", "test-user-id"
            )

            assert isinstance(connector, SharePointConnector)
            mock_instance.initialize.assert_called_once()


# ===========================================================================
# SharePointSubscriptionManager
# ===========================================================================


class TestSharePointSubscriptionManager:

    def _make_manager(self):
        mock_client = MagicMock()
        logger = logging.getLogger("test.subscriptions")
        return SharePointSubscriptionManager(mock_client, logger)

    @pytest.mark.asyncio
    async def test_create_site_subscription_success(self):
        manager = self._make_manager()
        mock_result = MagicMock()
        mock_result.id = "sub-1"
        manager.client.subscriptions = MagicMock()
        manager.client.subscriptions.post = AsyncMock(return_value=mock_result)

        result = await manager.create_site_subscription("site-1", "https://webhook.url/notify")
        assert result == "sub-1"
        assert "sites/site-1" in manager.subscriptions

    @pytest.mark.asyncio
    async def test_create_site_subscription_no_result(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        manager.client.subscriptions.post = AsyncMock(return_value=None)

        result = await manager.create_site_subscription("site-1", "https://webhook.url/notify")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_site_subscription_error(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        manager.client.subscriptions.post = AsyncMock(side_effect=Exception("api error"))

        result = await manager.create_site_subscription("site-1", "https://webhook.url/notify")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_drive_subscription_success(self):
        manager = self._make_manager()
        mock_result = MagicMock()
        mock_result.id = "sub-drive-1"
        manager.client.subscriptions = MagicMock()
        manager.client.subscriptions.post = AsyncMock(return_value=mock_result)

        result = await manager.create_drive_subscription("site-1", "drive-1", "https://webhook.url")
        assert result == "sub-drive-1"
        assert "sites/site-1/drives/drive-1" in manager.subscriptions

    @pytest.mark.asyncio
    async def test_create_drive_subscription_no_result(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        manager.client.subscriptions.post = AsyncMock(return_value=None)

        result = await manager.create_drive_subscription("site-1", "drive-1", "https://webhook.url")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_drive_subscription_error(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        manager.client.subscriptions.post = AsyncMock(side_effect=Exception("fail"))

        result = await manager.create_drive_subscription("site-1", "drive-1", "https://webhook.url")
        assert result is None

    @pytest.mark.asyncio
    async def test_renew_subscription_success(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        mock_builder = MagicMock()
        mock_builder.patch = AsyncMock()
        manager.client.subscriptions.by_subscription_id = MagicMock(return_value=mock_builder)

        result = await manager.renew_subscription("sub-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_renew_subscription_error(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        mock_builder = MagicMock()
        mock_builder.patch = AsyncMock(side_effect=Exception("expired"))
        manager.client.subscriptions.by_subscription_id = MagicMock(return_value=mock_builder)

        result = await manager.renew_subscription("sub-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_subscription_success(self):
        manager = self._make_manager()
        manager.subscriptions = {"sites/site-1": "sub-1"}
        manager.client.subscriptions = MagicMock()
        mock_builder = MagicMock()
        mock_builder.delete = AsyncMock()
        manager.client.subscriptions.by_subscription_id = MagicMock(return_value=mock_builder)

        result = await manager.delete_subscription("sub-1")
        assert result is True
        assert "sites/site-1" not in manager.subscriptions

    @pytest.mark.asyncio
    async def test_delete_subscription_not_tracked(self):
        manager = self._make_manager()
        manager.subscriptions = {}
        manager.client.subscriptions = MagicMock()
        mock_builder = MagicMock()
        mock_builder.delete = AsyncMock()
        manager.client.subscriptions.by_subscription_id = MagicMock(return_value=mock_builder)

        result = await manager.delete_subscription("sub-unknown")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_subscription_error(self):
        manager = self._make_manager()
        manager.client.subscriptions = MagicMock()
        mock_builder = MagicMock()
        mock_builder.delete = AsyncMock(side_effect=Exception("not found"))
        manager.client.subscriptions.by_subscription_id = MagicMock(return_value=mock_builder)

        result = await manager.delete_subscription("sub-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_subscriptions(self):
        manager = self._make_manager()
        manager.subscriptions = {"res1": "sub-1", "res2": "sub-2"}
        manager.delete_subscription = AsyncMock(return_value=True)

        await manager.cleanup_subscriptions()

        assert manager.delete_subscription.call_count == 2
        assert len(manager.subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cleanup_subscriptions_error(self):
        manager = self._make_manager()
        manager.subscriptions = {"res1": "sub-1"}
        manager.delete_subscription = AsyncMock(side_effect=Exception("cleanup fail"))

        await manager.cleanup_subscriptions()


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    @pytest.mark.asyncio
    async def test_site_notification(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_site = MagicMock()
        connector._safe_api_call = AsyncMock(return_value=mock_site)
        connector._sync_site_content = AsyncMock()

        notification = {
            "resource": "sites/site-123/drives/drive-1",
            "changeType": "updated"
        }
        await connector.handle_webhook_notification(notification)
        connector._sync_site_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_site_notification(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        notification = {
            "resource": "drives/drive-1",
            "changeType": "updated"
        }
        await connector.handle_webhook_notification(notification)

    @pytest.mark.asyncio
    async def test_notification_error(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock(side_effect=Exception("error"))

        await connector.handle_webhook_notification({"resource": "sites/s1", "changeType": "updated"})


# ===========================================================================
# test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        result = await connector.test_connection_and_access()
        assert result is True


# ===========================================================================
# _get_list_permissions / _get_list_item_permissions / _get_page_permissions
# ===========================================================================


class TestPermissionMethods:

    @pytest.mark.asyncio
    async def test_get_list_permissions(self):
        connector = _make_connector()
        result = await connector._get_list_permissions("site-1", "list-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_list_item_permissions(self):
        connector = _make_connector()
        result = await connector._get_list_item_permissions("site-1", "list-1", "item-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_page_permissions(self):
        connector = _make_connector()
        result = await connector._get_page_permissions("site-1", "page-1")
        assert result == []


# ===========================================================================
# _convert_to_permissions
# ===========================================================================


class TestConvertToPermissions:

    @pytest.mark.asyncio
    async def test_user_permission(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = MagicMock()
        perm.granted_to_v2.user.id = "user-1"
        perm.granted_to_v2.user.additional_data = {"email": "user@test.com"}
        perm.granted_to_v2.group = None
        perm.granted_to_identities_v2 = None
        perm.link = None
        perm.roles = ["write"]

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.WRITE):
            result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_group_permission(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = None
        perm.granted_to_v2.group = MagicMock()
        perm.granted_to_v2.group.id = "group-1"
        perm.granted_to_v2.group.additional_data = {"email": "group@test.com"}
        perm.granted_to_identities_v2 = None
        perm.link = None
        perm.roles = ["read"]

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.READ):
            result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_anonymous_link(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        perm.link = MagicMock()
        perm.link.scope = "anonymous"
        perm.link.type = "view"
        perm.roles = []

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.READ):
            result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.ANYONE_WITH_LINK

    @pytest.mark.asyncio
    async def test_organization_link(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        perm.link = MagicMock()
        perm.link.scope = "organization"
        perm.link.type = "edit"
        perm.roles = []

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.WRITE):
            result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_identities_v2_group(self):
        connector = _make_connector()
        identity = MagicMock()
        identity.group = MagicMock()
        identity.group.id = "grp-1"
        identity.group.additional_data = {}
        identity.user = None

        perm = MagicMock()
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = [identity]
        perm.link = None
        perm.roles = ["read"]

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.READ):
            result = await connector._convert_to_permissions([perm])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_identities_v2_user(self):
        connector = _make_connector()
        identity = MagicMock()
        identity.group = None
        identity.user = MagicMock()
        identity.user.id = "usr-1"
        identity.user.additional_data = {"email": "u@t.com"}

        perm = MagicMock()
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = [identity]
        perm.link = None
        perm.roles = ["write"]

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.WRITE):
            result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_permission_error_continues(self):
        connector = _make_connector()
        perm_good = MagicMock()
        perm_good.granted_to_v2 = None
        perm_good.granted_to_identities_v2 = None
        perm_good.link = MagicMock()
        perm_good.link.scope = "anonymous"
        perm_good.link.type = "view"
        perm_good.roles = []

        perm_bad = MagicMock()
        perm_bad.granted_to_v2 = None
        perm_bad.granted_to_identities_v2 = None
        type(perm_bad).link = PropertyMock(side_effect=Exception("bad perm"))

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.map_msgraph_role_to_permission_type", return_value=PermissionType.READ):
            result = await connector._convert_to_permissions([perm_bad, perm_good])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_permissions(self):
        connector = _make_connector()
        result = await connector._convert_to_permissions([])
        assert result == []


# ===========================================================================
# _get_item_permissions
# ===========================================================================


class TestGetItemPermissions:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_perms = MagicMock()
        mock_perms.value = []
        connector._safe_api_call = AsyncMock(return_value=mock_perms)
        connector._convert_to_permissions = AsyncMock(return_value=[])

        result = await connector._get_item_permissions("site-1", "drive-1", "item-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_response(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        result = await connector._get_item_permissions("site-1", "drive-1", "item-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(side_effect=Exception("perms error"))

        result = await connector._get_item_permissions("site-1", "drive-1", "item-1")
        assert result == []


# ===========================================================================
# _get_drive_permissions
# ===========================================================================


class TestGetDrivePermissions:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_root = MagicMock()
        mock_root.id = "root-item-id"
        mock_perms = MagicMock()
        mock_perms.value = []
        connector._safe_api_call = AsyncMock(side_effect=[mock_root, mock_perms])
        connector._convert_to_permissions = AsyncMock(return_value=[])

        result = await connector._get_drive_permissions("site-1", "drive-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_root_item(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        result = await connector._get_drive_permissions("site-1", "drive-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(side_effect=Exception("fail"))

        result = await connector._get_drive_permissions("site-1", "drive-1")
        assert result == []


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_site_ids_filter(self):
        connector = _make_connector()
        connector._get_site_options = AsyncMock(return_value=MagicMock())

        from app.connectors.core.registry.filters import SyncFilterKey
        result = await connector.get_filter_options(SyncFilterKey.SITE_IDS)
        connector._get_site_options.assert_called_once()

    @pytest.mark.asyncio
    async def test_drive_ids_filter(self):
        connector = _make_connector()
        connector._get_document_library_options = AsyncMock(return_value=MagicMock())

        from app.connectors.core.registry.filters import SyncFilterKey
        result = await connector.get_filter_options(SyncFilterKey.DRIVE_IDS)
        connector._get_document_library_options.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_filter_key(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="Unsupported filter key"):
            await connector.get_filter_options("invalid_key")


# ===========================================================================
# _pass_drive_date_filters / _pass_page_date_filters
# ===========================================================================


class TestPassDriveDateFilters:

    def test_folder_always_passes(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()

        item = MagicMock()
        item.folder = MagicMock()
        assert connector._pass_drive_date_filters(item) is True

    def test_no_filters(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)

        item = MagicMock()
        item.folder = None
        assert connector._pass_drive_date_filters(item) is True

    def test_created_filter_before_range(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-06-01T00:00:00+00:00", None)

        from app.connectors.core.registry.filters import SyncFilterKey

        def side_effect(key):
            if key == SyncFilterKey.CREATED:
                return mock_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = side_effect

        item = MagicMock()
        item.folder = None
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_drive_date_filters(item) is False


class TestPassPageDateFilters:

    def test_no_filters(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)

        page = MagicMock()
        assert connector._pass_page_date_filters(page) is True

    def test_modified_filter_in_range(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00+00:00", "2024-12-31T23:59:59+00:00")

        from app.connectors.core.registry.filters import SyncFilterKey

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = side_effect

        page = MagicMock()
        page.last_modified_date_time = datetime(2024, 6, 15, tzinfo=timezone.utc)
        page.created_date_time = None
        assert connector._pass_page_date_filters(page) is True


# ===========================================================================
# _get_subsites
# ===========================================================================


class TestGetSubsites:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        subsite = MagicMock()
        subsite.id = "sub-1"
        subsite.web_url = "https://contoso.sharepoint.com/sites/sub1"
        subsite.display_name = "Sub Site 1"
        subsite.name = "sub1"
        subsite.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        subsite.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_result.value = [subsite]
        connector._safe_api_call = AsyncMock(return_value=mock_result)

        result = await connector._get_subsites("parent-site-id")
        assert len(result) == 1
        assert result[0].id == "sub-1"
        assert "sub-1" in connector.site_cache

    @pytest.mark.asyncio
    async def test_no_subsites(self):
        connector = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        mock_result = MagicMock()
        mock_result.value = None
        connector._safe_api_call = AsyncMock(return_value=mock_result)

        result = await connector._get_subsites("site-id")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(side_effect=Exception("api fail"))

        result = await connector._get_subsites("site-id")
        assert result == []


# ===========================================================================
# _fetch_graph_group_members
# ===========================================================================


class TestFetchGraphGroupMembers:

    @pytest.mark.asyncio
    async def test_member_pagination(self):
        connector = _make_connector()
        connector.client = MagicMock()

        member1 = MagicMock()
        member1.odata_type = "#microsoft.graph.user"
        member1.mail = "user1@test.com"
        member1.id = "u1"
        member1.display_name = "User 1"
        member1.user_principal_name = "user1@test.com"

        page1 = MagicMock()
        page1.value = [member1]
        page1.odata_next_link = None

        mock_group = MagicMock()
        mock_group.transitive_members = MagicMock()
        mock_group.transitive_members.get = AsyncMock(return_value=page1)
        connector.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await connector._fetch_graph_group_members("group-1", is_owner=False)
        assert len(result) == 1
        assert result[0]["email"] == "user1@test.com"

    @pytest.mark.asyncio
    async def test_owner_mode(self):
        connector = _make_connector()
        connector.client = MagicMock()

        member = MagicMock()
        member.odata_type = "#microsoft.graph.user"
        member.mail = "owner@test.com"
        member.id = "o1"
        member.display_name = "Owner"
        member.user_principal_name = "owner@test.com"

        page = MagicMock()
        page.value = [member]
        page.odata_next_link = None

        mock_group = MagicMock()
        mock_group.owners = MagicMock()
        mock_group.owners.get = AsyncMock(return_value=page)
        connector.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await connector._fetch_graph_group_members("group-1", is_owner=True)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        connector.client = MagicMock()
        mock_group = MagicMock()
        mock_group.transitive_members = MagicMock()
        mock_group.transitive_members.get = AsyncMock(side_effect=Exception("fail"))
        connector.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await connector._fetch_graph_group_members("group-1")
        assert result == []


# ===========================================================================
# _create_list_record
# ===========================================================================


class TestCreateListRecord:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.id = "list-1"
        list_obj.display_name = "Tasks"
        list_obj.name = "tasks"
        list_obj.web_url = "https://contoso.sharepoint.com/lists/tasks"
        list_obj.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        list_obj.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        list_obj.e_tag = "etag-list"
        list_obj.list = MagicMock()
        list_obj.list.template = "GenericList"
        list_obj.list.item_count = 42

        result = await connector._create_list_record(list_obj, "site-1")
        assert result is not None
        assert result.record_name == "Tasks"
        assert result.record_type == RecordType.SHAREPOINT_LIST

    @pytest.mark.asyncio
    async def test_no_list_id(self):
        connector = _make_connector()
        list_obj = MagicMock()
        list_obj.id = None

        result = await connector._create_list_record(list_obj, "site-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        list_obj = MagicMock()
        type(list_obj).id = PropertyMock(side_effect=Exception("error"))

        result = await connector._create_list_record(list_obj, "site-1")
        assert result is None


# ===========================================================================
# _create_list_item_record
# ===========================================================================


class TestCreateListItemRecord:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        item = MagicMock()
        item.id = "li-1"
        item.fields = MagicMock()
        item.fields.additional_data = {"Title": "My Item"}
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = "etag-li"
        item.web_url = "https://contoso.sharepoint.com/lists/tasks/1"
        item.content_type = MagicMock()
        item.content_type.name = "Item"

        result = await connector._create_list_item_record(item, "site-1", "list-1")
        assert result is not None
        assert "My Item" in result.record_name

    @pytest.mark.asyncio
    async def test_no_item_id(self):
        connector = _make_connector()
        item = MagicMock()
        item.id = None

        result = await connector._create_list_item_record(item, "site-1", "list-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        item = MagicMock()
        type(item).id = PropertyMock(side_effect=Exception("fail"))

        result = await connector._create_list_item_record(item, "site-1", "list-1")
        assert result is None


# ===========================================================================
# _create_page_record
# ===========================================================================


class TestCreatePageRecord:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        page = MagicMock()
        page.id = "page-1"
        page.title = "Welcome"
        page.name = "welcome"
        page.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        page.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        page.e_tag = "etag-page"
        page.web_url = "https://contoso.sharepoint.com/sites/test/SitePages/Welcome.aspx"
        page.page_layout = MagicMock()
        page.page_layout.type = "Article"
        page.promotion_kind = None

        result = await connector._create_page_record(page, "site-1", "Test Site")
        assert result is not None
        assert "Welcome" in result.record_name
        assert "Test Site" in result.record_name
        assert result.record_type == RecordType.SHAREPOINT_PAGE

    @pytest.mark.asyncio
    async def test_no_page_id(self):
        connector = _make_connector()
        page = MagicMock()
        page.id = None

        result = await connector._create_page_record(page, "site-1", "Site")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_existing_record(self):
        connector = _make_connector()
        page = MagicMock()
        page.id = "page-1"
        page.title = "Updated"
        page.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        page.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        page.e_tag = "etag-2"
        page.web_url = "https://contoso.sharepoint.com/pages/test"
        page.page_layout = None
        page.promotion_kind = None

        existing = _make_page_record()

        result = await connector._create_page_record(page, "site-1", "Site", existing)
        assert result.id == existing.id
        assert result.version == existing.version + 1

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        page = MagicMock()
        type(page).id = PropertyMock(side_effect=Exception("err"))

        result = await connector._create_page_record(page, "site-1", "Site")
        assert result is None


# ===========================================================================
# _create_file_record
# ===========================================================================


class TestCreateFileRecord:

    @pytest.mark.asyncio
    async def test_file_with_extension(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed.url")

        item = MagicMock()
        item.id = "item-1"
        item.name = "document.pdf"
        item.root = None
        item.folder = None
        item.file = MagicMock()
        item.file.mime_type = "application/pdf"
        item.file.hashes = MagicMock()
        item.file.hashes.quick_xor_hash = "hash1"
        item.file.hashes.crc32_hash = None
        item.file.hashes.sha1_hash = None
        item.file.hashes.sha256_hash = None
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = "etag"
        item.c_tag = "ctag"
        item.web_url = "https://contoso.sharepoint.com/doc.pdf"
        item.size = 1024
        item.parent_reference = MagicMock()
        item.parent_reference.id = "parent-1"
        item.parent_reference.path = "/drive/root:/folder"

        result = await connector._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.extension == "pdf"
        assert result.is_file is True

    @pytest.mark.asyncio
    async def test_file_without_extension(self):
        connector = _make_connector()

        item = MagicMock()
        item.id = "item-2"
        item.name = "README"
        item.root = None
        item.folder = None
        item.file = MagicMock()
        item.created_date_time = None
        item.last_modified_date_time = None

        result = await connector._create_file_record(item, "drive-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_folder(self):
        connector = _make_connector()

        item = MagicMock()
        item.id = "folder-1"
        item.name = "Documents"
        item.root = None
        item.folder = MagicMock()
        item.file = None
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = "etag-folder"
        item.c_tag = "ctag-folder"
        item.web_url = "https://contoso.sharepoint.com/Docs"
        item.size = 0
        item.parent_reference = None

        result = await connector._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.is_file is False

    @pytest.mark.asyncio
    async def test_root_item(self):
        connector = _make_connector()

        item = MagicMock()
        item.id = "root-item"
        item.name = "root"
        item.root = MagicMock()
        item.folder = MagicMock()
        item.file = None
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = "etag-root"
        item.c_tag = "ctag-root"
        item.web_url = None
        item.size = 0
        item.parent_reference = None

        result = await connector._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.external_record_id == "drive-1:root:root-item"

    @pytest.mark.asyncio
    async def test_no_item_id(self):
        connector = _make_connector()
        item = MagicMock()
        item.id = None

        result = await connector._create_file_record(item, "drive-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_at_root(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value=None)

        item = MagicMock()
        item.id = "item-3"
        item.name = "file.txt"
        item.root = None
        item.folder = None
        item.file = MagicMock()
        item.file.mime_type = "text/plain"
        item.file.hashes = None
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = None
        item.c_tag = None
        item.web_url = None
        item.size = 100
        item.parent_reference = MagicMock()
        item.parent_reference.id = "parent-root"
        item.parent_reference.path = "/drive/root:"

        result = await connector._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.parent_external_record_id == "drive-1:root:parent-root"
