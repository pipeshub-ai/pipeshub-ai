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
               book_slug="test-book", revision_count=1,
               created_at="2024-12-01T00:00:00Z", updated_at="2025-01-01T00:00:00Z"):
    return {
        "id": page_id,
        "name": name,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "slug": slug,
        "book_slug": book_slug,
        "revision_count": revision_count,
        "created_at": created_at,
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
        dep.get_record_by_external_id = AsyncMock(return_value=None)
        dep.get_user_by_user_id = AsyncMock(return_value=MagicMock(email="test@example.com"))
        dep.get_user_by_email = AsyncMock(return_value=MagicMock(id="user-db-1"))
        dep.delete_edges_between_collections = AsyncMock()

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



# ---------------------------------------------------------------------------
# _process_bookstack_page
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# _handle_record_updates
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# _sync_users
# ---------------------------------------------------------------------------

class TestBookstackSyncUsers:


    async def test_sync_users_full(self, connector):
        users = [_make_app_user()]
        await connector._sync_users_full(users)
        connector.data_entities_processor.on_new_app_users.assert_called_once_with(users)
