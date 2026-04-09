"""Tests for BookStack connector."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.bookstack.connector import BookStackConnector, RecordUpdate
from contextlib import asynccontextmanager
from fastapi import HTTPException
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
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


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================
class TestRecordUpdate:
    def test_construction(self):
        ru = RecordUpdate(
            record=None, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        assert ru.is_new is True
        assert ru.external_record_id is None

    def test_with_external_id(self):
        ru = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=False,
            external_record_id="ext-123",
        )
        assert ru.external_record_id == "ext-123"
        assert ru.is_updated is True


# ===========================================================================
# Init / Connection
# ===========================================================================
class TestBookStackConnectorInit:
    def test_constructor(self, bookstack_connector):
        assert bookstack_connector.connector_id == "bs-conn-1"
        assert bookstack_connector.data_source is None
        assert bookstack_connector.batch_size == 100

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate", new_callable=AsyncMock)
    @patch("app.connectors.sources.bookstack.connector.BookStackDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, bookstack_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
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

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate", new_callable=AsyncMock)
    async def test_init_fails_client_validation_error(self, mock_build, bookstack_connector):
        mock_build.side_effect = ValueError("Invalid credentials")
        assert await bookstack_connector.init() is False

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate", new_callable=AsyncMock)
    async def test_init_fails_general_exception(self, mock_build, bookstack_connector):
        mock_build.side_effect = Exception("Network error")
        assert await bookstack_connector.init() is False


class TestBookStackTestConnection:
    async def test_not_initialized(self, bookstack_connector):
        assert await bookstack_connector.test_connection_and_access() is False

    async def test_success(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.list_books = AsyncMock(return_value=_make_response(True))
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
    async def test_returns_api_url(self, bookstack_connector):
        bookstack_connector.bookstack_base_url = "https://bs.example.com/"
        record = MagicMock()
        record.external_record_id = "page/42"
        url = await bookstack_connector.get_signed_url(record)
        assert "api/pages/page/42/export/markdown" in url

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

    async def test_stream_record_not_initialized(self, bookstack_connector):
        bookstack_connector.data_source = None
        with pytest.raises(Exception):
            await bookstack_connector.stream_record(MagicMock())

    async def test_stream_record_not_found(self, bookstack_connector):
        from fastapi import HTTPException
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.export_page_markdown = AsyncMock(
            return_value=_make_response(False, error="Not found")
        )
        record = MagicMock()
        record.external_record_id = "page/42"
        with pytest.raises(HTTPException):
            await bookstack_connector.stream_record(record)


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
            return_value=_make_response(True, {"data": [{"id": 1}, {"id": 2}]})
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
class TestBookStackPermissions:
    def test_parse_bookstack_permissions_all_users(self, bookstack_connector):
        from app.models.entities import AppUser
        users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="1", email="a@t.com", full_name="A", is_active=True,
            ),
        ]
        perms = bookstack_connector._parse_bookstack_permissions_all_users(users)
        assert len(perms) == 1
        assert perms[0].email == "a@t.com"

    def test_build_role_permissions_map(self, bookstack_connector):
        roles = [
            {"id": 1, "display_name": "Admin", "users": [{"id": 10}]},
            {"id": 2, "display_name": "Editor", "users": [{"id": 20}, {"id": 30}]},
        ]
        user_email_map = {10: "a@t.com", 20: "b@t.com", 30: "c@t.com"}
        result = bookstack_connector._build_role_permissions_map(roles, user_email_map)
        assert len(result) == 2
        assert len(result[1]) == 1
        assert len(result[2]) == 2

    def test_build_role_permissions_map_missing_email(self, bookstack_connector):
        roles = [{"id": 1, "display_name": "Admin", "users": [{"id": 10}]}]
        result = bookstack_connector._build_role_permissions_map(roles, {})
        assert len(result) == 0

    async def test_parse_bookstack_permissions_with_owner(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.get_user = AsyncMock(
            return_value=_make_response(True, {"email": "owner@test.com"})
        )
        permissions_data = {
            "owner": {"id": 1},
            "role_permissions": [
                {"role_id": 10, "view": True, "update": False, "delete": False, "create": False},
                {"role_id": 11, "view": True, "update": True, "delete": False, "create": False},
            ],
            "fallback_permissions": {"inheriting": False},
        }
        perms = await bookstack_connector._parse_bookstack_permissions(permissions_data, {}, "page")
        # 1 owner + 1 read + 1 write
        assert len(perms) == 3

    async def test_parse_bookstack_permissions_fallback_book(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.get_user = AsyncMock(
            return_value=_make_response(True, {"email": "owner@test.com"})
        )
        permissions_data = {
            "owner": {"id": 1},
            "role_permissions": [],
            "fallback_permissions": {"inheriting": True},
        }
        roles_details = {
            100: {"permissions": ["book-view-all", "book-create-all"]},
            200: {"permissions": ["something-else"]},
        }
        perms = await bookstack_connector._parse_bookstack_permissions(
            permissions_data, roles_details, "book"
        )
        # 1 owner + 1 from role 100 (write because create-all)
        assert len(perms) == 2


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

    async def test_sync_users_incremental_with_create_events(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        create_resp = _make_response(True, {"data": [{"id": 1, "detail": "(5) NewUser"}]})
        update_resp = _make_response(True, {"data": []})
        delete_resp = _make_response(True, {"data": [{"id": 3, "detail": "(7) OldUser"}]})
        bookstack_connector.data_source.list_audit_log = AsyncMock(
            side_effect=[create_resp, update_resp, delete_resp]
        )
        bookstack_connector._handle_user_upsert_event = AsyncMock()
        from app.models.entities import AppUser
        users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="5", email="new@t.com", full_name="NewUser", is_active=True,
            )
        ]
        await bookstack_connector._sync_users_incremental(users, "2024-01-01T00:00:00Z")
        bookstack_connector._handle_user_upsert_event.assert_awaited_once()


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

    async def test_sync_user_roles_incremental(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        empty_resp = _make_response(True, {"data": []})
        create_resp = _make_response(True, {"data": [{"id": 1, "detail": "(10) TestRole"}]})
        bookstack_connector.data_source.list_audit_log = AsyncMock(
            side_effect=[create_resp, empty_resp, empty_resp]
        )
        bookstack_connector._fetch_all_users_with_details = AsyncMock(return_value=[
            {"id": 1, "email": "a@t.com"}
        ])
        bookstack_connector._handle_role_create_event = AsyncMock()
        await bookstack_connector._sync_user_roles_incremental("2024-01-01T00:00:00Z")
        bookstack_connector._handle_role_create_event.assert_awaited()


# ===========================================================================
# Record groups
# ===========================================================================
class TestBookStackRecordGroups:
    async def test_sync_content_type_as_record_group(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.sync_filters = MagicMock()
        bookstack_connector.sync_filters.get = MagicMock(return_value=None)
        bookstack_connector._get_book_id_filter = MagicMock(return_value=(None, None))

        list_resp = _make_response(True, {"data": [{"id": 1, "name": "Book1"}], "total": 1})
        perm_resp = _make_response(True, {
            "owner": {"id": 1},
            "role_permissions": [],
            "fallback_permissions": {"inheriting": False},
        })
        bookstack_connector.data_source.get_content_permissions = AsyncMock(return_value=perm_resp)
        bookstack_connector.data_source.get_user = AsyncMock(
            return_value=_make_response(True, {"email": "owner@t.com"})
        )

        synced_ids = await bookstack_connector._sync_content_type_as_record_group(
            content_type_name="book",
            list_method=AsyncMock(return_value=list_resp),
            roles_details={},
        )
        assert 1 in synced_ids
        bookstack_connector.data_entities_processor.on_new_record_groups.assert_awaited()

    async def test_create_record_group_with_permissions_missing_id(self, bookstack_connector):
        result = await bookstack_connector._create_record_group_with_permissions(
            item={"name": "test"}, content_type_name="book", roles_details={}
        )
        assert result is None

    async def test_create_record_group_with_permissions_exception(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.get_content_permissions = AsyncMock(side_effect=Exception("fail"))
        result = await bookstack_connector._create_record_group_with_permissions(
            item={"id": 1, "name": "test"}, content_type_name="book", roles_details={}
        )
        assert result is None


# ===========================================================================
# Records sync
# ===========================================================================
class TestBookStackRecordsSync:
    async def test_process_bookstack_page_new(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.bookstack_base_url = "https://bs.example.com/"
        bookstack_connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(True, {
                "owner": None,
                "role_permissions": [],
                "fallback_permissions": {"inheriting": True},
            })
        )
        page = {
            "id": 42,
            "name": "Test Page",
            "book_id": 1,
            "chapter_id": 2,
            "book_slug": "test-book",
            "slug": "test-page",
            "revision_count": 3,
            "updated_at": "2024-06-01T12:00:00Z",
        }
        result = await bookstack_connector._process_bookstack_page(page, {}, [])
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "Test Page"

    async def test_process_bookstack_page_existing_updated(self, bookstack_connector, mock_data_store_provider):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.bookstack_base_url = "https://bs.example.com/"
        bookstack_connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(True, {
                "owner": None,
                "role_permissions": [],
                "fallback_permissions": {},
            })
        )
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Old Name"
        existing.external_revision_id = "1"
        existing.version = 2
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        page = {
            "id": 42, "name": "New Name", "book_id": 1, "book_slug": "b",
            "slug": "p", "revision_count": 5, "updated_at": "2024-06-01T12:00:00Z",
        }
        result = await bookstack_connector._process_bookstack_page(page, {}, [])
        assert result.is_updated is True
        assert result.metadata_changed is True
        assert result.content_changed is True

    async def test_process_bookstack_page_no_id(self, bookstack_connector):
        result = await bookstack_connector._process_bookstack_page({}, {}, [])
        assert result is None

    async def test_process_bookstack_page_exception(self, bookstack_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("DB error"))
        result = await bookstack_connector._process_bookstack_page(
            {"id": 1, "name": "test"}, {}, []
        )
        assert result is None


# ===========================================================================
# Handle record updates
# ===========================================================================
class TestBookStackHandleRecordUpdates:
    async def test_handle_deleted(self, bookstack_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=False,
            is_deleted=True, metadata_changed=False, content_changed=False,
            permissions_changed=False, external_record_id="page/1",
        )
        await bookstack_connector._handle_record_updates(update)
        bookstack_connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    async def test_handle_updated_metadata_and_content(self, bookstack_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=True,
            permissions_changed=True, new_permissions=[MagicMock()],
            external_record_id="page/1",
        )
        await bookstack_connector._handle_record_updates(update)
        bookstack_connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        bookstack_connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        bookstack_connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()

    async def test_handle_new_record(self, bookstack_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False, content_changed=False,
            permissions_changed=False,
        )
        await bookstack_connector._handle_record_updates(update)

    async def test_handle_update_exception(self, bookstack_connector):
        bookstack_connector.data_entities_processor.on_record_deleted = AsyncMock(side_effect=Exception("fail"))
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=False,
            is_deleted=True, metadata_changed=False, content_changed=False,
            permissions_changed=False, external_record_id="page/1",
        )
        # Should not raise
        await bookstack_connector._handle_record_updates(update)


# ===========================================================================
# Full run_sync flow
# ===========================================================================
class TestBookStackRunSync:
    @patch("app.connectors.sources.bookstack.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_full_flow(self, mock_filters, bookstack_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        bookstack_connector._sync_users = AsyncMock()
        bookstack_connector._sync_user_roles = AsyncMock()
        bookstack_connector._sync_record_groups = AsyncMock()
        bookstack_connector._sync_records = AsyncMock()
        await bookstack_connector.run_sync()
        bookstack_connector._sync_users.assert_awaited_once()
        bookstack_connector._sync_user_roles.assert_awaited_once()
        bookstack_connector._sync_record_groups.assert_awaited_once()
        bookstack_connector._sync_records.assert_awaited_once()

    @patch("app.connectors.sources.bookstack.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_exception_propagated(self, mock_filters, bookstack_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        bookstack_connector._sync_users = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(Exception, match="boom"):
            await bookstack_connector.run_sync()


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

    async def test_handle_user_delete_event_user_found(self, bookstack_connector):
        events = [{"detail": "(5) OldUser"}]
        from app.models.entities import AppUser
        app_users = []
        await bookstack_connector._handle_user_delete_event(events, app_users)
        bookstack_connector.data_entities_processor.on_user_removed.assert_awaited()

    async def test_handle_user_delete_event_user_not_found(self, bookstack_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_user_by_user_id = AsyncMock(return_value=None)
        events = [{"detail": "(5) Ghost"}]
        await bookstack_connector._handle_user_delete_event(events, [])

    async def test_handle_user_delete_event_no_email(self, bookstack_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": None})
        events = [{"detail": "(5) NoEmail"}]
        await bookstack_connector._handle_user_delete_event(events, [])

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
        bookstack_connector._handle_role_create_event = AsyncMock()
        events = [{"detail": "(5) TestUser"}]
        app_users = [
            AppUser(
                app_name=Connectors.BOOKSTACK, connector_id="bs-conn-1",
                source_user_id="5", email="test@t.com", full_name="TestUser", is_active=True,
            ),
        ]
        await bookstack_connector._handle_user_upsert_event(events, app_users)
        bookstack_connector._handle_role_create_event.assert_awaited()


# ===========================================================================
# Role event handling
# ===========================================================================
class TestBookStackRoleEvents:
    async def test_handle_role_create_event(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.get_role = AsyncMock(
            return_value=_make_response(True, {
                "id": 1, "display_name": "Admin", "users": [{"id": 10}],
            })
        )
        bookstack_connector.data_source.get_user = AsyncMock(
            return_value=_make_response(True, {"id": 10, "name": "A", "email": "a@t.com"})
        )
        await bookstack_connector._handle_role_create_event(1, {10: "a@t.com"})
        bookstack_connector.data_entities_processor.on_new_app_roles.assert_awaited()

    async def test_handle_role_create_event_none_id(self, bookstack_connector):
        await bookstack_connector._handle_role_create_event(None, {})
        bookstack_connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    async def test_handle_role_create_event_fetch_fails(self, bookstack_connector):
        bookstack_connector.data_source = MagicMock()
        bookstack_connector.data_source.get_role = AsyncMock(
            return_value=_make_response(False, error="Not found")
        )
        await bookstack_connector._handle_role_create_event(1, {})
        bookstack_connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    async def test_handle_role_delete_event(self, bookstack_connector):
        await bookstack_connector._handle_role_delete_event(42)
        bookstack_connector.data_entities_processor.on_app_role_deleted.assert_awaited_once()

    async def test_handle_role_update_event(self, bookstack_connector):
        bookstack_connector._handle_role_delete_event = AsyncMock()
        bookstack_connector._handle_role_create_event = AsyncMock()
        bookstack_connector._sync_record_groups = AsyncMock()
        bookstack_connector._sync_records = AsyncMock()
        await bookstack_connector._handle_role_update_event(1, {})
        bookstack_connector._handle_role_delete_event.assert_awaited_once()
        bookstack_connector._handle_role_create_event.assert_awaited_once()


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


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_page_list_response(pages, total=None):
    if total is None:
        total = len(pages)
    content = json.dumps({"data": pages, "total": total})
    return _make_response(data={"content": content})


class TestInitSuccess:
    @pytest.mark.asyncio
    async def test_init_success(self, connector):
        with patch(
            "app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate",
            new_callable=AsyncMock,
        ) as mock_build:
            mock_build.return_value = MagicMock()
            with patch("app.connectors.sources.bookstack.connector.BookStackDataSource"):
                result = await connector.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_client_validation_fails(self, connector):
        with patch(
            "app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate",
            new_callable=AsyncMock,
            side_effect=ValueError("invalid config"),
        ):
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
            return_value=_make_response(data={"data": [{"id": 1}, {"id": 2}]})
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


class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_full_sync(self, connector):
        connector.data_source = AsyncMock()
        connector.user_sync_point = AsyncMock()
        connector.user_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.user_sync_point.update_sync_point = AsyncMock()
        connector.get_all_users = AsyncMock(return_value=[
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="1",
                    email="u@test.com", full_name="User", is_active=True)
        ])
        connector._sync_users_full = AsyncMock()

        await connector._sync_users()
        connector._sync_users_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync(self, connector):
        connector.data_source = AsyncMock()
        connector.user_sync_point = AsyncMock()
        connector.user_sync_point.read_sync_point = AsyncMock(
            return_value={"timestamp": "2025-01-01T00:00:00Z"}
        )
        connector.user_sync_point.update_sync_point = AsyncMock()
        connector.get_all_users = AsyncMock(return_value=[])
        connector._sync_users_incremental = AsyncMock()

        await connector._sync_users()
        connector._sync_users_incremental.assert_awaited_once()


class TestSyncUsersFull:
    @pytest.mark.asyncio
    async def test_calls_processor(self, connector, mock_data_entities_processor_fullcov):
        users = [MagicMock()]
        await connector._sync_users_full(users)
        mock_data_entities_processor_fullcov.on_new_app_users.assert_awaited_once_with(users)


class TestSyncUsersIncremental:
    @pytest.mark.asyncio
    async def test_processes_create_and_update_events(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": [{"detail": "(1) User1"}]}),
            _make_response(data={"data": [{"detail": "(2) User2"}]}),
            _make_response(data={"data": []}),
        ])
        connector._handle_user_upsert_event = AsyncMock()

        await connector._sync_users_incremental([], "2025-01-01T00:00:00Z")
        assert connector._handle_user_upsert_event.await_count == 2

    @pytest.mark.asyncio
    async def test_handles_delete_events_logged(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
            _make_response(data={"data": [{"detail": "(1) Deleted"}]}),
        ])
        connector._handle_user_upsert_event = AsyncMock()
        await connector._sync_users_incremental([], "2025-01-01T00:00:00Z")
        connector._handle_user_upsert_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_events(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_audit_log = AsyncMock(
            return_value=_make_response(data={"data": []})
        )
        connector._handle_user_upsert_event = AsyncMock()
        await connector._sync_users_incremental([], "2025-01-01T00:00:00Z")
        connector._handle_user_upsert_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_audit_log = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        connector._handle_user_upsert_event = AsyncMock()
        await connector._sync_users_incremental([], "2025-01-01T00:00:00Z")
        connector._handle_user_upsert_event.assert_not_awaited()


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


class TestHandleUserDeleteEvent:
    @pytest.mark.asyncio
    async def test_deletes_user(self, connector, mock_data_entities_processor_fullcov):
        events = [{"detail": "(5) Deleted User"}]
        await connector._handle_user_delete_event(events, [])
        mock_data_entities_processor_fullcov.on_user_removed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_not_found(self, connector, mock_data_store_provider_fullcov):
        mock_data_store_provider_fullcov._mock_tx.get_user_by_user_id = AsyncMock(return_value=None)
        events = [{"detail": "(99) Unknown"}]
        await connector._handle_user_delete_event(events, [])

    @pytest.mark.asyncio
    async def test_user_no_email(self, connector, mock_data_store_provider_fullcov):
        mock_data_store_provider_fullcov._mock_tx.get_user_by_user_id = AsyncMock(
            return_value={"email": None}
        )
        events = [{"detail": "(5) NoEmail"}]
        await connector._handle_user_delete_event(events, [])

    @pytest.mark.asyncio
    async def test_bad_event_no_detail(self, connector):
        events = [{}]
        await connector._handle_user_delete_event(events, [])

    @pytest.mark.asyncio
    async def test_removal_failure(self, connector, mock_data_entities_processor_fullcov):
        mock_data_entities_processor_fullcov.on_user_removed = AsyncMock(return_value=False)
        events = [{"detail": "(5) FailDelete"}]
        await connector._handle_user_delete_event(events, [])

    @pytest.mark.asyncio
    async def test_exception_during_delete(self, connector, mock_data_store_provider_fullcov):
        mock_data_store_provider_fullcov._mock_tx.get_user_by_user_id = AsyncMock(
            side_effect=Exception("db error")
        )
        events = [{"detail": "(5) Error"}]
        await connector._handle_user_delete_event(events, [])


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
        connector._handle_role_create_event = AsyncMock()

        await connector._handle_user_upsert_event(events, users)
        connector._handle_user_create_event.assert_awaited_once()
        connector._handle_role_create_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_with_no_roles(self, connector):
        events = [{"detail": "(5) NoRoles"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="u@test.com", full_name="NoRoles", is_active=True)
        ]
        connector._handle_user_create_event = AsyncMock()
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(return_value=_make_response(
            data={"id": 5, "email": "u@test.com", "roles": []}
        ))
        connector._handle_role_create_event = AsyncMock()

        await connector._handle_user_upsert_event(events, users)
        connector._handle_role_create_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_fetch_failure(self, connector):
        events = [{"detail": "(5) FailUser"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="u@test.com", full_name="FailUser", is_active=True)
        ]
        connector._handle_user_create_event = AsyncMock()
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        connector._handle_role_create_event = AsyncMock()

        await connector._handle_user_upsert_event(events, users)
        connector._handle_role_create_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_no_email_skips_roles(self, connector):
        events = [{"detail": "(5) NoEmail"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="u@test.com", full_name="NoEmail", is_active=True)
        ]
        connector._handle_user_create_event = AsyncMock()
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(return_value=_make_response(
            data={"id": 5, "email": None, "roles": [{"id": 1}]}
        ))
        connector._handle_role_create_event = AsyncMock()

        await connector._handle_user_upsert_event(events, users)
        connector._handle_role_create_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_not_found_in_db_skips(self, connector, mock_data_store_provider_fullcov):
        mock_data_store_provider_fullcov._mock_tx.get_user_by_email = AsyncMock(return_value=None)
        events = [{"detail": "(5) Ghost"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="u@test.com", full_name="Ghost", is_active=True)
        ]
        connector._handle_user_create_event = AsyncMock()
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(return_value=_make_response(
            data={"id": 5, "email": "u@test.com", "roles": [{"id": 1}]}
        ))
        connector._handle_role_create_event = AsyncMock()

        await connector._handle_user_upsert_event(events, users)

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


class TestSyncUserRoles:
    @pytest.mark.asyncio
    async def test_full_sync(self, connector):
        connector.app_role_sync_point = AsyncMock()
        connector.app_role_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.app_role_sync_point.update_sync_point = AsyncMock()
        connector._sync_user_roles_full = AsyncMock()

        await connector._sync_user_roles()
        connector._sync_user_roles_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync(self, connector):
        connector.app_role_sync_point = AsyncMock()
        connector.app_role_sync_point.read_sync_point = AsyncMock(
            return_value={"timestamp": "2025-01-01T00:00:00Z"}
        )
        connector.app_role_sync_point.update_sync_point = AsyncMock()
        connector._sync_user_roles_incremental = AsyncMock()

        await connector._sync_user_roles()
        connector._sync_user_roles_incremental.assert_awaited_once()


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


class TestSyncRecordGroups:
    @pytest.mark.asyncio
    async def test_full_sync(self, connector):
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector._sync_record_groups_full = AsyncMock()

        await connector._sync_record_groups()
        connector._sync_record_groups_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync(self, connector):
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"timestamp": "2025-01-01T00:00:00Z"}
        )
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector._sync_record_groups_incremental = AsyncMock()

        await connector._sync_record_groups()
        connector._sync_record_groups_incremental.assert_awaited_once()


class TestSyncRecordGroupsFull:
    @pytest.mark.asyncio
    async def test_calls_all_content_types(self, connector):
        connector._sync_content_type_as_record_group = AsyncMock(return_value=set())
        connector.data_source = AsyncMock()
        await connector._sync_record_groups_full({})
        assert connector._sync_content_type_as_record_group.await_count == 3


class TestSyncContentTypeAsRecordGroup:
    @pytest.mark.asyncio
    async def test_empty_items(self, connector):
        connector.data_source = AsyncMock()
        list_method = AsyncMock(return_value=_make_response(data={"data": [], "total": 0}))
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        result = await connector._sync_content_type_as_record_group(
            "book", list_method, {}
        )
        assert result == set()

    @pytest.mark.asyncio
    async def test_with_items(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        list_method = AsyncMock(return_value=_make_response(
            data={"data": [{"id": 1, "name": "Book1"}], "total": 1}
        ))
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._create_record_group_with_permissions = AsyncMock(
            return_value=(MagicMock(), [])
        )

        result = await connector._sync_content_type_as_record_group(
            "book", list_method, {}
        )
        assert 1 in result
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_book_id_filter_in(self, connector):
        connector.data_source = AsyncMock()
        list_method = AsyncMock(return_value=_make_response(
            data={"data": [{"id": 1, "name": "B1"}, {"id": 2, "name": "B2"}], "total": 2}
        ))
        mock_op = MagicMock()
        mock_op.value = "in"
        connector._get_book_id_filter = MagicMock(return_value=({1}, mock_op))
        connector._create_record_group_with_permissions = AsyncMock(
            return_value=(MagicMock(), [])
        )

        result = await connector._sync_content_type_as_record_group(
            "book", list_method, {}
        )
        assert 1 in result
        assert connector._create_record_group_with_permissions.await_count == 1

    @pytest.mark.asyncio
    async def test_chapter_parent_filter(self, connector):
        connector.data_source = AsyncMock()
        list_method = AsyncMock(return_value=_make_response(
            data={"data": [
                {"id": 1, "name": "Ch1", "book_id": 10},
                {"id": 2, "name": "Ch2", "book_id": 20},
            ], "total": 2}
        ))
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._create_record_group_with_permissions = AsyncMock(
            return_value=(MagicMock(), [])
        )

        result = await connector._sync_content_type_as_record_group(
            "chapter", list_method, {}, parent_filter_ids={10}
        )
        assert connector._create_record_group_with_permissions.await_count == 1

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        list_method = AsyncMock(return_value=_make_response(success=False, error="err"))
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        result = await connector._sync_content_type_as_record_group(
            "bookshelf", list_method, {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_all_permissions_fail(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        list_method = AsyncMock(return_value=_make_response(
            data={"data": [{"id": 1, "name": "B1"}], "total": 1}
        ))
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._create_record_group_with_permissions = AsyncMock(return_value=None)

        await connector._sync_content_type_as_record_group("book", list_method, {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()


class TestCreateRecordGroupWithPermissions:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={"role_permissions": [], "owner": None})
        )
        connector._parse_bookstack_permissions = AsyncMock(return_value=[])
        item = {"id": 1, "name": "TestBook", "description": "desc"}
        result = await connector._create_record_group_with_permissions(
            item, "book", {}, None
        )
        assert result is not None
        assert result[0].name == "TestBook"

    @pytest.mark.asyncio
    async def test_missing_id(self, connector):
        result = await connector._create_record_group_with_permissions(
            {"name": "NoId"}, "book", {}, None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_name(self, connector):
        result = await connector._create_record_group_with_permissions(
            {"id": 1}, "book", {}, None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_content_permissions = AsyncMock(
            side_effect=Exception("boom")
        )
        result = await connector._create_record_group_with_permissions(
            {"id": 1, "name": "Fail"}, "book", {}, None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_with_parent_id(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={
                "role_permissions": [],
                "owner": None,
                "fallback_permissions": {"inheriting": True},
            })
        )
        connector._parse_bookstack_permissions = AsyncMock(return_value=[])
        item = {"id": 1, "name": "Chapter1", "description": ""}
        result = await connector._create_record_group_with_permissions(
            item, "chapter", {}, "book/10"
        )
        assert result is not None
        assert result[0].inherit_permissions is True

    @pytest.mark.asyncio
    async def test_permissions_fetch_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(success=False, error="denied")
        )
        item = {"id": 1, "name": "Book1", "description": ""}
        result = await connector._create_record_group_with_permissions(
            item, "book", {}, None
        )
        assert result is not None
        assert result[1] == []


class TestParseBookstackPermissions:
    @pytest.mark.asyncio
    async def test_with_owner(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(
            return_value=_make_response(data={"email": "owner@test.com"})
        )
        data = {
            "owner": {"id": 1},
            "role_permissions": [],
            "fallback_permissions": {},
        }
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 1
        assert result[0].type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_owner_no_email(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(
            return_value=_make_response(data={"email": None})
        )
        data = {"owner": {"id": 1}, "role_permissions": [], "fallback_permissions": {}}
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_owner_fetch_exception(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.get_user = AsyncMock(side_effect=Exception("fail"))
        data = {"owner": {"id": 1}, "role_permissions": [], "fallback_permissions": {}}
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_explicit_role_permissions_read(self, connector):
        data = {
            "owner": None,
            "role_permissions": [
                {"role_id": 1, "view": True, "update": False, "delete": False, "create": False},
            ],
            "fallback_permissions": {},
        }
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 1
        assert result[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_explicit_role_permissions_write(self, connector):
        data = {
            "owner": None,
            "role_permissions": [
                {"role_id": 2, "view": True, "update": True, "delete": False, "create": False},
            ],
            "fallback_permissions": {},
        }
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 1
        assert result[0].type == PermissionType.WRITE

    @pytest.mark.asyncio
    async def test_explicit_role_no_view(self, connector):
        data = {
            "owner": None,
            "role_permissions": [
                {"role_id": 3, "view": False, "update": False, "delete": False, "create": False},
            ],
            "fallback_permissions": {},
        }
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_explicit_role_no_role_id(self, connector):
        data = {
            "owner": None,
            "role_permissions": [
                {"view": True, "update": False, "delete": False, "create": False},
            ],
            "fallback_permissions": {},
        }
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_fallback_inheriting_book_read(self, connector):
        roles_details = {
            1: {"permissions": ["book-view-all"]},
        }
        data = {
            "owner": None,
            "role_permissions": [],
            "fallback_permissions": {"inheriting": True},
        }
        result = await connector._parse_bookstack_permissions(data, roles_details, "book")
        assert len(result) == 1
        assert result[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_fallback_inheriting_book_write(self, connector):
        roles_details = {
            1: {"permissions": ["book-create-all", "book-update-all"]},
        }
        data = {
            "owner": None,
            "role_permissions": [],
            "fallback_permissions": {"inheriting": True},
        }
        result = await connector._parse_bookstack_permissions(data, roles_details, "book")
        assert len(result) == 1
        assert result[0].type == PermissionType.WRITE

    @pytest.mark.asyncio
    async def test_fallback_inheriting_book_no_permissions(self, connector):
        roles_details = {
            1: {"permissions": ["something-else"]},
        }
        data = {
            "owner": None,
            "role_permissions": [],
            "fallback_permissions": {"inheriting": True},
        }
        result = await connector._parse_bookstack_permissions(data, roles_details, "book")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_fallback_inheriting_non_book(self, connector):
        roles_details = {1: {"permissions": ["chapter-view-all"]}}
        data = {
            "owner": None,
            "role_permissions": [],
            "fallback_permissions": {"inheriting": True},
        }
        result = await connector._parse_bookstack_permissions(data, roles_details, "chapter")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_no_owner_no_roles_no_fallback(self, connector):
        data = {"owner": None, "role_permissions": [], "fallback_permissions": {}}
        result = await connector._parse_bookstack_permissions(data, {}, "book")
        assert len(result) == 0


class TestSyncRecords:
    @pytest.mark.asyncio
    async def test_full_sync_route(self, connector):
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector.get_all_users = AsyncMock(return_value=[])
        connector._sync_records_full = AsyncMock()

        await connector._sync_records()
        connector._sync_records_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync_route(self, connector):
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"timestamp": "2025-01-01T00:00:00Z"}
        )
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector.get_all_users = AsyncMock(return_value=[])
        connector._sync_records_incremental = AsyncMock()

        await connector._sync_records()
        connector._sync_records_incremental.assert_awaited_once()


class TestSyncRecordsFull:
    @pytest.mark.asyncio
    async def test_processes_pages(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)

        page = {"id": 1, "name": "Page1", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(side_effect=[
            _make_page_list_response([page], total=1),
            _make_page_list_response([], total=1),
        ])

        mock_update = RecordUpdate(
            record=MagicMock(indexing_status=None), is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False, content_changed=False,
            permissions_changed=False, new_permissions=[],
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)

        await connector._sync_records_full({}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_empty_pages(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([], total=0)
        )
        await connector._sync_records_full({}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deleted_record_handled(self, connector):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)

        connector.data_source.list_pages = AsyncMock(side_effect=[
            _make_page_list_response([{"id": 1, "name": "Del"}], total=1),
            _make_page_list_response([], total=1),
        ])

        mock_update = RecordUpdate(
            record=None, is_new=False, is_updated=False,
            is_deleted=True, metadata_changed=False, content_changed=False,
            permissions_changed=False, external_record_id="page/1",
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        connector._handle_record_updates = AsyncMock()

        await connector._sync_records_full({}, [])
        connector._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updated_record_handled(self, connector):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)

        connector.data_source.list_pages = AsyncMock(side_effect=[
            _make_page_list_response([{"id": 1, "name": "Updated"}], total=1),
            _make_page_list_response([], total=1),
        ])

        mock_update = RecordUpdate(
            record=MagicMock(record_name="Updated"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=False,
            permissions_changed=False,
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        connector._handle_record_updates = AsyncMock()

        await connector._sync_records_full({}, [])
        connector._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_returns_none(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)

        connector.data_source.list_pages = AsyncMock(side_effect=[
            _make_page_list_response([{"id": 1, "name": "Bad"}], total=1),
            _make_page_list_response([], total=1),
        ])
        connector._process_bookstack_page = AsyncMock(return_value=None)

        await connector._sync_records_full({}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_failure(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        await connector._sync_records_full({}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_book_id_filter_applied(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        mock_op = MagicMock()
        mock_op.value = "in"
        connector._get_book_id_filter = MagicMock(return_value=({10}, mock_op))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._build_date_filter_params = MagicMock(return_value=None)

        pages = [
            {"id": 1, "name": "P1", "book_id": 10},
            {"id": 2, "name": "P2", "book_id": 20},
        ]
        connector.data_source.list_pages = AsyncMock(side_effect=[
            _make_page_list_response(pages, total=2),
            _make_page_list_response([], total=2),
        ])

        mock_update = RecordUpdate(
            record=MagicMock(indexing_status=None), is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False, content_changed=False,
            permissions_changed=False, new_permissions=[],
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)

        await connector._sync_records_full({}, [])
        assert connector._process_bookstack_page.await_count == 1


class TestHandlePageUpsertEvent:
    @pytest.mark.asyncio
    async def test_new_page(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "NewPage", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        mock_update = RecordUpdate(
            record=MagicMock(indexing_status=None, record_name="NewPage"),
            is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False, content_changed=False,
            permissions_changed=False, new_permissions=[],
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        connector._pass_date_filters = MagicMock(return_value=True)

        event = {"detail": "(42) NewPage"}
        await connector._handle_page_upsert_event(event, {}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_bad_event(self, connector, mock_data_entities_processor_fullcov):
        event = {}
        await connector._handle_page_upsert_event(event, {}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_date_filter_blocks(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "FilteredPage", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        connector._pass_date_filters = MagicMock(return_value=False)

        event = {"detail": "(42) FilteredPage"}
        await connector._handle_page_upsert_event(event, {}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_failure(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        event = {"detail": "(42) FailPage"}
        await connector._handle_page_upsert_event(event, {}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updated_page(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "UpdatedPage", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        mock_update = RecordUpdate(
            record=MagicMock(indexing_status=None, record_name="UpdatedPage"),
            is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=False,
            permissions_changed=False, new_permissions=[],
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._handle_record_updates = AsyncMock()

        event = {"detail": "(42) UpdatedPage"}
        await connector._handle_page_upsert_event(event, {}, [])
        connector._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_content_response(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_response(data={"content": None})
        )
        event = {"detail": "(42) Empty"}
        await connector._handle_page_upsert_event(event, {}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_book_id_filter_blocks(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "Blocked", "book_id": 99}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        connector._pass_date_filters = MagicMock(return_value=True)
        mock_op = MagicMock()
        mock_op.value = "in"

        event = {"detail": "(42) Blocked"}
        await connector._handle_page_upsert_event(
            event, {}, [], book_ids={10}, book_ids_operator=mock_op
        )
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_returns_none(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "None", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        connector._process_bookstack_page = AsyncMock(return_value=None)
        connector._pass_date_filters = MagicMock(return_value=True)

        event = {"detail": "(42) None"}
        await connector._handle_page_upsert_event(event, {}, [])
        mock_data_entities_processor_fullcov.on_new_records.assert_not_awaited()


class TestHandleRecordGroupCreateEvent:
    @pytest.mark.asyncio
    async def test_creates_book(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_book = AsyncMock(
            return_value=_make_response(data={"id": 5, "name": "NewBook", "description": ""})
        )
        connector._create_record_group_with_permissions = AsyncMock(
            return_value=(MagicMock(), [])
        )
        event = {"detail": "(5) NewBook"}
        await connector._handle_record_group_create_event(event, "book", {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bad_event(self, connector, mock_data_entities_processor_fullcov):
        event = {}
        await connector._handle_record_group_create_event(event, "book", {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_failure(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_book = AsyncMock(
            return_value=_make_response(success=False, error="not found")
        )
        event = {"detail": "(5) Missing"}
        await connector._handle_record_group_create_event(event, "book", {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_book_id_filter_blocks(self, connector, mock_data_entities_processor_fullcov):
        mock_op = MagicMock()
        mock_op.value = "in"
        event = {"detail": "(99) Filtered"}
        await connector._handle_record_group_create_event(
            event, "book", {}, book_ids={1, 2}, book_ids_operator=mock_op
        )
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chapter_with_book_filter(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_chapter = AsyncMock(
            return_value=_make_response(data={"id": 5, "name": "Ch1", "book_id": 99, "description": ""})
        )
        mock_op = MagicMock()
        mock_op.value = "in"
        event = {"detail": "(5) Ch1"}
        await connector._handle_record_group_create_event(
            event, "chapter", {}, book_ids={1}, book_ids_operator=mock_op
        )
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_shelf(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_shelf = AsyncMock(
            return_value=_make_response(data={"id": 3, "name": "Shelf1", "description": ""})
        )
        connector._create_record_group_with_permissions = AsyncMock(
            return_value=(MagicMock(), [])
        )
        event = {"detail": "(3) Shelf1"}
        await connector._handle_record_group_create_event(event, "bookshelf", {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_content_type(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        event = {"detail": "(1) Invalid"}
        await connector._handle_record_group_create_event(event, "unknown_type", {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_permission_result_none(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_book = AsyncMock(
            return_value=_make_response(data={"id": 5, "name": "Book", "description": ""})
        )
        connector._create_record_group_with_permissions = AsyncMock(return_value=None)
        event = {"detail": "(5) Book"}
        await connector._handle_record_group_create_event(event, "book", {})
        mock_data_entities_processor_fullcov.on_new_record_groups.assert_not_awaited()


class TestHandleRecordGroupDeleteEvent:
    @pytest.mark.asyncio
    async def test_logs_not_implemented(self, connector):
        await connector._handle_record_group_delete_event({"detail": "(1) Del"}, "book")


class TestSyncRecordGroupsIncremental:
    @pytest.mark.asyncio
    async def test_calls_events_for_all_types(self, connector):
        connector._sync_record_groups_events = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        await connector._sync_record_groups_incremental("2025-01-01T00:00:00Z", {})
        assert connector._sync_record_groups_events.await_count == 3


class TestSyncRecordGroupsEvents:
    @pytest.mark.asyncio
    async def test_processes_create_events(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": [{"detail": "(1) NewBook"}]}),
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
        ])
        connector._handle_record_group_create_event = AsyncMock()
        connector._handle_record_group_delete_event = AsyncMock()

        await connector._sync_record_groups_events("book", {}, "2025-01-01T00:00:00Z")
        connector._handle_record_group_create_event.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_events(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_audit_log = AsyncMock(
            return_value=_make_response(data={"data": []})
        )
        connector._handle_record_group_create_event = AsyncMock()
        connector._handle_record_group_delete_event = AsyncMock()

        await connector._sync_record_groups_events("book", {}, "2025-01-01T00:00:00Z")
        connector._handle_record_group_create_event.assert_not_awaited()


class TestSyncRecordsIncremental:
    @pytest.mark.asyncio
    async def test_processes_create_events(self, connector):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._handle_page_upsert_event = AsyncMock()

        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": [{"detail": "(1) Created"}]}),
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
        ])

        await connector._sync_records_incremental("2025-01-01T00:00:00Z", {}, [])
        connector._handle_page_upsert_event.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_events(self, connector):
        connector.data_source = AsyncMock()
        connector._get_book_id_filter = MagicMock(return_value=(None, None))
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))
        connector._handle_page_upsert_event = AsyncMock()

        connector.data_source.list_audit_log = AsyncMock(
            return_value=_make_response(data={"data": []})
        )

        await connector._sync_records_incremental("2025-01-01T00:00:00Z", {}, [])
        connector._handle_page_upsert_event.assert_not_awaited()


class TestRunSync:
    @pytest.mark.asyncio
    async def test_full_sync_flow(self, connector):
        connector._sync_users = AsyncMock()
        connector._sync_user_roles = AsyncMock()
        connector._sync_record_groups = AsyncMock()
        connector._sync_records = AsyncMock()

        with patch(
            "app.connectors.sources.bookstack.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.run_sync()

        connector._sync_users.assert_awaited_once()
        connector._sync_user_roles.assert_awaited_once()
        connector._sync_record_groups.assert_awaited_once()
        connector._sync_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_propagates(self, connector):
        connector._sync_users = AsyncMock(side_effect=Exception("sync error"))

        with patch(
            "app.connectors.sources.bookstack.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            with pytest.raises(Exception, match="sync error"):
                await connector.run_sync()


class TestSyncUserRolesIncremental:
    @pytest.mark.asyncio
    async def test_processes_role_create_events(self, connector):
        connector.data_source = AsyncMock()
        connector._fetch_all_users_with_details = AsyncMock(return_value=[
            {"id": 1, "email": "u@test.com"}
        ])
        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": [{"detail": "(1) NewRole"}]}),
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
        ])
        connector._handle_role_create_event = AsyncMock()
        connector._handle_role_update_event = AsyncMock()
        connector._handle_role_delete_event = AsyncMock()
        connector._sync_user_roles_full = AsyncMock()

        await connector._sync_user_roles_incremental("2025-01-01T00:00:00Z")
        connector._handle_role_create_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_processes_role_delete_events(self, connector):
        connector.data_source = AsyncMock()
        connector._fetch_all_users_with_details = AsyncMock(return_value=[])
        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": []}),
            _make_response(data={"data": []}),
            _make_response(data={"data": [{"detail": "(1) Deleted"}]}),
        ])
        connector._handle_role_create_event = AsyncMock()
        connector._handle_role_update_event = AsyncMock()
        connector._handle_role_delete_event = AsyncMock()
        connector._sync_user_roles_full = AsyncMock()

        await connector._sync_user_roles_incremental("2025-01-01T00:00:00Z")
        connector._handle_role_delete_event.assert_awaited_once()
        connector._sync_user_roles_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_processes_role_update_events(self, connector):
        connector.data_source = AsyncMock()
        connector._fetch_all_users_with_details = AsyncMock(return_value=[])
        connector.data_source.list_audit_log = AsyncMock(side_effect=[
            _make_response(data={"data": []}),
            _make_response(data={"data": [{"detail": "(1) Updated"}]}),
            _make_response(data={"data": []}),
        ])
        connector._handle_role_create_event = AsyncMock()
        connector._handle_role_update_event = AsyncMock()
        connector._handle_role_delete_event = AsyncMock()

        await connector._sync_user_roles_incremental("2025-01-01T00:00:00Z")
        connector._handle_role_update_event.assert_awaited_once()


class TestHandleRoleCreateEvent:
    @pytest.mark.asyncio
    async def test_none_role_id(self, connector):
        await connector._handle_role_create_event(None, {})

    @pytest.mark.asyncio
    async def test_success_no_users(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_role = AsyncMock(
            return_value=_make_response(data={"id": 1, "display_name": "Admin", "users": []})
        )
        await connector._handle_role_create_event(1, {})
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_with_users(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_role = AsyncMock(
            return_value=_make_response(data={
                "id": 1, "display_name": "Admin",
                "users": [{"id": 10}]
            })
        )
        connector.data_source.get_user = AsyncMock(
            return_value=_make_response(data={
                "id": 10, "email": "u@test.com", "name": "User"
            })
        )
        await connector._handle_role_create_event(1, {10: "u@test.com"})
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_failure(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_role = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        await connector._handle_role_create_event(1, {})
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_detail_fetch_failure(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.data_source.get_role = AsyncMock(
            return_value=_make_response(data={
                "id": 1, "display_name": "Admin",
                "users": [{"id": 10}]
            })
        )
        connector.data_source.get_user = AsyncMock(side_effect=Exception("fail"))
        await connector._handle_role_create_event(1, {})
        mock_data_entities_processor_fullcov.on_new_app_roles.assert_awaited_once()


class TestHandleRoleDeleteEvent:
    @pytest.mark.asyncio
    async def test_success(self, connector, mock_data_entities_processor_fullcov):
        await connector._handle_role_delete_event(1)
        mock_data_entities_processor_fullcov.on_app_role_deleted.assert_awaited_once()


class TestHandleRoleUpdateEvent:
    @pytest.mark.asyncio
    async def test_delegates(self, connector):
        connector._handle_role_delete_event = AsyncMock()
        connector._handle_role_create_event = AsyncMock()
        connector._sync_record_groups = AsyncMock()
        connector._sync_records = AsyncMock()

        await connector._handle_role_update_event(1, {})
        connector._handle_role_delete_event.assert_awaited_once()
        connector._handle_role_create_event.assert_awaited_once()
        connector._sync_record_groups.assert_awaited_once()
        connector._sync_records.assert_awaited_once()


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self, connector):
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(Exception):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_with_updated_records(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector.get_all_users = AsyncMock(return_value=[])
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(MagicMock(), [])
        )
        record = MagicMock()
        record.id = "r1"
        await connector.reindex_records([record])
        mock_data_entities_processor_fullcov.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_with_non_updated_records(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector.get_all_users = AsyncMock(return_value=[])
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        record = MagicMock()
        record.id = "r1"
        await connector.reindex_records([record])
        mock_data_entities_processor_fullcov.reindex_existing_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_check_raises_continues(self, connector, mock_data_entities_processor_fullcov):
        connector.data_source = AsyncMock()
        connector.list_roles_with_details = AsyncMock(return_value={})
        connector.get_all_users = AsyncMock(return_value=[])
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("fail"))
        record = MagicMock()
        record.id = "r1"
        await connector.reindex_records([record])


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_updated_record(self, connector):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "Updated", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        mock_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=True,
            permissions_changed=False, new_permissions=[],
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_updated(self, connector):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "Same", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        mock_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False,
            is_deleted=False, metadata_changed=False, content_changed=False,
            permissions_changed=False,
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_external_id(self, connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "invalid"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_external_id(self, connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = None
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_response(success=False, error="fail")
        )
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_record_returns_none(self, connector):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "Deleted", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        mock_update = RecordUpdate(
            record=None, is_new=False, is_updated=False,
            is_deleted=True, metadata_changed=False, content_changed=False,
            permissions_changed=False,
        )
        connector._process_bookstack_page = AsyncMock(return_value=mock_update)
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_process_returns_none(self, connector):
        connector.data_source = AsyncMock()
        page_data = {"id": 42, "name": "None", "book_id": 10}
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([page_data])
        )
        connector._process_bookstack_page = AsyncMock(return_value=None)
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_page_list(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_pages = AsyncMock(
            return_value=_make_page_list_response([])
        )
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_pages = AsyncMock(side_effect=Exception("boom"))
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "page/42"
        result = await connector._check_and_fetch_updated_record("org1", record, {}, [])
        assert result is None


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


class TestProcessBookstackPageException:
    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = MagicMock(
            side_effect=Exception("DB fail")
        )
        page = {"id": 1, "name": "FailPage"}
        result = await connector._process_bookstack_page(page, {}, [])
        assert result is None


class TestParseBookstackPermissionsAllUsersEdgeCases:
    def test_user_without_email(self, connector):
        users = [MagicMock(email=None, source_user_id="1", full_name="NoEmail")]
        perms = connector._parse_bookstack_permissions_all_users(users)
        assert len(perms) == 0

    def test_user_without_source_id(self, connector):
        users = [MagicMock(email="u@test.com", source_user_id=None, full_name="NoId")]
        perms = connector._parse_bookstack_permissions_all_users(users)
        assert len(perms) == 0

    def test_both_valid(self, connector):
        users = [MagicMock(email="u@test.com", source_user_id="1", full_name="Valid")]
        perms = connector._parse_bookstack_permissions_all_users(users)
        assert len(perms) == 1
        assert perms[0].type == PermissionType.READ
