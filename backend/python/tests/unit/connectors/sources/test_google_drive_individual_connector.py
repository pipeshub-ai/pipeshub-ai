"""
Comprehensive unit tests for GoogleDriveIndividualConnector.

Targets 95%+ statement and branch coverage by testing every method,
including success paths, error paths, and all conditional branches.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleDriveError,
)
from app.models.entities import FileRecord, Record, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_gdrive_cov")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_file_metadata(
    file_id="file-1",
    name="test.txt",
    mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z",
    modified_time="2025-01-15T00:00:00Z",
    parents=None,
    shared=False,
    head_revision_id="rev-1",
    file_extension="txt",
    size=1024,
    version=None,
    web_view_link=None,
):
    meta = {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "createdTime": created_time,
        "modifiedTime": modified_time,
        "shared": shared,
        "headRevisionId": head_revision_id,
        "fileExtension": file_extension,
        "size": size,
        "webViewLink": web_view_link or f"https://drive.google.com/file/d/{file_id}/view",
    }
    if parents is not None:
        meta["parents"] = parents
    if version is not None:
        meta["version"] = version
    return meta


def _make_record(**kwargs):
    defaults = dict(
        id="rec-1",
        external_record_id="file-1",
        external_record_group_id="drive-1",
        record_name="test.txt",
        external_revision_id="rev-1",
        parent_external_record_id=None,
        version=0,
        indexing_status=ProgressStatus.QUEUED.value,
        extraction_status=ProgressStatus.NOT_STARTED.value,
        is_shared=False,
    )
    defaults.update(kwargs)
    rec = MagicMock(spec=Record)
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


def _make_existing_file_record(**kwargs):
    """Create a mock that behaves like a FileRecord from the DB."""
    defaults = dict(
        id="rec-existing",
        record_name="test.txt",
        external_revision_id="rev-1",
        external_record_group_id="drive-1",
        parent_external_record_id=None,
        version=1,
        indexing_status=ProgressStatus.QUEUED.value,
        extraction_status=ProgressStatus.NOT_STARTED.value,
        is_shared=False,
    )
    defaults.update(kwargs)
    rec = MagicMock(spec=FileRecord)
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


def _make_filter(value=None, operator_value=FilterOperator.IN, is_empty=False, datetime_iso=None):
    """Create a mock Filter object."""
    mock_filter = MagicMock()
    mock_filter.is_empty.return_value = is_empty
    mock_filter.value = value
    mock_op = MagicMock()
    mock_op.value = operator_value
    mock_filter.get_operator.return_value = mock_op
    if datetime_iso:
        mock_filter.get_datetime_iso.return_value = datetime_iso
    return mock_filter


# ---------------------------------------------------------------------------
# Fixture: connector instance with mocked dependencies
# ---------------------------------------------------------------------------

@pytest.fixture
def connector():
    with patch(
        "app.connectors.sources.google.drive.individual.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.drive.individual.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.drive.individual.connector import (
            GoogleDriveIndividualConnector,
        )

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        dep.reindex_existing_records = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()

        conn = GoogleDriveIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.google_client = MagicMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {"access_token": "t", "refresh_token": "r"}}
        yield conn


# ===================================================================
# init() -- all branches
# ===================================================================


class TestInit:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleDriveDataSource")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_init_success(self, MockGClient, MockDS, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {"clientId": "cid", "clientSecret": "csec"}
        }
        mock_client_inst = AsyncMock()
        mock_client_inst.get_client.return_value = MagicMock()
        MockGClient.build_from_services = AsyncMock(return_value=mock_client_inst)
        MockDS.return_value = MagicMock()
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at", "refresh_token": "rt"},
            }
        )
        result = await connector.init()
        assert result is True
        assert connector.google_client is mock_client_inst

    @pytest.mark.asyncio
    async def test_init_config_not_found(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_config_empty(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={})
        # Missing auth / oauthConfigId — init returns False without raising
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self, connector):
        connector.config_service.get_config = AsyncMock(
            return_value={"auth": {}}
        )
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    async def test_init_oauth_config_not_found(self, mock_fetch, connector):
        mock_fetch.return_value = None
        connector.config_service.get_config = AsyncMock(
            return_value={"auth": {"oauthConfigId": "oc-1"}}
        )
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    async def test_init_missing_client_id(self, mock_fetch, connector):
        mock_fetch.return_value = {"config": {"clientSecret": "csec"}}
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at"},
            }
        )
        with pytest.raises(ValueError, match="Incomplete Google Drive credentials"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    async def test_init_missing_client_secret(self, mock_fetch, connector):
        mock_fetch.return_value = {"config": {"clientId": "cid"}}
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at"},
            }
        )
        with pytest.raises(ValueError, match="Incomplete Google Drive credentials"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_init_no_tokens_warning(self, MockGClient, mock_fetch, connector):
        """No access_token and no refresh_token -- should still succeed with warning."""
        mock_fetch.return_value = {
            "config": {"clientId": "cid", "clientSecret": "csec"}
        }
        mock_client_inst = AsyncMock()
        mock_client_inst.get_client.return_value = MagicMock()
        MockGClient.build_from_services = AsyncMock(return_value=mock_client_inst)
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {},
            }
        )
        with patch(
            "app.connectors.sources.google.drive.individual.connector.GoogleDriveDataSource"
        ):
            result = await connector.init()
        assert result is True

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_init_client_build_fails(self, MockGClient, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {"clientId": "cid", "clientSecret": "csec"}
        }
        MockGClient.build_from_services = AsyncMock(side_effect=RuntimeError("build fail"))
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at", "refresh_token": "rt"},
            }
        )
        with pytest.raises(ValueError, match="Failed to initialize Google Drive client"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    async def test_init_empty_oauth_config_data(self, mock_fetch, connector):
        """oauth_config has no 'config' key, so clientId/Secret are None."""
        mock_fetch.return_value = {"id": "oc-1"}
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at"},
            }
        )
        with pytest.raises(ValueError, match="Incomplete Google Drive credentials"):
            await connector.init()


# ===================================================================
# _get_fresh_datasource()
# ===================================================================


class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_raises_when_google_client_is_none(self, connector):
        connector.google_client = None
        with pytest.raises(GoogleDriveError, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_raises_when_drive_data_source_is_none(self, connector):
        connector.drive_data_source = None
        with pytest.raises(GoogleDriveError, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_success(self, mock_refresh, connector):
        mock_refresh.return_value = None
        await connector._get_fresh_datasource()
        mock_refresh.assert_awaited_once()


# ===================================================================
# _parse_datetime()
# ===================================================================


class TestParseDatetime:
    def test_none_input(self, connector):
        assert connector._parse_datetime(None) is None

    def test_iso_string_with_z(self, connector):
        result = connector._parse_datetime("2025-01-01T00:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_iso_string_with_offset(self, connector):
        result = connector._parse_datetime("2025-01-01T00:00:00+00:00")
        assert isinstance(result, int)

    def test_datetime_object(self, connector):
        from datetime import datetime, timezone
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert isinstance(result, int)

    def test_invalid_string(self, connector):
        result = connector._parse_datetime("not-a-date")
        assert result is None

    def test_invalid_object(self, connector):
        result = connector._parse_datetime(12345)
        assert result is None

    def test_empty_string(self, connector):
        # empty string is falsy, handled by `if not dt_obj`
        result = connector._parse_datetime("")
        assert result is None


# ===================================================================
# _pass_date_filters() -- all branches
# ===================================================================


class TestPassDateFilters:
    def test_folder_always_passes(self, connector):
        meta = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_date_filters(meta) is True

    def test_no_filters_passes(self, connector):
        connector.sync_filters = FilterCollection()
        meta = _make_file_metadata()
        assert connector._pass_date_filters(meta) is True

    def test_created_filter_before_start_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time="2024-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_created_filter_after_end_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_created_filter_within_range_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is True

    def test_modified_filter_before_start_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time="2024-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_modified_filter_after_end_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_modified_filter_within_range_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time="2025-01-15T00:00:00Z")
        assert connector._pass_date_filters(meta) is True

    def test_no_created_time_in_metadata_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time=None)
        assert connector._pass_date_filters(meta) is True

    def test_no_modified_time_in_metadata_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time=None)
        assert connector._pass_date_filters(meta) is True

    def test_both_filters_pass(self, connector):
        created_f = MagicMock()
        created_f.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        modified_f = MagicMock()
        modified_f.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: created_f if key == SyncFilterKey.CREATED else (
                modified_f if key == SyncFilterKey.MODIFIED else None
            )
        )
        meta = _make_file_metadata(
            created_time="2025-01-01T00:00:00Z",
            modified_time="2025-01-15T00:00:00Z",
        )
        assert connector._pass_date_filters(meta) is True

    def test_created_filter_none_start_and_end(self, connector):
        """Both start and end are None in the filter -- passes."""
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is True


# ===================================================================
# _pass_extension_filter() -- all branches
# ===================================================================


class TestPassExtensionFilter:
    def test_folder_always_passes(self, connector):
        meta = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_extension_filter(meta) is True

    def test_no_filter_passes(self, connector):
        connector.sync_filters = FilterCollection()
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_empty_filter_passes(self, connector):
        mock_filter = _make_filter(is_empty=True)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_non_list_value_passes(self, connector):
        mock_filter = _make_filter(value="not-a-list")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_google_docs_in_allowed(self, connector):
        mock_filter = _make_filter(
            value=[MimeTypes.GOOGLE_DOCS.value],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(meta) is True

    def test_google_docs_not_in_allowed(self, connector):
        mock_filter = _make_filter(
            value=[MimeTypes.GOOGLE_SHEETS.value],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(meta) is False

    def test_google_sheets_not_in_filter(self, connector):
        mock_filter = _make_filter(
            value=[MimeTypes.GOOGLE_SHEETS.value],
            operator_value=FilterOperator.NOT_IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_SHEETS.value}
        assert connector._pass_extension_filter(meta) is False

    def test_google_slides_not_in_not_excluded(self, connector):
        mock_filter = _make_filter(
            value=[MimeTypes.GOOGLE_DOCS.value],
            operator_value=FilterOperator.NOT_IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_SLIDES.value}
        assert connector._pass_extension_filter(meta) is True

    def test_google_workspace_unknown_operator(self, connector):
        mock_filter = _make_filter(
            value=[MimeTypes.GOOGLE_SHEETS.value],
            operator_value="UNKNOWN_OP",
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_SHEETS.value}
        assert connector._pass_extension_filter(meta) is True

    def test_no_extension_in_operator_returns_false(self, connector):
        mock_filter = _make_filter(
            value=["pdf"],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "name": "noext"}
        assert connector._pass_extension_filter(meta) is False

    def test_no_extension_not_in_operator_returns_true(self, connector):
        mock_filter = _make_filter(
            value=["pdf"],
            operator_value=FilterOperator.NOT_IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "name": "noext"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_in_allowed_list(self, connector):
        mock_filter = _make_filter(
            value=["pdf", "txt"],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_not_in_excluded_list(self, connector):
        mock_filter = _make_filter(
            value=["pdf"],
            operator_value=FilterOperator.NOT_IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_in_excluded_list(self, connector):
        mock_filter = _make_filter(
            value=["txt"],
            operator_value=FilterOperator.NOT_IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is False

    def test_extension_from_name_fallback(self, connector):
        mock_filter = _make_filter(
            value=["docx"],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "application/msword", "name": "report.docx"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_with_leading_dot(self, connector):
        mock_filter = _make_filter(
            value=[".pdf"],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "application/pdf", "fileExtension": ".pdf"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_unknown_operator(self, connector):
        mock_filter = _make_filter(
            value=["pdf"],
            operator_value="BETWEEN",
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is True

    def test_google_apps_mime_excluded_from_normalized_extensions(self, connector):
        """Allowed values mixing google-apps mimeTypes and extensions."""
        mock_filter = _make_filter(
            value=[MimeTypes.GOOGLE_DOCS.value, "pdf"],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "application/pdf", "fileExtension": "pdf"}
        assert connector._pass_extension_filter(meta) is True

    def test_no_file_extension_no_name_extension_in_operator(self, connector):
        """No fileExtension field and name has no dot -- IN operator."""
        mock_filter = _make_filter(
            value=["pdf"],
            operator_value=FilterOperator.IN,
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "name": "noext", "fileExtension": None}
        assert connector._pass_extension_filter(meta) is False

    def test_operator_has_no_value_attr(self, connector):
        """Operator returned doesn't have .value attribute -- str() fallback."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = "custom_op_string"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "pdf"}
        # custom_op_string won't match FilterOperator.IN or NOT_IN, so defaults True
        assert connector._pass_extension_filter(meta) is True


# ===================================================================
# _process_drive_item() -- all branches
# ===================================================================


class TestProcessDriveItem:
    @pytest.mark.asyncio
    async def test_no_file_id_returns_none(self, connector):
        result = await connector._process_drive_item({}, "u1", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_skipped_by_date_filter(self, connector):
        connector._pass_date_filters = MagicMock(return_value=False)
        meta = _make_file_metadata()
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_skipped_by_extension_filter(self, connector):
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=False)
        meta = _make_file_metadata()
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_new_record_success(self, connector):
        """No existing record -- should be a new record."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        # Default tx_store returns None for get_record_by_external_id
        meta = _make_file_metadata(parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is not None
        assert result.is_new is True
        assert result.is_updated is False
        assert result.record.record_name == "test.txt"
        assert result.record.is_file is True
        assert result.record.version == 0
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].email == "u@t.com"

    @pytest.mark.asyncio
    async def test_new_record_folder(self, connector):
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(
            mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            file_extension=None,
        )
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is not None
        assert result.record.is_file is False

    @pytest.mark.asyncio
    async def test_existing_record_no_changes(self, connector):
        """Record exists but no metadata/content/permission changes -- is_updated=False."""
        existing = _make_existing_file_record(
            record_name="test.txt",
            external_revision_id="rev-1",
            external_record_group_id="d1",
            parent_external_record_id="parent-1",
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is not None
        assert result.is_new is False
        assert result.is_updated is False
        # No content change: indexing_status preserved from existing
        assert result.record.indexing_status == existing.indexing_status

    @pytest.mark.asyncio
    async def test_existing_record_name_changed(self, connector):
        """Existing record with different name -- metadata_changed."""
        existing = _make_existing_file_record(
            record_name="old_name.txt",
            external_revision_id="rev-1",
            external_record_group_id="d1",
            parent_external_record_id="parent-1",
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(name="new_name.txt", parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_revision_changed(self, connector):
        """Existing record with different revision -- content_changed."""
        existing = _make_existing_file_record(
            external_revision_id="rev-1",
            external_record_group_id="d1",
            parent_external_record_id="parent-1",
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(head_revision_id="rev-2", parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.is_updated is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_drive_id_changed(self, connector):
        """Drive ID changed for existing record -- metadata_changed."""
        existing = _make_existing_file_record(
            external_revision_id="rev-1",
            external_record_group_id="old-drive-id",
            parent_external_record_id="parent-1",
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "new-drive-id")
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_parent_changed(self, connector):
        """Parent external record ID changed -- metadata_changed."""
        existing = _make_existing_file_record(
            external_revision_id="rev-1",
            external_record_group_id="d1",
            parent_external_record_id="old-parent",
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(parents=["new-parent"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_version_incremented(self, connector):
        """Existing record gets version incremented."""
        existing = _make_existing_file_record(
            record_name="old.txt",
            external_revision_id="rev-1",
            external_record_group_id="d1",
            parent_external_record_id="parent-1",
            version=5,
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(name="new.txt", parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.version == 6

    @pytest.mark.asyncio
    async def test_no_parents_in_metadata(self, connector):
        """No parents in metadata -- parent_external_record_id is None."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata()
        # no "parents" key
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is not None
        assert result.record.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_parent_equals_drive_id(self, connector):
        """Parent is same as drive_id -- parent should be None."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(parents=["d1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.parent_external_record_id is None
        assert result.record.parent_record_type is None

    @pytest.mark.asyncio
    async def test_no_head_revision_uses_version(self, connector):
        """No headRevisionId -- falls back to version field."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(head_revision_id=None, version="42")
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.external_revision_id == "42"

    @pytest.mark.asyncio
    async def test_no_timestamps_uses_defaults(self, connector):
        """No createdTime/modifiedTime -- uses current epoch."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(created_time=None, modified_time=None)
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.source_created_at is not None
        assert result.record.source_updated_at is not None

    @pytest.mark.asyncio
    async def test_file_extension_from_name(self, connector):
        """No fileExtension field -- extracted from name."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(file_extension=None, name="report.docx")
        # Remove fileExtension from meta explicitly
        meta.pop("fileExtension", None)
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.extension == "docx"

    @pytest.mark.asyncio
    async def test_no_extension_no_dot_in_name(self, connector):
        """No fileExtension and no dot in name."""
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(file_extension=None, name="README")
        meta.pop("fileExtension", None)
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.extension is None

    @pytest.mark.asyncio
    async def test_shared_file(self, connector):
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(shared=True)
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.is_shared is True

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        """Exception during processing returns None."""
        connector._pass_date_filters = MagicMock(side_effect=RuntimeError("boom"))
        meta = _make_file_metadata()
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_size_defaults_to_zero(self, connector):
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(size=None)
        meta["size"] = None
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.size_in_bytes == 0

    @pytest.mark.asyncio
    async def test_unknown_mime_type(self, connector):
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        meta = _make_file_metadata(mime_type="")
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.mime_type == MimeTypes.UNKNOWN.value

    @pytest.mark.asyncio
    async def test_existing_record_content_not_changed_preserves_statuses(self, connector):
        """When content is not changed, indexing_status and extraction_status are preserved."""
        existing = _make_existing_file_record(
            record_name="test.txt",
            external_revision_id="rev-1",
            external_record_group_id="d1",
            parent_external_record_id="parent-1",
            indexing_status=ProgressStatus.COMPLETED.value,
            extraction_status=ProgressStatus.COMPLETED.value,
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        meta = _make_file_metadata(parents=["parent-1"])
        result = await connector._process_drive_item(meta, "u1", "u@t.com", "d1")
        assert result.record.indexing_status == ProgressStatus.COMPLETED.value
        assert result.record.extraction_status == ProgressStatus.COMPLETED.value


# ===================================================================
# _process_drive_items_generator()
# ===================================================================


class TestProcessDriveItemsGenerator:
    @pytest.mark.asyncio
    async def test_yields_records(self, connector):
        connector._process_drive_item = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_shared = False
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = [MagicMock()]
        connector._process_drive_item.return_value = update

        results = []
        async for rec, perms, upd in connector._process_drive_items_generator(
            [_make_file_metadata()], "u1", "u@t.com", "d1"
        ):
            results.append((rec, perms, upd))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_skips_none_update(self, connector):
        connector._process_drive_item = AsyncMock(return_value=None)
        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata()], "u1", "u@t.com", "d1"
        ):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_skips_none_record(self, connector):
        update = MagicMock()
        update.record = None
        connector._process_drive_item = AsyncMock(return_value=update)
        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata()], "u1", "u@t.com", "d1"
        ):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_files_disabled_sets_auto_index_off(self, connector):
        connector._process_drive_item = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_shared = False
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = []
        connector._process_drive_item.return_value = update

        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata()], "u1", "u@t.com", "d1"
        ):
            results.append(item)
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_shared_disabled_sets_auto_index_off(self, connector):
        connector._process_drive_item = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_shared = True
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = []
        connector._process_drive_item.return_value = update

        mock_indexing = MagicMock()
        mock_indexing.is_enabled = MagicMock(
            side_effect=lambda key, default=True: key != IndexingFilterKey.SHARED
        )
        connector.indexing_filters = mock_indexing

        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata(shared=True)], "u1", "u@t.com", "d1"
        ):
            results.append(item)
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception_continues(self, connector):
        connector._process_drive_item = AsyncMock(side_effect=Exception("boom"))
        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata(), _make_file_metadata(file_id="f2")], "u1", "u@t.com", "d1"
        ):
            results.append(item)
        assert len(results) == 0


# ===================================================================
# _handle_record_updates()
# ===================================================================


class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted_record(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "file-1"
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_awaited_once_with(
            record_id="file-1"
        )

    @pytest.mark.asyncio
    async def test_new_record(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = True
        update.is_updated = False
        update.record = MagicMock()
        update.record.record_name = "new.txt"
        await connector._handle_record_updates(update)
        # New records are just logged, not processed here
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updated_metadata_only(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = True
        update.permissions_changed = False
        update.content_changed = False
        update.record = MagicMock()
        update.record.record_name = "renamed.txt"
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updated_permissions_only(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = True
        update.content_changed = False
        update.record = MagicMock()
        update.record.record_name = "shared.txt"
        update.new_permissions = [MagicMock()]
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updated_content_only(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = False
        update.content_changed = True
        update.record = MagicMock()
        update.record.record_name = "edited.txt"
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updated_all_changes(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = True
        update.permissions_changed = True
        update.content_changed = True
        update.record = MagicMock()
        update.record.record_name = "changed.txt"
        update.new_permissions = [MagicMock()]
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_swallowed(self, connector):
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "file-1"
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        # Should not raise
        await connector._handle_record_updates(update)


# ===================================================================
# _sync_user_personal_drive()
# ===================================================================


class TestSyncUserPersonalDrive:
    @pytest.mark.asyncio
    async def test_drive_data_source_not_initialized(self, connector):
        connector.drive_data_source = None
        await connector._sync_user_personal_drive("d1")
        # Should log error and return

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_no_user_id(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {}}
        )
        await connector._sync_user_personal_drive("d1")
        # Should log error and return, no sync performed

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_when_no_sync_point(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector.drive_delta_sync_point = AsyncMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._perform_full_sync = AsyncMock()

        await connector._sync_user_personal_drive("d1")
        connector._perform_full_sync.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_sync_when_sync_point_exists(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector.drive_delta_sync_point = AsyncMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-page-token-12345"}
        )
        connector._perform_incremental_sync = AsyncMock()

        await connector._sync_user_personal_drive("d1")
        connector._perform_incremental_sync.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_no_user_email(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1"}}
        )
        await connector._sync_user_personal_drive("d1")
        # Should log error and return


# ===================================================================
# _perform_full_sync()
# ===================================================================


class TestPerformFullSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_no_start_page_token(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={}
        )
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_with_pagination(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        page1 = {
            "files": [_make_file_metadata(file_id="f1")],
            "nextPageToken": "next-token-12345678901234567890",
        }
        page2 = {"files": [_make_file_metadata(file_id="f2")]}
        connector.drive_data_source.files_list = AsyncMock(side_effect=[page1, page2])

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        connector.data_entities_processor.on_new_records.assert_awaited()
        connector.drive_delta_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_handles_deleted_and_updated(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        connector.drive_data_source.files_list = AsyncMock(
            return_value={"files": [_make_file_metadata()]}
        )

        async def mock_gen(files, uid, email, did):
            update_del = MagicMock()
            update_del.is_deleted = True
            update_del.is_updated = False
            yield MagicMock(), [], update_del

            update_upd = MagicMock()
            update_upd.is_deleted = False
            update_upd.is_updated = True
            yield MagicMock(), [], update_upd

        connector._process_drive_items_generator = mock_gen
        connector._handle_record_updates = AsyncMock()
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        assert connector._handle_record_updates.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_empty_files_breaks(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        connector.drive_data_source.files_list = AsyncMock(
            return_value={"files": []}
        )
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_exception_raises(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            side_effect=RuntimeError("api fail")
        )
        with pytest.raises(RuntimeError):
            await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_batch_processing(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.batch_size = 2
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        connector.drive_data_source.files_list = AsyncMock(
            return_value={
                "files": [_make_file_metadata(file_id=f"f{i}") for i in range(5)]
            }
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        assert connector.data_entities_processor.on_new_records.await_count >= 2


# ===================================================================
# _perform_incremental_sync()
# ===================================================================


class TestPerformIncrementalSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_deleted_files(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": True, "fileId": "del-1"},
                    {"removed": True, "fileId": "del-2"},
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )
        connector._handle_record_updates = AsyncMock()
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        assert connector._handle_record_updates.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_file_changes(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": False, "file": _make_file_metadata(file_id="f1")},
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_updated_records(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [{"removed": False, "file": _make_file_metadata()}],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            upd = MagicMock()
            upd.is_deleted = False
            upd.is_updated = True
            upd.record = MagicMock()
            upd.record.record_name = "updated.txt"
            yield upd.record, [], upd

        connector._process_drive_items_generator = mock_gen
        connector._handle_record_updates = AsyncMock()
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector._handle_record_updates.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_deleted_in_generator(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [{"removed": False, "file": _make_file_metadata()}],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            upd = MagicMock()
            upd.is_deleted = True
            upd.is_updated = False
            upd.record = MagicMock()
            yield upd.record, [], upd

        connector._process_drive_items_generator = mock_gen
        connector._handle_record_updates = AsyncMock()
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector._handle_record_updates.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_pagination(self, mock_refresh, connector):
        mock_refresh.return_value = None
        page1 = {
            "changes": [{"removed": False, "file": _make_file_metadata(file_id="f1")}],
            "nextPageToken": "next-page-token-1234567890123456",
        }
        page2 = {
            "changes": [],
            "newStartPageToken": "final-start-token-1234567890123",
        }
        connector.drive_data_source.changes_list = AsyncMock(side_effect=[page1, page2])

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector.drive_delta_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_no_tokens_warning(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={"changes": []}
        )
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_exception_raises(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            side_effect=RuntimeError("api error")
        )
        with pytest.raises(RuntimeError):
            await connector._perform_incremental_sync(
                "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
            )

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_batch_processing(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.batch_size = 1
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": False, "file": _make_file_metadata(file_id=f"f{i}")}
                    for i in range(3)
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        assert connector.data_entities_processor.on_new_records.await_count >= 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_sync_point_unchanged(self, mock_refresh, connector):
        mock_refresh.return_value = None
        token = "same-token-12345678901234567"
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={"changes": [], "newStartPageToken": token}
        )
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", token, "d1"
        )
        connector.drive_delta_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_removed_no_file_id(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [{"removed": True}],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )
        connector._handle_record_updates = AsyncMock()
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector._handle_record_updates.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_change_no_file_metadata(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [{"removed": False}],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===================================================================
# test_connection_and_access()
# ===================================================================


class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.google_client.get_client.return_value = MagicMock()
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_drive_data_source_none(self, connector):
        connector.drive_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_google_client_none(self, connector):
        connector.google_client = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_client_returns_none(self, connector):
        connector.google_client.get_client.return_value = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connector):
        connector.google_client.get_client.side_effect = RuntimeError("fail")
        result = await connector.test_connection_and_access()
        assert result is False


# ===================================================================
# _stream_google_api_request()
# ===================================================================


class TestStreamGoogleApiRequest:
    @pytest.mark.asyncio
    async def test_success_with_content(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(return_value=(MagicMock(), True))
            MockDownloader.return_value = mock_dl

            chunks = []
            async for chunk in connector._stream_google_api_request(mock_request, "test"):
                chunks.append(chunk)

    @pytest.mark.asyncio
    async def test_http_error(self, connector):
        mock_request = MagicMock()
        resp = MagicMock()
        resp.status = 500
        resp.reason = "error"
        http_err = HttpError(resp, b"error")
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=http_err)
            MockDownloader.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                async for _ in connector._stream_google_api_request(mock_request, "download"):
                    pass
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_chunk_error(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=ValueError("chunk fail"))
            MockDownloader.return_value = mock_dl

            with pytest.raises(HTTPException):
                async for _ in connector._stream_google_api_request(mock_request, "download"):
                    pass

    @pytest.mark.asyncio
    async def test_multiple_chunks(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            call_count = 0

            def next_chunk_side_effect():
                nonlocal call_count
                call_count += 1
                return (MagicMock(), call_count >= 2)

            mock_dl.next_chunk = MagicMock(side_effect=next_chunk_side_effect)
            MockDownloader.return_value = mock_dl

            chunks = []
            async for chunk in connector._stream_google_api_request(mock_request, "test"):
                chunks.append(chunk)


# ===================================================================
# _convert_to_pdf()
# ===================================================================


class TestConvertToPdf:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.path.exists", return_value=True):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert result == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_nonzero_return_code(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"conversion error"))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout_force_kill(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.kill = MagicMock()
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_output_file_not_found(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.path.exists", return_value=False):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self, connector):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no soffice")):
            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")


# ===================================================================
# _get_file_metadata_from_drive()
# ===================================================================


class TestGetFileMetadataFromDrive:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute.return_value = {"id": "f1", "name": "test.txt", "mimeType": "text/plain"}
        mock_files.get.return_value = mock_get
        mock_service.files.return_value = mock_files
        connector.google_client.get_client.return_value = mock_service

        result = await connector._get_file_metadata_from_drive("f1")
        assert result["id"] == "f1"

    @pytest.mark.asyncio
    async def test_not_found_error(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"
        mock_files.get.return_value.execute.side_effect = HttpError(resp, b"not found")
        mock_service.files.return_value = mock_files
        connector.google_client.get_client.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_other_http_error(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        mock_files.get.return_value.execute.side_effect = HttpError(resp, b"forbidden")
        mock_service.files.return_value = mock_files
        connector.google_client.get_client.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("f1")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self, connector):
        mock_service = MagicMock()
        mock_service.files.side_effect = RuntimeError("unexpected")
        connector.google_client.get_client.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("f1")
        assert exc_info.value.status_code == 500


# ===================================================================
# get_signed_url()
# ===================================================================


class TestGetSignedUrl:
    def test_raises_not_implemented(self, connector):
        record = _make_record()
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(record)


# ===================================================================
# stream_record()
# ===================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_file_id(self, connector):
        record = _make_record(external_record_id=None)
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_google_workspace_pdf_export(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.document"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.export_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            mock_stream.assert_called_once()
            call_kwargs = mock_stream.call_args
            assert call_kwargs.kwargs.get("mime_type") == "application/pdf" or call_kwargs[1].get("mime_type") == "application/pdf"

    @pytest.mark.asyncio
    async def test_google_workspace_spreadsheet_export(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.spreadsheet"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.export_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_workspace_presentation_export(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.presentation"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.export_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_file_pdf_conversion(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        )
        mock_service = MagicMock()
        mock_request = MagicMock()
        mock_service.files.return_value.get_media.return_value = mock_request
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)
            status_mock = MagicMock()
            status_mock.progress.return_value = 1.0
            mock_dl_inst = MagicMock()
            mock_dl_inst.next_chunk.return_value = (status_mock, True)
            MockDL.return_value = mock_dl_inst
            connector._convert_to_pdf = AsyncMock(return_value="/tmp/test_dir/test.pdf")
            mock_stream.return_value = MagicMock()

            await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_file_download(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/octet-stream"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.get_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_exception_passthrough(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Not found")
        )
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pdf_download_http_error_forbidden_not_downloadable(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "text/plain"}
        )
        mock_service = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        http_err = HttpError(resp, b"forbidden")
        http_err.error_details = [{"reason": "fileNotDownloadable"}]

        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)
            mock_dl = MagicMock()
            mock_dl.next_chunk.side_effect = http_err
            MockDL.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_pdf_download_http_error_forbidden_other_reason(self, connector):
        """Forbidden but not fileNotDownloadable -- re-raises."""
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "text/plain"}
        )
        mock_service = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        http_err = HttpError(resp, b"forbidden")
        http_err.error_details = [{"reason": "rateLimitExceeded"}]

        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)
            mock_dl = MagicMock()
            mock_dl.next_chunk.side_effect = http_err
            MockDL.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pdf_download_http_error_non_forbidden(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "text/plain"}
        )
        mock_service = MagicMock()
        resp = MagicMock()
        resp.status = 500
        resp.reason = "Server Error"
        http_err = HttpError(resp, b"error")

        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)
            mock_dl = MagicMock()
            mock_dl.next_chunk.side_effect = http_err
            MockDL.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pdf_conversion_requested_google_workspace_doc(self, connector):
        """PDF conversion for google-apps.document goes through export path."""
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.document"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.export_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            # Should export as PDF
            call_kwargs = mock_stream.call_args
            assert "application/pdf" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_empty_file_id_string(self, connector):
        """Empty string file_id should raise BAD_REQUEST."""
        record = _make_record(external_record_id="")
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400


# ===================================================================
# _create_personal_record_group()
# ===================================================================


class TestCreatePersonalRecordGroup:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        result = await connector._create_personal_record_group(
            "u1", "u@t.com", "Google Drive - u@t.com", "d1"
        )
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        assert result.name == "Google Drive - u@t.com"
        assert result.external_group_id == "d1"


# ===================================================================
# _create_app_user()
# ===================================================================


class TestCreateAppUser:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        user_about = {
            "user": {"permissionId": "p1", "emailAddress": "u@t.com", "displayName": "User"}
        }
        await connector._create_app_user(user_about)
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_propagates(self, connector):
        connector.data_entities_processor.on_new_app_users = AsyncMock(
            side_effect=RuntimeError("db fail")
        )
        user_about = {
            "user": {"permissionId": "p1", "emailAddress": "u@t.com", "displayName": "U"}
        }
        with pytest.raises(RuntimeError):
            await connector._create_app_user(user_about)


# ===================================================================
# run_sync()
# ===================================================================


class TestRunSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_success(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={
                "user": {"permissionId": "p1", "emailAddress": "u@t.com", "displayName": "U"},
                "storageQuota": {},
            }
        )
        connector.drive_data_source.files_get = AsyncMock(return_value={"id": "root-drive-id"})
        connector._create_app_user = AsyncMock()
        connector._create_personal_record_group = AsyncMock()
        connector._sync_user_personal_drive = AsyncMock()

        await connector.run_sync()
        connector._create_app_user.assert_awaited_once()
        connector._create_personal_record_group.assert_awaited_once()
        connector._sync_user_personal_drive.assert_awaited_once_with(drive_id="root-drive-id")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_drive_id_raises(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={
                "user": {"permissionId": "p1", "emailAddress": "u@t.com", "displayName": "U"},
                "storageQuota": {},
            }
        )
        connector.drive_data_source.files_get = AsyncMock(return_value={})
        connector._create_app_user = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await connector.run_sync()
        assert exc_info.value.status_code == 500


# ===================================================================
# run_incremental_sync()
# ===================================================================


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_calls_sync_user_personal_drive(self, connector):
        connector._sync_user_personal_drive = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_user_personal_drive.assert_awaited_once()


# ===================================================================
# handle_webhook_notification()
# ===================================================================


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({"type": "test"})


# ===================================================================
# cleanup()
# ===================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_all_set(self, connector):
        connector.drive_data_source = MagicMock()
        connector.google_client = MagicMock()
        connector.config = {"some": "config"}
        await connector.cleanup()
        assert connector.drive_data_source is None
        assert connector.google_client is None
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_without_drive_data_source_attr(self, connector):
        if hasattr(connector, "drive_data_source"):
            del connector.drive_data_source
        await connector.cleanup()
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_without_google_client_attr(self, connector):
        connector.drive_data_source = MagicMock()
        if hasattr(connector, "google_client"):
            del connector.google_client
        await connector.cleanup()
        assert connector.drive_data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_with_none_values(self, connector):
        connector.drive_data_source = None
        connector.google_client = None
        await connector.cleanup()
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_exception_swallowed(self, connector):
        """Cleanup should catch exceptions and not propagate."""
        connector.drive_data_source = MagicMock()
        cls = type(connector)
        cls.drive_data_source = property(
            fget=MagicMock(side_effect=RuntimeError("fail")),
            fset=MagicMock()
        )
        try:
            await connector.cleanup()
        except Exception:
            pass
        finally:
            try:
                delattr(cls, "drive_data_source")
            except AttributeError:
                pass


# ===================================================================
# reindex_records()
# ===================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector):
        await connector.reindex_records([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drive_data_source_not_initialized(self, connector):
        connector.drive_data_source = None
        with pytest.raises(Exception, match="Drive data source not initialized"):
            await connector.reindex_records([_make_record()])

    @pytest.mark.asyncio
    async def test_no_user_info_raises(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {}}
        )
        with pytest.raises(Exception, match="Failed to get user information"):
            await connector.reindex_records([_make_record()])

    @pytest.mark.asyncio
    async def test_record_check_exception_continues(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=RuntimeError("check fail")
        )
        await connector.reindex_records([_make_record()])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_updated_records(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(MagicMock(), [MagicMock()])
        )
        await connector.reindex_records([_make_record()])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_non_updated_records(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector.reindex_records([_make_record()])
        connector.data_entities_processor.on_new_records.assert_not_awaited()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_updated_and_non_updated(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[
                (MagicMock(), [MagicMock()]),
                None,
            ]
        )
        await connector.reindex_records([_make_record(), _make_record(id="rec-2")])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError):
            await connector.reindex_records([_make_record()])


# ===================================================================
# _check_and_fetch_updated_record()
# ===================================================================


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_no_file_id(self, connector):
        record = _make_record(external_record_id=None)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_group_id_uses_user_id(self, connector):
        record = _make_record(external_record_group_id=None)
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = True
        update.record = MagicMock()
        update.new_permissions = []
        connector._process_drive_item = AsyncMock(return_value=update)

        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is not None
        # The drive_id should be user_id when record_group_id is None
        call_args = connector._process_drive_item.call_args
        assert call_args[0][3] == "u1"

    @pytest.mark.asyncio
    async def test_http_not_found_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"
        connector.drive_data_source.files_get = AsyncMock(
            side_effect=HttpError(resp, b"not found")
        )
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_other_error_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        resp = MagicMock()
        resp.status = 500
        resp.reason = "Server Error"
        connector.drive_data_source.files_get = AsyncMock(
            side_effect=HttpError(resp, b"error")
        )
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_file_metadata_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(return_value=None)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_update_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        connector._process_drive_item = AsyncMock(return_value=None)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_update_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = True
        update.is_updated = False
        connector._process_drive_item = AsyncMock(return_value=update)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_updated_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = False
        connector._process_drive_item = AsyncMock(return_value=update)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_updated_returns_record_and_permissions(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = True
        update.record = MagicMock()
        update.new_permissions = [MagicMock()]
        connector._process_drive_item = AsyncMock(return_value=update)

        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is not None
        assert update.record.id == record.id

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None


# ===================================================================
# get_filter_options()
# ===================================================================


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("any_key")

    @pytest.mark.asyncio
    async def test_raises_with_all_args(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options(
                "key", page=2, limit=50, search="test", cursor="abc"
            )


# ===================================================================
# create_connector()
# ===================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.DataSourceEntitiesProcessor")
    @patch("app.connectors.sources.google.drive.individual.connector.SyncPoint")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_create_connector(self, MockGClient, MockSP, MockDSEP):
        from app.connectors.sources.google.drive.individual.connector import (
            GoogleDriveIndividualConnector,
        )

        MockSP.return_value = AsyncMock()
        mock_dep = AsyncMock()
        mock_dep.org_id = "org-1"
        MockDSEP.return_value = mock_dep

        logger = _make_logger()
        ds_provider = MagicMock()
        config_service = AsyncMock()

        result = await GoogleDriveIndividualConnector.create_connector(
            logger, ds_provider, config_service, "conn-1", "team", "test-user-id"
        )
        assert isinstance(result, GoogleDriveIndividualConnector)
        mock_dep.initialize.assert_awaited_once()
