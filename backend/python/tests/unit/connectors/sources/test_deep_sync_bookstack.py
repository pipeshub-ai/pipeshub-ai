"""Deep sync loop tests for BookStackConnector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import AppUser, FileRecord, RecordType
from app.models.permission import PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    return logging.getLogger("test_bookstack_deep")


def _make_mock_data_store_provider(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_page(page_id=1, name="Test Page", book_id=10, chapter_id=5, slug="test-page",
               book_slug="test-book", revision_count=1, updated_at="2025-01-01T00:00:00Z"):
    return {
        "id": page_id,
        "name": name,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "slug": slug,
        "book_slug": book_slug,
        "revision_count": revision_count,
        "updated_at": updated_at,
    }


def _make_app_user(email="user@t.com", user_id="1", full_name="User"):
    return AppUser(
        app_name=Connectors.BOOKSTACK,
        connector_id="bs-1",
        source_user_id=user_id,
        email=email,
        full_name=full_name,
        is_active=True,
    )


@pytest.fixture
def connector():
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"), \
         patch("app.connectors.sources.bookstack.connector.SyncPoint") as MockSP:
        mock_sp = AsyncMock()
        mock_sp.read_sync_point = AsyncMock(return_value={})
        mock_sp.update_sync_point = AsyncMock()
        MockSP.return_value = mock_sp

        from app.connectors.sources.bookstack.connector import BookStackConnector

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-bs"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_user_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()

        conn = BookStackConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="bs-1",
            scope="team",
            created_by="test-user",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.data_source = AsyncMock()
        conn.bookstack_base_url = "https://bookstack.example.com"
        conn.connector_name = "BookStack"
        yield conn


# ---------------------------------------------------------------------------
# run_sync orchestration
# ---------------------------------------------------------------------------

class TestBookStackRunSync:
    async def test_run_sync_calls_all_steps(self, connector):
        with patch.object(connector, "_sync_users", new_callable=AsyncMock) as m_users, \
             patch.object(connector, "_sync_user_roles", new_callable=AsyncMock) as m_roles, \
             patch.object(connector, "_sync_record_groups", new_callable=AsyncMock) as m_rg, \
             patch.object(connector, "_sync_records", new_callable=AsyncMock) as m_rec, \
             patch("app.connectors.sources.bookstack.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            await connector.run_sync()
            m_users.assert_called_once()
            m_roles.assert_called_once()
            m_rg.assert_called_once()
            m_rec.assert_called_once()

    async def test_run_sync_propagates_errors(self, connector):
        with patch.object(connector, "_sync_users", new_callable=AsyncMock,
                          side_effect=ValueError("bad")), \
             patch("app.connectors.sources.bookstack.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            with pytest.raises(ValueError, match="bad"):
                await connector.run_sync()


# ---------------------------------------------------------------------------
# _process_bookstack_page
# ---------------------------------------------------------------------------

class TestProcessBookstackPage:
    async def test_new_page(self, connector):
        page = _make_page()
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={"permissions": []})
        )
        with patch.object(connector, "_parse_bookstack_permissions", new_callable=AsyncMock, return_value=[]):
            result = await connector._process_bookstack_page(page, {}, [])
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "Test Page"
        assert result.record.external_record_id == "page/1"

    async def test_page_without_id_returns_none(self, connector):
        page = {"name": "No ID"}
        result = await connector._process_bookstack_page(page, {}, [])
        assert result is None

    async def test_existing_page_name_change(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Old Name"
        existing.external_revision_id = "1"
        existing.version = 1

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        page = _make_page(name="New Name")
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={"permissions": []})
        )
        with patch.object(connector, "_parse_bookstack_permissions", new_callable=AsyncMock, return_value=[]):
            result = await connector._process_bookstack_page(page, {}, [])
        assert result.metadata_changed is True
        assert result.is_updated is True

    async def test_existing_page_revision_change(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Test Page"
        existing.external_revision_id = "1"
        existing.version = 1

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        page = _make_page(revision_count=5)
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={"permissions": []})
        )
        with patch.object(connector, "_parse_bookstack_permissions", new_callable=AsyncMock, return_value=[]):
            result = await connector._process_bookstack_page(page, {}, [])
        assert result.content_changed is True

    async def test_page_with_chapter_parent(self, connector):
        page = _make_page(chapter_id=42)
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={"permissions": []})
        )
        with patch.object(connector, "_parse_bookstack_permissions", new_callable=AsyncMock, return_value=[]):
            result = await connector._process_bookstack_page(page, {}, [])
        assert result.record.external_record_group_id == "chapter/42"

    async def test_page_book_only_parent(self, connector):
        page = _make_page(chapter_id=None)
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={"permissions": []})
        )
        with patch.object(connector, "_parse_bookstack_permissions", new_callable=AsyncMock, return_value=[]):
            result = await connector._process_bookstack_page(page, {}, [])
        assert result.record.external_record_group_id == "book/10"

    async def test_page_permissions_fetched(self, connector):
        page = _make_page()
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(data={
                "permissions": [{"perm": "read"}],
                "fallback_permissions": {"inheriting": False},
            })
        )
        fake_perms = [MagicMock()]
        with patch.object(connector, "_parse_bookstack_permissions", new_callable=AsyncMock, return_value=fake_perms):
            result = await connector._process_bookstack_page(page, {}, [])
        assert result.record.inherit_permissions is False
        assert len(result.new_permissions) == 1

    async def test_page_permissions_failure(self, connector):
        page = _make_page()
        connector.data_source.get_content_permissions = AsyncMock(
            return_value=_make_response(success=False, error="denied")
        )
        result = await connector._process_bookstack_page(page, {}, [])
        assert result is not None
        assert result.new_permissions == []

    async def test_page_exception_returns_none(self, connector):
        connector.data_store_provider = MagicMock()

        @asynccontextmanager
        async def _fail():
            raise RuntimeError("db error")
            yield  # noqa

        connector.data_store_provider.transaction = _fail
        result = await connector._process_bookstack_page(_make_page(), {}, [])
        assert result is None


# ---------------------------------------------------------------------------
# _handle_record_updates
# ---------------------------------------------------------------------------

class TestBookstackHandleRecordUpdates:
    async def test_deleted(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="page/1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once()

    async def test_new_record_logs_only(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "new.md"
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="page/1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_not_called()

    async def test_metadata_change(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "renamed.md"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
            external_record_id="page/1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()

    async def test_content_change(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "file.md"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=False,
            external_record_id="page/1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_called_once()

    async def test_permissions_change(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "file.md"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=[MagicMock()], external_record_id="page/1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_called_once()

    async def test_exception_handled(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=RuntimeError("err")
        )
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="page/1",
        )
        await connector._handle_record_updates(update)  # Should not raise


# ---------------------------------------------------------------------------
# _sync_users
# ---------------------------------------------------------------------------

class TestBookstackSyncUsers:
    async def test_full_sync_when_no_timestamp(self, connector):
        connector.user_sync_point = AsyncMock()
        connector.user_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.user_sync_point.update_sync_point = AsyncMock()
        with patch.object(connector, "get_all_users", new_callable=AsyncMock, return_value=[_make_app_user()]), \
             patch.object(connector, "_sync_users_full", new_callable=AsyncMock) as m_full:
            await connector._sync_users()
            m_full.assert_called_once()

    async def test_incremental_sync_when_timestamp_exists(self, connector):
        connector.user_sync_point = AsyncMock()
        connector.user_sync_point.read_sync_point = AsyncMock(return_value={"timestamp": "2025-01-01T00:00:00Z"})
        connector.user_sync_point.update_sync_point = AsyncMock()
        with patch.object(connector, "get_all_users", new_callable=AsyncMock, return_value=[_make_app_user()]), \
             patch.object(connector, "_sync_users_incremental", new_callable=AsyncMock) as m_inc:
            await connector._sync_users()
            m_inc.assert_called_once()

    async def test_sync_users_full(self, connector):
        users = [_make_app_user()]
        await connector._sync_users_full(users)
        connector.data_entities_processor.on_new_app_users.assert_called_once_with(users)
