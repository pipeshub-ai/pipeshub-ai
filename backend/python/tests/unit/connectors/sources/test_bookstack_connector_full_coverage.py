"""Full coverage tests for BookStack connector."""

import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.connectors.sources.bookstack.connector import BookStackConnector, ReadOutcome
from app.models.entities import (
    AppRole,
    AppUser,
)


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.bookstack.full")


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
    proc.reindex_existing_records = AsyncMock()
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
    async def test_calls_processor(self, connector, mock_data_entities_processor):
        users = [MagicMock()]
        await connector._sync_users_full(users)
        mock_data_entities_processor.on_new_app_users.assert_awaited_once_with(users)




class TestHandleUserCreateEvent:
    @pytest.mark.asyncio
    async def test_parses_and_submits(self, connector, mock_data_entities_processor):
        events = [{"detail": "(5) Harshit"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="h@test.com", full_name="Harshit", is_active=True)
        ]
        await connector._handle_user_create_event(events, users)
        mock_data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_matching_users(self, connector, mock_data_entities_processor):
        events = [{"detail": "(99) Ghost"}]
        users = [
            AppUser(app_name=Connectors.BOOKSTACK, connector_id="bs-1", source_user_id="5",
                    email="h@test.com", full_name="Harshit", is_active=True)
        ]
        await connector._handle_user_create_event(events, users)
        mock_data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_events(self, connector, mock_data_entities_processor):
        await connector._handle_user_create_event([], [])
        mock_data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bad_detail(self, connector, mock_data_entities_processor):
        events = [{"detail": "bad format"}]
        await connector._handle_user_create_event(events, [])
        mock_data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_detail(self, connector, mock_data_entities_processor):
        events = [{}]
        await connector._handle_user_create_event(events, [])
        mock_data_entities_processor.on_new_app_users.assert_not_awaited()




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
    async def test_no_roles(self, connector, mock_data_entities_processor):
        connector._fetch_all_roles_with_details = AsyncMock(return_value=[])
        await connector._sync_user_roles_full()
        mock_data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_roles_and_users(self, connector, mock_data_entities_processor):
        connector._fetch_all_roles_with_details = AsyncMock(return_value=[
            {"id": 1, "display_name": "Admin", "users": [{"id": 10}]}
        ])
        connector._fetch_all_users_with_details = AsyncMock(return_value=[
            {"id": 10, "email": "u@test.com", "name": "User"}
        ])
        await connector._sync_user_roles_full()
        mock_data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_not_in_details_skipped(self, connector, mock_data_entities_processor):
        connector._fetch_all_roles_with_details = AsyncMock(return_value=[
            {"id": 1, "display_name": "Admin", "users": [{"id": 10}]}
        ])
        connector._fetch_all_users_with_details = AsyncMock(return_value=[])
        await connector._sync_user_roles_full()
        mock_data_entities_processor.on_new_app_roles.assert_awaited_once()


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
    async def test_success(self, connector, mock_data_entities_processor):
        await connector._handle_role_delete_event(1)
        mock_data_entities_processor.on_app_role_deleted.assert_awaited_once()




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






class TestBookStackCoverageGaps:
    """Target remaining uncovered lines in bookstack/connector.py."""

    @pytest.mark.asyncio
    async def test_user_create_detail_without_name_triggers_parse_warning(self, connector):
        await connector._handle_user_create_event([{"detail": "(5)"}], [])

    @pytest.mark.asyncio
    async def test_user_upsert_unparseable_event(self, connector):
        connector._handle_user_create_event = AsyncMock()
        await connector._handle_user_upsert_event([{}], [])
        connector._handle_user_create_event.assert_awaited_once()


    @pytest.mark.asyncio
    async def test_fetch_all_roles_empty_data_page(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_roles = AsyncMock(
            return_value=_make_response(data={"data": [], "total": 0})
        )
        assert await connector._fetch_all_roles() == []

    @pytest.mark.asyncio
    async def test_fetch_all_users_list_failure(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(
            return_value=_make_response(success=False, error="list failed")
        )
        assert await connector._fetch_all_users_with_details() == []

    @pytest.mark.asyncio
    async def test_fetch_all_users_pagination(self, connector):
        connector.batch_size = 1
        connector.data_source = AsyncMock()
        connector.data_source.list_users = AsyncMock(side_effect=[
            _make_response(data={"data": [{"id": 1}], "total": 2}),
            _make_response(data={"data": [{"id": 2}], "total": 2}),
        ])
        connector.data_source.get_user = AsyncMock(
            return_value=_make_response(data={"id": 1, "name": "U", "email": "u@test.com"})
        )
        result = await connector._fetch_all_users_with_details()
        assert len(result) == 2















    def test_get_date_filters_modified_and_created(self, connector):
        modified_filter = MagicMock()
        modified_filter.is_empty.return_value = False
        modified_filter.get_datetime_iso.return_value = (
            "2025-01-01T00:00:00+00:00",
            "2025-06-01T00:00:00+00:00",
        )
        created_filter = MagicMock()
        created_filter.is_empty.return_value = False
        created_filter.get_datetime_iso.return_value = (
            "2025-02-01T00:00:00+00:00",
            "2025-12-31T00:00:00+00:00",
        )
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: {
            SyncFilterKey.MODIFIED: modified_filter,
            SyncFilterKey.CREATED: created_filter,
        }.get(key)
        modified_after, modified_before, created_after, created_before = connector._get_date_filters()
        assert modified_after is not None
        assert modified_before is not None
        assert created_after is not None
        assert created_before is not None

    @pytest.mark.asyncio
    async def test_run_incremental_sync_error_propagates(self, connector):
        connector.run_sync = AsyncMock(side_effect=RuntimeError("sync failed"))
        with pytest.raises(RuntimeError, match="sync failed"):
            await connector.run_incremental_sync()


    def test_get_book_id_filter_not_in_logs(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_value.return_value = ["7"]
        mock_filter.get_operator.return_value = MagicMock(value="not_in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.BOOK_IDS else None
        )
        book_ids, operator = connector._get_book_id_filter()
        assert book_ids == {7}
        assert operator.value == "not_in"

    @pytest.mark.asyncio
    async def test_create_connector_factory(
        self, mock_logger, mock_data_store_provider, mock_config_service
    ):
        with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
            processor = MagicMock()
            processor.org_id = "org-1"
            result = await BookStackConnector.create_connector(
                mock_logger,
                mock_data_store_provider,
                mock_config_service,
                "bs-factory-1",
                "personal",
                "creator-1",
                data_entities_processor=processor,
            )
        assert isinstance(result, BookStackConnector)
        assert result.connector_id == "bs-factory-1"
