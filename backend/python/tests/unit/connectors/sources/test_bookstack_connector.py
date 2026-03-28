"""Tests for BookStack connector."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.bookstack.connector import BookStackConnector, RecordUpdate


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
