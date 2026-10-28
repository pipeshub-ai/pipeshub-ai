"""Comprehensive tests for BookStack connector - extended coverage."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.connectors.sources.bookstack.connector import BookStackConnector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.bookstack_comp")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-bs-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_new_app_roles = AsyncMock()
    proc.on_app_role_deleted = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.on_user_removed = AsyncMock(return_value=True)
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_user_by_user_id = AsyncMock(return_value=MagicMock(email="test@example.com"))
    proc.get_user_by_email = AsyncMock(return_value=MagicMock(id="user-db-1"))
    proc.delete_edges_between_collections = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_email = AsyncMock(return_value=MagicMock(id="user-db-1"))
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "test@example.com"})
    mock_tx.delete_edges_between_collections = AsyncMock()

    @asynccontextmanager
    async def _transaction():
        yield mock_tx

    provider.transaction = _transaction
    provider._mock_tx = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "base_url": "https://bookstack.example.com",
            "token_id": "tok-id-1",
            "token_secret": "tok-secret-1",
        },
    })
    return svc


@pytest.fixture()
def connector(mock_logger, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        c = BookStackConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="bs-comp-1",
            scope="personal",
            created_by="test-user-id",
        )
    return c


def _make_response(success=True, data=None, error=None, status_code=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    r.status_code = status_code
    return r


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================


# ===========================================================================
# Init
# ===========================================================================
class TestInitComprehensive:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_missing_base_url(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"token_id": "t", "token_secret": "s"}
        })
        result = await connector.init()
        assert result is False


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestConnectionComprehensive:
    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = AsyncMock()
        for method in ("list_books", "list_users", "list_roles", "list_audit_log"):
            setattr(connector.data_source, method, AsyncMock(return_value=MagicMock(success=True, data={"data": [], "total": 0}, status_code=200, error=None)))
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_books = AsyncMock(return_value=_make_response(success=False, error="denied"))
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_books = AsyncMock(side_effect=Exception("boom"))
        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# _get_app_users
# ===========================================================================
class TestGetAppUsersComprehensive:
    def test_converts_users(self, connector):
        users = [
            {"id": 1, "name": "User One", "email": "u1@test.com"},
            {"id": 2, "name": "User Two", "email": "u2@test.com"},
        ]
        result = connector._get_app_users(users)
        assert len(result) == 2
        assert result[0].email == "u1@test.com"
        assert result[1].full_name == "User Two"

    def test_empty_list(self, connector):
        assert connector._get_app_users([]) == []


# ===========================================================================
# get_all_users
# ===========================================================================
class TestGetAllUsersComprehensive:
    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(side_effect=[
            _make_response(data={"data": [{"id": 1, "name": "U1", "email": "u1@test.com"}], "total": 2}),
            _make_response(data={"data": [{"id": 2, "name": "U2", "email": "u2@test.com"}], "total": 2}),
        ])
        users = await connector.get_all_users()
        assert len(users) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(return_value=_make_response(data={"data": []}))
        users = await connector.get_all_users()
        assert users == []

    @pytest.mark.asyncio
    async def test_api_error(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(
            return_value=_make_response(success=False, error="error")
        )
        users = await connector.get_all_users()
        assert users == []


# ===========================================================================
# _parse_timestamp
# ===========================================================================
class TestParseTimestampComprehensive:
    def test_valid_iso(self, connector):
        result = connector._parse_timestamp("2025-01-15T10:30:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_invalid(self, connector):
        assert connector._parse_timestamp("not-a-date") is None

    def test_none(self, connector):
        assert connector._parse_timestamp(None) is None

    def test_empty_string(self, connector):
        assert connector._parse_timestamp("") is None


# ===========================================================================
# _build_role_permissions_map
# ===========================================================================


# ===========================================================================
# _pass_date_filters
# ===========================================================================
class TestPassDateFiltersComprehensive:
    def test_no_filters_passes(self, connector):
        page = {"created_at": "2025-01-15T00:00:00Z", "updated_at": "2025-01-15T00:00:00Z"}
        assert connector._pass_date_filters(page) is True

    def test_created_after_blocks(self, connector):
        page = {"created_at": "2024-01-01T00:00:00Z"}
        created_after = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(page, created_after=created_after) is False

    def test_created_before_blocks(self, connector):
        page = {"created_at": "2025-06-01T00:00:00Z"}
        created_before = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(page, created_before=created_before) is False

    def test_modified_after_blocks(self, connector):
        page = {"updated_at": "2024-01-01T00:00:00Z"}
        modified_after = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(page, modified_after=modified_after) is False

    def test_modified_before_blocks(self, connector):
        page = {"updated_at": "2025-06-01T00:00:00Z"}
        modified_before = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(page, modified_before=modified_before) is False

    def test_within_range_passes(self, connector):
        page = {"created_at": "2025-06-01T00:00:00Z", "updated_at": "2025-06-01T00:00:00Z"}
        created_after = datetime(2025, 1, 1, tzinfo=timezone.utc)
        created_before = datetime(2025, 12, 31, tzinfo=timezone.utc)
        assert connector._pass_date_filters(page, created_after=created_after, created_before=created_before) is True

    def test_no_timestamps_in_page_passes(self, connector):
        page = {}
        created_after = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(page, created_after=created_after) is True


# ===========================================================================
# _build_date_filter_params
# ===========================================================================
class TestBuildDateFilterParamsComprehensive:
    def test_no_dates(self, connector):
        result = connector._build_date_filter_params()
        assert result is None

    def test_modified_after(self, connector):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = connector._build_date_filter_params(modified_after=dt)
        assert "updated_at:gte" in result

    def test_created_before(self, connector):
        dt = datetime(2025, 12, 31, tzinfo=timezone.utc)
        result = connector._build_date_filter_params(created_before=dt)
        assert "created_at:lte" in result

    def test_additional_filters(self, connector):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = connector._build_date_filter_params(
            modified_after=dt,
            additional_filters={"custom_key": "custom_value"}
        )
        assert result["custom_key"] == "custom_value"
        assert "updated_at:gte" in result

    def test_all_dates(self, connector):
        dt1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        dt2 = datetime(2025, 12, 31, tzinfo=timezone.utc)
        result = connector._build_date_filter_params(
            modified_after=dt1, modified_before=dt2,
            created_after=dt1, created_before=dt2
        )
        assert len(result) == 4


# ===========================================================================
# _process_bookstack_page
# ===========================================================================


# ===========================================================================
# _handle_record_updates
# ===========================================================================


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecordComprehensive:
    @pytest.mark.asyncio
    async def test_stream_not_initialized(self, connector):
        connector.data_source = None
        record = MagicMock()
        record.external_record_id = "page/1"
        with pytest.raises(HTTPException):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_not_found(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(success=False, status_code=404)
        )
        record = MagicMock()
        record.external_record_id = "page/1"
        with pytest.raises(HTTPException) as ei:
            await connector.stream_record(record)
        assert ei.value.status_code == 404


# ===========================================================================
# get_signed_url
# ===========================================================================
class TestGetSignedUrlComprehensive:
    @pytest.mark.asyncio
    async def test_returns_no_signed_url(self, connector):
        connector.bookstack_base_url = "https://bookstack.example.com/"
        record = MagicMock()
        record.external_record_id = "page/42"
        assert await connector.get_signed_url(record) is None


# ===========================================================================
# Misc
# ===========================================================================
class TestMiscComprehensive:
    @pytest.mark.asyncio
    async def test_cleanup(self, connector):
        connector.data_source = MagicMock()
        await connector.cleanup()
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    def test_handle_webhook_notification(self, connector):
        with patch("asyncio.create_task"):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_reindex_records_empty(self, connector):
        await connector.reindex_records([])

    def test_parse_id_and_name_success(self, connector):
        # Format is "(ID) Name"
        event = {"detail": "(42) TestPage"}
        pid, name = connector._parse_id_and_name_from_event(event)
        assert pid == 42
        assert name == "TestPage"

    def test_parse_id_and_name_no_detail(self, connector):
        event = {}
        pid, name = connector._parse_id_and_name_from_event(event)
        assert pid is None
        assert name is None

    def test_parse_id_and_name_bad_format(self, connector):
        event = {"detail": "bad-format"}
        pid, name = connector._parse_id_and_name_from_event(event)
        assert pid is None
        assert name is None

    def test_get_iso_time(self, connector):
        result = connector._get_iso_time()
        assert "T" in result



# ===========================================================================
# _get_date_filters
# ===========================================================================
class TestGetDateFiltersComprehensive:
    def test_empty_filters(self, connector):
        connector.sync_filters = FilterCollection()
        result = connector._get_date_filters()
        assert all(v is None for v in result)

    def test_with_modified_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        # Python 3.10 fromisoformat doesn't support 'Z' suffix - use +00:00
        mock_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", "2025-12-31T00:00:00+00:00")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        result = connector._get_date_filters()
        assert result[0] is not None  # modified_after
        assert result[1] is not None  # modified_before
