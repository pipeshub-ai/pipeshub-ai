"""Tests for Zendesk connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.core.registry.filters import SyncFilterKey
from app.connectors.sources.zendesk.connector import (
    DEFAULT_INCREMENTAL_START_TIME,
    PAGE_SIZE,
    SYNC_POINT_KEY,
    ZendeskConnector,
)
from app.models.entities import (
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.zendesk")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-zd-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
    return proc


@pytest.fixture()
def mock_tx_store():
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    return tx


@pytest.fixture()
def mock_data_store_provider(mock_tx_store):
    provider = MagicMock()
    provider.transaction.return_value = mock_tx_store
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "authType": "API_TOKEN",
            "subdomain": "acme",
            "apiToken": "test-token",
            "email": "agent@acme.com",
        },
    })
    return svc


@pytest.fixture()
def zendesk_connector(mock_logger, mock_data_entities_processor,
                      mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.zendesk.connector.ZendeskApp"):
        connector = ZendeskConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="zd-conn-1",
            scope="team",
            created_by="test-user",
        )
    return connector


def _make_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


# ===========================================================================
# Init
# ===========================================================================


class TestZendeskConnectorInit:
    def test_constructor(self, zendesk_connector):
        assert zendesk_connector.connector_id == "zd-conn-1"
        assert zendesk_connector.connector_name == Connectors.ZENDESK
        assert zendesk_connector.data_source is None
        assert zendesk_connector.external_client is None

    @patch("app.connectors.sources.zendesk.connector.ZendeskDataSource")
    @patch("app.connectors.sources.zendesk.connector.ZendeskClient.build_from_services",
           new_callable=AsyncMock)
    async def test_init_success(self, mock_build, mock_ds_cls, zendesk_connector):
        mock_client = MagicMock()
        mock_client.get_base_url.return_value = "https://acme.zendesk.com/api/v2"
        mock_build.return_value = mock_client

        assert await zendesk_connector.init() is True
        assert zendesk_connector.external_client is mock_client
        assert zendesk_connector.base_url == "https://acme.zendesk.com/api/v2"

    @patch("app.connectors.sources.zendesk.connector.ZendeskClient.build_from_services",
           new_callable=AsyncMock)
    async def test_init_returns_false_on_failure(self, mock_build, zendesk_connector):
        mock_build.side_effect = Exception("bad credentials")
        assert await zendesk_connector.init() is False


# ===========================================================================
# Pagination helper
# ===========================================================================


class TestFetchPaginatedList:
    async def test_stops_on_short_page(self, zendesk_connector):
        api = AsyncMock(return_value=_make_response(data={"groups": [{"id": 1}]}))
        result = await zendesk_connector._fetch_paginated_list(api, "groups")
        assert len(result) == 1
        api.assert_awaited_once_with(page=1, per_page=PAGE_SIZE)

    async def test_follows_multiple_pages(self, zendesk_connector):
        full = {"groups": [{"id": i} for i in range(PAGE_SIZE)]}
        api = AsyncMock(side_effect=[
            _make_response(data=full),
            _make_response(data={"groups": [{"id": 999}]}),
        ])
        result = await zendesk_connector._fetch_paginated_list(api, "groups")
        assert len(result) == PAGE_SIZE + 1
        assert api.await_count == 2

    async def test_stops_on_failed_response(self, zendesk_connector):
        api = AsyncMock(return_value=_make_response(success=False))
        assert await zendesk_connector._fetch_paginated_list(api, "groups") == []


# ===========================================================================
# Ticket transformation
# ===========================================================================


class TestTicketToRecord:
    async def test_builds_ticket_record(self, zendesk_connector):
        zendesk_connector._user_id_to_data = {
            "10": {"email": "req@acme.com", "name": "Req User"},
            "20": {"email": "agent@acme.com", "name": "Agent User"},
        }
        result = await zendesk_connector._ticket_to_record({
            "id": 555,
            "subject": "Printer on fire",
            "group_id": 7,
            "requester_id": 10,
            "assignee_id": 20,
            "status": "open",
            "priority": "urgent",
            "tags": ["hardware"],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        })
        assert result is not None
        record, permissions = result
        assert isinstance(record, TicketRecord)
        assert record.record_type == RecordType.TICKET
        assert record.external_record_id == "555"
        assert record.record_name == "Printer on fire"
        assert record.external_record_group_id == "group_7"
        assert record.record_group_type == RecordGroupType.PROJECT
        assert record.mime_type == MimeTypes.BLOCKS.value
        assert record.version == 0
        assert record.labels == ["hardware"]
        assert record.reporter_email == "req@acme.com"
        assert record.assignee_email == "agent@acme.com"
        assert permissions

    async def test_returns_none_without_id(self, zendesk_connector):
        assert await zendesk_connector._ticket_to_record({"subject": "no id"}) is None

    async def test_skips_unchanged_ticket(self, zendesk_connector, mock_tx_store):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.version = 3
        existing.source_updated_at = 1767312000000  # 2026-01-02T00:00:00Z in ms
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        result = await zendesk_connector._ticket_to_record({
            "id": 555,
            "subject": "Unchanged",
            "updated_at": "2026-01-02T00:00:00Z",
        })
        assert result is None

    async def test_bumps_version_on_changed_ticket(self, zendesk_connector, mock_tx_store):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.version = 3
        existing.source_updated_at = 1  # differs from the payload
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        result = await zendesk_connector._ticket_to_record({
            "id": 555,
            "subject": "Changed",
            "updated_at": "2026-01-02T00:00:00Z",
        })
        assert result is not None
        record, _ = result
        assert record.id == "rec-1"
        assert record.version == 4

    async def test_group_filter_excludes_ticket(self, zendesk_connector):
        group_filter = MagicMock()
        group_filter.get_value.return_value = ["7"]
        group_filter.get_operator.return_value = "in"
        zendesk_connector.sync_filters = {SyncFilterKey.GROUP_IDS: group_filter}
        result = await zendesk_connector._ticket_to_record({
            "id": 1, "subject": "other group", "group_id": 99,
        })
        assert result is None


# ===========================================================================
# Article transformation
# ===========================================================================


class TestArticleToRecord:
    async def test_builds_webpage_record(self, zendesk_connector):
        result = await zendesk_connector._article_to_record({
            "id": 42,
            "title": "How to reset",
            "html_url": "https://acme.zendesk.com/hc/en-us/articles/42",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        })
        assert result is not None
        record, permissions = result
        assert isinstance(record, WebpageRecord)
        assert record.record_type == RecordType.WEBPAGE
        assert record.external_record_id == "article_42"
        assert record.weburl == "https://acme.zendesk.com/hc/en-us/articles/42"
        assert permissions[0].entity_type == EntityType.ORG

    async def test_returns_none_without_id(self, zendesk_connector):
        assert await zendesk_connector._article_to_record({"title": "no id"}) is None


# ===========================================================================
# Permissions
# ===========================================================================


class TestRecordPermissions:
    def test_group_and_requester(self, zendesk_connector):
        perms = zendesk_connector._record_permissions(7, {"email": "req@acme.com"})
        assert {p.entity_type for p in perms} == {EntityType.GROUP, EntityType.USER}
        group_perm = next(p for p in perms if p.entity_type == EntityType.GROUP)
        assert group_perm.external_id == "group_7"
        assert group_perm.type == PermissionType.READ

    def test_falls_back_to_org_when_nothing_known(self, zendesk_connector):
        perms = zendesk_connector._record_permissions(None, {})
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG

    def test_group_only(self, zendesk_connector):
        perms = zendesk_connector._record_permissions(7, {})
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP


# ===========================================================================
# reindex_records — must not widen ACLs
# ===========================================================================


class TestReindexRecords:
    async def test_republishes_without_touching_permissions(
        self, zendesk_connector, mock_data_entities_processor
    ):
        """Regression: routing reindex through on_new_records appended an org-wide
        READ edge to group-scoped tickets."""
        records = [MagicMock(), MagicMock()]
        await zendesk_connector.reindex_records(records)

        mock_data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)
        mock_data_entities_processor.on_new_records.assert_not_awaited()

    async def test_noop_on_empty(self, zendesk_connector, mock_data_entities_processor):
        await zendesk_connector.reindex_records([])
        mock_data_entities_processor.reindex_existing_records.assert_not_awaited()


# ===========================================================================
# Attachment host guard
# ===========================================================================


class TestAttachmentHostGuard:
    @pytest.mark.parametrize("url", [
        "https://acme.zendesk.com/attachments/1",
        "https://p1.zdusercontent.com/attachment/2",
        "https://foo.zendeskusercontent.com/x",
    ])
    def test_accepts_zendesk_hosts(self, zendesk_connector, url):
        assert zendesk_connector._is_safe_zendesk_asset_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://evil.com/steal",
        "https://zendesk.com.evil.com/steal",
        "https://notzendesk.com/x",
    ])
    def test_rejects_foreign_hosts(self, zendesk_connector, url):
        assert zendesk_connector._is_safe_zendesk_asset_url(url) is False

    async def test_download_refuses_untrusted_host(self, zendesk_connector):
        """Regression: credentials must never be sent to an API-supplied foreign host."""
        datasource = MagicMock()
        datasource.http = MagicMock()
        datasource.http.execute = AsyncMock()
        zendesk_connector.data_source = datasource
        zendesk_connector.external_client = MagicMock()

        record = MagicMock()
        record.weburl = "https://evil.com/payload"
        record.external_record_id = "ticket_1_comment_2_attachment_3"

        with pytest.raises(ValueError, match="untrusted host"):
            await zendesk_connector._process_file_for_streaming(record)
        datasource.http.execute.assert_not_awaited()

    async def test_download_raises_without_url(self, zendesk_connector):
        zendesk_connector.data_source = MagicMock()
        zendesk_connector.external_client = MagicMock()
        record = MagicMock()
        record.weburl = None
        with pytest.raises(ValueError):
            await zendesk_connector._process_file_for_streaming(record)


# ===========================================================================
# Incremental cursor handling
# ===========================================================================


class TestIncrementalCursor:
    async def test_fetch_users_stops_on_repeated_cursor(self, zendesk_connector):
        """Regression: a non-advancing cursor used to loop forever."""
        datasource = MagicMock()
        datasource.incremental_users = AsyncMock(return_value=_make_response(data={
            "users": [{"id": 1, "email": "a@acme.com", "name": "A"}],
            "after_cursor": "stuck",
            "end_of_stream": False,
        }))
        users, _ = await zendesk_connector._fetch_users(datasource)
        # First page consumed, second call detects the repeated cursor and stops.
        assert datasource.incremental_users.await_count == 2
        assert len(users) == 2

    async def test_fetch_users_starts_from_epoch(self, zendesk_connector):
        datasource = MagicMock()
        datasource.incremental_users = AsyncMock(return_value=_make_response(data={
            "users": [], "end_of_stream": True,
        }))
        await zendesk_connector._fetch_users(datasource)
        assert datasource.incremental_users.await_args.kwargs["start_time"] == (
            DEFAULT_INCREMENTAL_START_TIME
        )

    async def test_sync_tickets_persists_end_time(self, zendesk_connector):
        datasource = MagicMock()
        datasource.incremental_tickets = AsyncMock(return_value=_make_response(data={
            "tickets": [], "end_time": 1767312000, "end_of_stream": True,
        }))
        zendesk_connector.records_sync_point.update_sync_point = AsyncMock()
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(return_value={})

        await zendesk_connector._sync_tickets(datasource)

        args = zendesk_connector.records_sync_point.update_sync_point.await_args
        assert args.args[0] == SYNC_POINT_KEY
        assert args.args[1]["lastEndTime"] == 1767312000


# ===========================================================================
# Sideload caching
# ===========================================================================


class TestCacheSideloads:
    def test_caches_users_and_groups(self, zendesk_connector):
        zendesk_connector._cache_sideloads({
            "users": [{"id": 5, "email": "u@acme.com"}],
            "groups": [{"id": 9, "name": "Support"}],
        })
        assert zendesk_connector._user_id_to_data["5"]["email"] == "u@acme.com"
        assert zendesk_connector._group_id_to_data["9"]["name"] == "Support"

    def test_ignores_entries_without_id(self, zendesk_connector):
        zendesk_connector._cache_sideloads({"users": [{"email": "x@acme.com"}]})
        assert zendesk_connector._user_id_to_data == {}


# ===========================================================================
# Small helpers
# ===========================================================================


class TestHelpers:
    def test_parse_iso_datetime_to_ms(self, zendesk_connector):
        assert zendesk_connector._parse_datetime("2026-01-02T00:00:00Z") == 1767312000000

    def test_parse_datetime_none(self, zendesk_connector):
        assert zendesk_connector._parse_datetime(None) is None

    def test_parse_datetime_invalid(self, zendesk_connector):
        assert zendesk_connector._parse_datetime("not-a-date") is None

    def test_parse_epoch_seconds_promoted_to_ms(self, zendesk_connector):
        assert zendesk_connector._parse_datetime(1767312000) == 1767312000000

    def test_extension(self, zendesk_connector):
        assert zendesk_connector._extension("report.PDF") == "pdf"

    def test_extension_none_when_absent(self, zendesk_connector):
        assert zendesk_connector._extension("README") is None

    def test_extract_list_from_dict(self, zendesk_connector):
        assert zendesk_connector._extract_list({"groups": [{"id": 1}]}, "groups") == [{"id": 1}]

    def test_extract_list_from_bare_list(self, zendesk_connector):
        assert zendesk_connector._extract_list([{"id": 1}], "groups") == [{"id": 1}]

    def test_extract_list_missing_key(self, zendesk_connector):
        assert zendesk_connector._extract_list({"other": []}, "groups") == []

    def test_indexing_enabled_defaults_true_without_filters(self, zendesk_connector):
        zendesk_connector.indexing_filters = None
        assert zendesk_connector._is_indexing_enabled("tickets") is True

    def test_group_filter_allows_all_when_unset(self, zendesk_connector):
        zendesk_connector.sync_filters = None
        assert zendesk_connector._is_group_allowed_by_filter("7") is True
