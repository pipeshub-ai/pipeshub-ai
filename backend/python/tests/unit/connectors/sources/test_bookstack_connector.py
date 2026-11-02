"""Tests for BookStack connector."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.bookstack.connector import BookStackConnector, ReadOutcome
from contextlib import asynccontextmanager
from fastapi import HTTPException
from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.models.entities import (
    AppRole,
    AppUser,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.bookstack")


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
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def bookstack_connector(mock_logger, mock_data_entities_processor,
                        mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        connector = BookStackConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="bs-conn-1",
            scope="team",
            created_by="test-user",
        )
    return connector


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
# Init / Connection
# ===========================================================================
class TestBookStackConnectorInit:
    def test_constructor(self, bookstack_connector):
        assert bookstack_connector.connector_id == "bs-conn-1"
        assert bookstack_connector.data_source is None
        assert bookstack_connector.batch_size == 100

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_with_config")
    @patch("app.connectors.sources.bookstack.connector.BookStackDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, bookstack_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        # init checks the token with the user listing a sync starts with.
        mock_ds_cls.return_value.list_users = AsyncMock(return_value=MagicMock(success=True, data={"data": [], "total": 0}, status_code=200, error=None))
        result = await bookstack_connector.init()
        assert result is True
        assert bookstack_connector.bookstack_base_url == "https://bookstack.example.com"

    async def test_init_fails_no_config(self, bookstack_connector):
        bookstack_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await bookstack_connector.init() is False

    async def test_init_fails_missing_credentials(self, bookstack_connector):
        bookstack_connector.config_service.get_config = AsyncMock(
            return_value={"auth": {"base_url": "https://bs.example.com"}}
        )
        assert await bookstack_connector.init() is False

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_with_config")
    @patch("app.connectors.sources.bookstack.connector.BookStackDataSource")
    async def test_init_fails_client_validation_error(self, mock_ds_cls, mock_build, bookstack_connector):
        mock_ds_cls.return_value.list_users = AsyncMock(return_value=MagicMock(success=False, data={"error": {"message": "Invalid", "code": 401}}, status_code=401, error="Invalid"))
        assert await bookstack_connector.init() is False

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_with_config")
    async def test_init_fails_general_exception(self, mock_build, bookstack_connector):
        mock_build.side_effect = Exception("Network error")
        assert await bookstack_connector.init() is False


class TestBookStackTestConnection:
    async def test_not_initialized(self, bookstack_connector):
        assert await bookstack_connector.test_connection_and_access() is False

    async def test_success(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        # The test reads what a sync reads: books, users, roles and the audit log.
        for method in ("list_books", "list_users", "list_roles", "list_audit_log"):
            setattr(bookstack_connector.data_source, method, AsyncMock(return_value=MagicMock(success=True, data={"data": [], "total": 0}, status_code=200, error=None)))
        assert await bookstack_connector.test_connection_and_access() is True

    async def test_failure(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_books = AsyncMock(
            return_value=_make_response(False, error="Forbidden")
        )
        assert await bookstack_connector.test_connection_and_access() is False

    async def test_exception(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_books = AsyncMock(side_effect=Exception("timeout"))
        assert await bookstack_connector.test_connection_and_access() is False


# ===========================================================================
# Signed URL / Stream
# ===========================================================================
class TestBookStackSignedUrlAndStream:
    async def test_returns_no_signed_url(self, bookstack_connector):
        """BookStack has no signed URLs; its API URL needs the token, so the preview streams the page."""
        bookstack_connector.bookstack_base_url = "https://bs.example.com/"
        record = MagicMock()
        record.external_record_id = "page/42"
        assert await bookstack_connector.get_signed_url(record) is None

    @patch("app.connectors.sources.bookstack.connector.create_stream_record_response")
    async def test_stream_record_success(self, mock_stream, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(True, {"markdown": "# Hello"})
        )
        record = MagicMock()
        record.external_record_id = "page/42"
        record.record_name = "Test Page"
        record.mime_type = "text/markdown"
        record.id = "rec-1"
        mock_stream.return_value = MagicMock()
        await bookstack_connector.stream_record(record)
        mock_stream.assert_called_once()

    async def test_stream_record_yields_one_bytes_chunk(self, bookstack_connector):
        """Starlette iterates a `str` body one character per ASGI message."""
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(True, {"markdown": "# Hello"}, status_code=200)
        )
        record = MagicMock()
        record.external_record_id = "page/42"
        record.record_name = "Test Page"
        record.mime_type = "text/markdown"
        record.id = "rec-1"

        response = await bookstack_connector.stream_record(record)
        chunks = [chunk async for chunk in response.body_iterator]
        assert chunks == [b"# Hello"]

    async def test_stream_record_not_initialized(self, bookstack_connector):
        bookstack_connector.data_source = None
        with pytest.raises(Exception):
            await bookstack_connector.stream_record(MagicMock())

    async def test_stream_record_not_found(self, bookstack_connector):
        from fastapi import HTTPException
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(False, error="Not found", status_code=404)
        )
        record = MagicMock()
        record.external_record_id = "page/42"
        with pytest.raises(HTTPException) as ei:
            await bookstack_connector.stream_record(record)
        assert ei.value.status_code == 404

    async def test_stream_record_expired_token_is_not_reported_as_deleted(
        self, bookstack_connector
    ):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(False, error="HTTP 401", status_code=401)
        )
        record = MagicMock()
        record.external_record_id = "page/42"
        with pytest.raises(HTTPException) as ei:
            await bookstack_connector.stream_record(record)
        assert ei.value.status_code == 409


# ===========================================================================
# User-related methods
# ===========================================================================
class TestBookStackUsers:
    def test_get_app_users(self, bookstack_connector):
        users = [
            {"id": 1, "name": "Alice", "email": "alice@test.com"},
            {"id": 2, "name": "Bob", "email": "bob@test.com"},
        ]
        app_users = bookstack_connector._get_app_users(users)
        assert len(app_users) == 2
        assert app_users[0].full_name == "Alice"

    async def test_get_all_users_pagination(self, bookstack_connector):
        page1 = _make_response(True, {"data": [{"id": 1, "name": "A", "email": "a@t.com"}], "total": 2})
        page2 = _make_response(True, {"data": [{"id": 2, "name": "B", "email": "b@t.com"}], "total": 2})
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_users = AsyncMock(side_effect=[page1, page2])
        result = await bookstack_connector.get_all_users()
        assert len(result) == 2

    async def test_get_all_users_empty(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_users = AsyncMock(
            return_value=_make_response(True, {"data": [], "total": 0})
        )
        assert await bookstack_connector.get_all_users() == []

    async def test_get_all_users_api_error(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_users = AsyncMock(
            return_value=_make_response(False, None, "Server Error")
        )
        assert await bookstack_connector.get_all_users() == []


# ===========================================================================
# Roles
# ===========================================================================
class TestBookStackRoles:
    async def test_list_roles_with_details_success(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(True, {"data": [{"id": 1}, {"id": 2}], "total": 2})
        )
        role1_detail = _make_response(True, {"id": 1, "display_name": "Admin", "permissions": []})
        role2_detail = _make_response(True, {"id": 2, "display_name": "Editor", "permissions": []})
        bookstack_connector.data_source.get_role = AsyncMock(side_effect=[role1_detail, role2_detail])
        result = await bookstack_connector.list_roles_with_details()
        assert len(result) == 2
        assert 1 in result
        assert 2 in result

    async def test_list_roles_with_details_failed_role(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(True, {"data": [{"id": 1}]})
        )
        bookstack_connector.data_source.get_role = AsyncMock(side_effect=Exception("fail"))
        result = await bookstack_connector.list_roles_with_details()
        assert len(result) == 0

    async def test_list_roles_list_fails(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(False, error="error")
        )
        assert await bookstack_connector.list_roles_with_details() == {}

    async def test_list_roles_empty(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(True, {"data": []})
        )
        assert await bookstack_connector.list_roles_with_details() == {}


# ===========================================================================
# Parsing helpers
# ===========================================================================
class TestBookStackParsing:
    def test_parse_id_and_name_success(self, bookstack_connector):
        event = {"id": 1, "detail": "(5) Tester"}
        eid, name = bookstack_connector._parse_id_and_name_from_event(event)
        assert eid == 5
        assert name == "Tester"

    def test_parse_id_and_name_missing_detail(self, bookstack_connector):
        eid, name = bookstack_connector._parse_id_and_name_from_event({"id": 1})
        assert eid is None
        assert name is None

    def test_parse_id_and_name_bad_format(self, bookstack_connector):
        eid, name = bookstack_connector._parse_id_and_name_from_event({"id": 1, "detail": "garbage"})
        assert eid is None

    def test_get_iso_time(self, bookstack_connector):
        result = bookstack_connector._get_iso_time()
        assert "T" in result
        assert result.endswith("Z")

    def test_parse_timestamp_valid(self, bookstack_connector):
        result = bookstack_connector._parse_timestamp("2024-01-01T00:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_parse_timestamp_invalid(self, bookstack_connector):
        assert bookstack_connector._parse_timestamp("not-a-date") is None

    def test_parse_timestamp_none(self, bookstack_connector):
        assert bookstack_connector._parse_timestamp(None) is None


# ===========================================================================
# Permissions
# ===========================================================================


# ===========================================================================
# Sync users
# ===========================================================================
class TestBookStackSyncUsers:
    async def test_sync_users_full(self, bookstack_connector, mock_data_entities_processor):
        from app.models.entities import AppUser
        users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="1", email="a@t.com", full_name="A", is_active=True,
            )
        ]
        await bookstack_connector._sync_users_full(users)
        mock_data_entities_processor.on_new_app_users.assert_awaited_once()



# ===========================================================================
# Sync user roles
# ===========================================================================
class TestBookStackSyncUserRoles:
    async def test_sync_user_roles_full(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        # _fetch_all_roles_with_details
        bookstack_connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(True, {"data": [{"id": 1}], "total": 1})
        )
        bookstack_connector.data_source.get_role = AsyncMock(
            return_value=_make_response(True, {"id": 1, "display_name": "Admin", "users": [{"id": 10}]})
        )
        # _fetch_all_users_with_details
        bookstack_connector.data_source.list_users = AsyncMock(
            return_value=_make_response(True, {"data": [{"id": 10}], "total": 1})
        )
        bookstack_connector.data_source.get_user = AsyncMock(
            return_value=_make_response(True, {"id": 10, "name": "A", "email": "a@t.com", "created_at": "2024-01-01T00:00:00Z"})
        )
        await bookstack_connector._sync_user_roles_full()
        bookstack_connector.data_entities_processor.on_new_app_roles.assert_awaited()



# ===========================================================================
# Record groups
# ===========================================================================


# ===========================================================================
# Records sync
# ===========================================================================


# ===========================================================================
# Handle record updates
# ===========================================================================


# ===========================================================================
# Full run_sync flow
# ===========================================================================


# ===========================================================================
# User delete/upsert event handling
# ===========================================================================
class TestBookStackUserEvents:
    async def test_handle_user_create_event(self, bookstack_connector):
        from app.models.entities import AppUser
        events = [{"detail": "(5) NewUser"}, {"detail": "bad data"}]
        app_users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="5", email="new@t.com", full_name="NewUser", is_active=True,
            ),
        ]
        await bookstack_connector._handle_user_create_event(events, app_users)
        bookstack_connector.data_entities_processor.on_new_app_users.assert_awaited()

    async def test_handle_user_create_event_no_match(self, bookstack_connector):
        events = [{"detail": "(999) Ghost"}]
        from app.models.entities import AppUser
        app_users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="5", email="new@t.com", full_name="NewUser", is_active=True,
            ),
        ]
        await bookstack_connector._handle_user_create_event(events, app_users)




    async def test_handle_user_upsert_event(self, bookstack_connector):
        from app.models.entities import AppUser
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.get_user = AsyncMock(
            return_value=_make_response(True, {
                "id": 5, "name": "Test", "email": "test@t.com",
                "roles": [{"id": 1, "display_name": "Admin"}],
            })
        )
        bookstack_connector._handle_user_create_event = AsyncMock()
        role = (AppRole(app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1", source_role_id="1", name="Admin"), [])
        bookstack_connector._read_role_with_members = AsyncMock(return_value=(ReadOutcome.COMPLETE, role))
        events = [{"detail": "(5) TestUser"}]
        app_users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="5", email="test@t.com", full_name="TestUser", is_active=True,
            ),
        ]
        await bookstack_connector._handle_user_upsert_event(events, app_users)
        bookstack_connector.data_entities_processor.on_new_app_roles.assert_awaited_once_with([role])


# ===========================================================================
# Role event handling
# ===========================================================================
class TestBookStackRoleEvents:



    async def test_handle_role_delete_event(self, bookstack_connector):
        await bookstack_connector._handle_role_delete_event(42)
        bookstack_connector.data_entities_processor.on_app_role_deleted.assert_awaited_once()



# ===========================================================================
# Date filters
# ===========================================================================
class TestBookStackDateFilters:
    def test_get_date_filters_empty(self, bookstack_connector):
        from app.connectors.core.registry.filters import FilterCollection
        bookstack_connector.sync_filters = FilterCollection()
        ma, mb, ca, cb = bookstack_connector._get_date_filters()
        assert all(x is None for x in (ma, mb, ca, cb))

    def test_build_date_filter_params_with_dates(self, bookstack_connector):
        dt = datetime(2024, 6, 1, tzinfo=timezone.utc)
        result = bookstack_connector._build_date_filter_params(
            modified_after=dt, modified_before=dt,
            created_after=dt, created_before=dt,
            additional_filters={"foo": "bar"},
        )
        assert "updated_at:gte" in result
        assert "foo" in result

    def test_build_date_filter_params_empty(self, bookstack_connector):
        result = bookstack_connector._build_date_filter_params()
        assert result is None

# =============================================================================
# Merged from test_bookstack_connector_full_coverage.py
# =============================================================================

@pytest.fixture()
def mock_logger_fullcov():
    return logging.getLogger("test.bookstack.full")


@pytest.fixture()
def mock_data_entities_processor_fullcov():
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
    proc.reindex_existing_records = AsyncMock()
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_user_by_user_id = AsyncMock(return_value=MagicMock(email="test@example.com"))
    proc.get_user_by_email = AsyncMock(return_value=MagicMock(id="user-db-1"))
    proc.delete_edges_between_collections = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider_fullcov():
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
def connector(mock_logger_fullcov, mock_data_entities_processor_fullcov,
              mock_data_store_provider_fullcov, mock_config_service):
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        c = BookStackConnector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_data_entities_processor_fullcov,
            data_store_provider=mock_data_store_provider_fullcov,
            config_service=mock_config_service,
            connector_id="bs-full-1",
            scope="personal",
            created_by="test-user-id",
        )
    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    c.bookstack_base_url = "https://bookstack.example.com/"
    return c


def _make_response(success=True, data=None, error=None, status_code=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    r.status_code = status_code
    return r


def _make_page_list_response(pages, total=None):
    if total is None:
        total = len(pages)
    content = json.dumps({"data": pages, "total": total})
    return _make_response(data={"content": content})


class TestInitSuccess:
    @pytest.mark.asyncio
    async def test_init_success(self, connector):
        with patch("app.connectors.sources.bookstack.connector.BookStackClient.build_with_config"):
            with patch("app.connectors.sources.bookstack.connector.BookStackDataSource") as ds:
                ds.return_value.list_users = AsyncMock(return_value=MagicMock(success=True, data={"data": [], "total": 0}, status_code=200, error=None))
                result = await connector.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_client_validation_fails(self, connector):
        with patch("app.connectors.sources.bookstack.connector.BookStackClient.build_with_config"):
            with patch("app.connectors.sources.bookstack.connector.BookStackDataSource") as ds:
                ds.return_value.list_users = AsyncMock(return_value=MagicMock(success=False, data={"error": {"message": "Invalid", "code": 401}}, status_code=401, error="Invalid"))
                result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_exception(self, connector):
        connector.config_service.get_config = AsyncMock(side_effect=Exception("boom"))
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_missing_token_secret(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"base_url": "https://bs.com", "token_id": "t"}
        })
        result = await connector.init()
        assert result is False


class TestStreamRecordSuccess:
    @pytest.mark.asyncio
    async def test_stream_success(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(success=True, data={"markdown": "# Hello"})
        )
        record = MagicMock()
        record.external_record_id = "page/42"
        record.record_name = "test.md"
        record.mime_type = "text/markdown"
        record.id = "r1"
        with patch("app.connectors.sources.bookstack.connector.create_stream_record_response") as mock_resp:
            mock_resp.return_value = MagicMock()
            result = await connector.stream_record(record)
        assert result is not None


class TestListRolesWithDetails:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(data={"data": [{"id": 1}, {"id": 2}], "total": 2})
        )
        connector.data_source.get_role = AsyncMock(side_effect=[
            _make_response(data={"id": 1, "display_name": "Admin"}),
            _make_response(data={"id": 2, "display_name": "Editor"}),
        ])
        result = await connector.list_roles_with_details()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        result = await connector.list_roles_with_details()
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_roles(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(data={"data": []})
        )
        result = await connector.list_roles_with_details()
        assert result == {}

    @pytest.mark.asyncio
    async def test_role_detail_exception(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(data={"data": [{"id": 1}]})
        )
        connector.data_source.get_role = AsyncMock(side_effect=Exception("err"))
        result = await connector.list_roles_with_details()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_role_detail_failure_response(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(data={"data": [{"id": 1}]})
        )
        connector.data_source.get_role = AsyncMock(
            return_value=_make_response(success=False, error="denied")
        )
        result = await connector.list_roles_with_details()
        assert len(result) == 0




class TestSyncUsersFull:
    @pytest.mark.asyncio
    async def test_calls_processor(self, connector, mock_data_entities_processor_fullcov):
        users = [MagicMock()]
        await connector._sync_users_full(users)
        mock_data_entities_processor_fullcov.on_new_app_users.assert_awaited_once_with(users)




class TestHandleUserCreateEvent:
    @pytest.mark.asyncio
    async def test_parses_and_submits(self, connector, mock_data_entities_processor_fullcov):
        events = [{"detail": "(5) Harshit"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="h@test.com", full_name="Harshit", is_active=True)
        ]
        await connector._handle_user_create_event(events, users)
        mock_data_entities_processor_fullcov.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_matching_users(self, connector, mock_data_entities_processor_fullcov):
        events = [{"detail": "(99) Ghost"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="h@test.com", full_name="Harshit", is_active=True)
        ]
        await connector._handle_user_create_event(events, users)
        mock_data_entities_processor_fullcov.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_events(self, connector, mock_data_entities_processor_fullcov):
        await connector._handle_user_create_event([], [])
        mock_data_entities_processor_fullcov.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bad_detail(self, connector, mock_data_entities_processor_fullcov):
        events = [{"detail": "bad format"}]
        await connector._handle_user_create_event(events, [])
        mock_data_entities_processor_fullcov.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_detail(self, connector, mock_data_entities_processor_fullcov):
        events = [{}]
        await connector._handle_user_create_event(events, [])
        mock_data_entities_processor_fullcov.on_new_app_users.assert_not_awaited()




class TestHandleUserUpsertEvent:
    @pytest.mark.asyncio
    async def test_processes_upsert_with_roles(self, connector):
        events = [{"detail": "(5) TestUser"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="u@test.com", full_name="TestUser", is_active=True)
        ]
        connector._handle_user_create_event = AsyncMock()
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(return_value=_make_response(
            data={"id": 5, "email": "u@test.com", "roles": [{"id": 1, "display_name": "Admin"}]}
        ))
        role = (AppRole(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_role_id="1", name="Admin"), [])
        connector._read_role_with_members = AsyncMock(return_value=(ReadOutcome.COMPLETE, role))

        assert await connector._handle_user_upsert_event(events, users) is True
        connector._handle_user_create_event.assert_awaited_once()
        connector._read_role_with_members.assert_awaited_once_with(1)
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once_with([role])





    @pytest.mark.asyncio
    async def test_exception_during_processing(self, connector):
        events = [{"detail": "(5) Error"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="u@test.com", full_name="Error", is_active=True)
        ]
        connector._handle_user_create_event = AsyncMock()
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(side_effect=Exception("boom"))

        await connector._handle_user_upsert_event(events, users)




class TestSyncUserRolesFull:
    @pytest.mark.asyncio
    async def test_no_roles(self, connector, mock_data_entities_processor_fullcov):
        connector._fetch_all_roles_with_details = AsyncMock(return_value=[])
        await connector._sync_user_roles_full()
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_roles_and_users(self, connector, mock_data_entities_processor_fullcov):
        connector._fetch_all_roles_with_details = AsyncMock(return_value=[
            {"id": 1, "display_name": "Admin", "users": [{"id": 10}]}
        ])
        connector._fetch_all_users_with_details = AsyncMock(return_value=[
            {"id": 10, "email": "u@test.com", "name": "User"}
        ])
        await connector._sync_user_roles_full()
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_not_in_details_skipped(self, connector, mock_data_entities_processor_fullcov):
        connector._fetch_all_roles_with_details = AsyncMock(return_value=[
            {"id": 1, "display_name": "Admin", "users": [{"id": 10}]}
        ])
        connector._fetch_all_users_with_details = AsyncMock(return_value=[])
        await connector._sync_user_roles_full()
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_awaited_once()


class TestFetchAllRolesWithDetails:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = AsyncMock()
        connector._fetch_all_roles = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        connector.data_source.get_role = AsyncMock(side_effect=[
            _make_response(data={"id": 1, "display_name": "Admin", "users": []}),
            _make_response(data={"id": 2, "display_name": "Editor", "users": []}),
        ])
        result = await connector._fetch_all_roles_with_details()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_basic_roles(self, connector):
        connector._fetch_all_roles = AsyncMock(return_value=[])
        result = await connector._fetch_all_roles_with_details()
        assert result == []

    @pytest.mark.asyncio
    async def test_partial_failure(self, connector):
        connector.data_source = AsyncMock()
        connector._fetch_all_roles = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        connector.data_source.get_role = AsyncMock(side_effect=[
            _make_response(data={"id": 1, "display_name": "Admin"}),
            Exception("fail"),
        ])
        result = await connector._fetch_all_roles_with_details()
        assert len(result) == 1


class TestFetchAllRoles:
    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(data={"data": [{"id": 1}], "total": 1})
        )
        result = await connector._fetch_all_roles()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        result = await connector._fetch_all_roles()
        assert result == []

    @pytest.mark.asyncio
    async def test_multi_page(self, connector):
        connector.batch_size = 1
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(side_effect=[
            _make_response(data={"data": [{"id": 1}], "total": 2}),
            _make_response(data={"data": [{"id": 2}], "total": 2}),
        ])
        result = await connector._fetch_all_roles()
        assert len(result) == 2


class TestFetchAllUsersWithDetails:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(
            return_value=_make_response(data={"data": [{"id": 1}], "total": 1})
        )
        connector.data_source.get_user = AsyncMock(
            return_value=_make_response(data={"id": 1, "name": "User", "email": "u@test.com"})
        )
        result = await connector._fetch_all_users_with_details()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(
            return_value=_make_response(data={"data": [], "total": 0})
        )
        result = await connector._fetch_all_users_with_details()
        assert result == []

    @pytest.mark.asyncio
    async def test_detail_fetch_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(
            return_value=_make_response(data={"data": [{"id": 1}], "total": 1})
        )
        connector.data_source.get_user = AsyncMock(side_effect=Exception("fail"))
        result = await connector._fetch_all_users_with_details()
        assert result == []




















class TestHandleRecordGroupDeleteEvent:
    @pytest.mark.asyncio
    async def test_logs_not_implemented(self, connector):
        await connector._handle_record_group_delete_event({"detail": "(1) Del"}, "book")














class TestHandleRoleDeleteEvent:
    @pytest.mark.asyncio
    async def test_success(self, connector, mock_data_entities_processor_fullcov):
        await connector._handle_role_delete_event(1)
        mock_data_entities_processor_fullcov.on_app_role_deleted.assert_awaited_once()




class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self, connector):
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(Exception):
            await connector.reindex_records([MagicMock()])







class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_book_ids(self, connector):
        connector.data_source = AsyncMock()
        connector._get_book_options = AsyncMock(return_value=MagicMock())
        await connector.get_filter_options("book_ids")
        connector._get_book_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_key(self, connector):
        with pytest.raises(ValueError):
            await connector.get_filter_options("unknown")


class TestGetBookOptions:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.search_all = AsyncMock(return_value=_make_response(
            data={"data": [{"id": 1, "name": "Book1", "slug": "book1"}], "total": 1}
        ))
        result = await connector._get_book_options(1, 20, "", None)
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_has_more_pages(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.search_all = AsyncMock(return_value=_make_response(
            data={"data": [{"id": 1, "name": "B1", "slug": "b1"}], "total": 50}
        ))
        result = await connector._get_book_options(1, 20, "", None)
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_no_more_pages(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.search_all = AsyncMock(return_value=_make_response(
            data={"data": [{"id": 1, "name": "B1", "slug": "b1"}], "total": 1}
        ))
        result = await connector._get_book_options(1, 20, "", None)
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.search_all = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        with pytest.raises(RuntimeError):
            await connector._get_book_options(1, 20, "", None)

    @pytest.mark.asyncio
    async def test_with_search_query(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.search_all = AsyncMock(return_value=_make_response(
            data={"data": [], "total": 0}
        ))
        result = await connector._get_book_options(1, 20, "  test  ", None)
        assert result.success is True
        connector.data_source.search_all.assert_awaited_once_with(
            query="test", type="book", page=1, count=20
        )


class TestGetBookIdFilter:
    def test_no_filter(self, connector):
        ids, op = connector._get_book_id_filter()
        assert ids is None
        assert op is None

    def test_with_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_value.return_value = ["1", "2"]
        mock_filter.get_operator.return_value = MagicMock(value="in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.BOOK_IDS else None
        )
        ids, op = connector._get_book_id_filter()
        assert ids == {1, 2}

    def test_empty_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = True
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        ids, op = connector._get_book_id_filter()
        assert ids is None

    def test_none_filter(self, connector):
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None
        ids, op = connector._get_book_id_filter()
        assert ids is None




