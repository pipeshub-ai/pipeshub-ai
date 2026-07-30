"""Tests for Zendesk connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.core.registry.filters import IndexingFilterKey, SyncFilterKey
from app.connectors.sources.zendesk.connector import (
    DEFAULT_INCREMENTAL_START_TIME,
    PAGE_SIZE,
    SYNC_POINT_KEY,
    ZendeskConnector,
)
from app.models.blocks import BlockGroup, GroupType
from app.models.entities import (
    AppUser,
    OriginTypes,
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


def _app_user(source_user_id="1", email="a@acme.com", name="A"):
    return AppUser(
        app_name=Connectors.ZENDESK,
        connector_id="zd-conn-1",
        source_user_id=source_user_id,
        email=email,
        full_name=name,
        org_id="org-zd-1",
    )


def _ready(connector):
    """Mark the connector initialised so `_get_fresh_datasource` short-circuits."""
    connector.external_client = MagicMock()
    connector.data_source = MagicMock()
    return connector.data_source


# ===========================================================================
# run_sync orchestration
# ===========================================================================


class TestRunSync:
    @staticmethod
    def _stub_stages(connector, user):
        connector._fetch_users = AsyncMock(return_value=([user], {"1": user}))
        connector._ensure_creator_access = AsyncMock(return_value=None)
        connector._fetch_groups = AsyncMock(return_value=([("g_rg", [])], [("g_ug", [])]))
        connector._fetch_organizations = AsyncMock(
            return_value=([("o_rg", [])], [("o_ug", [])])
        )
        connector._sync_tickets = AsyncMock(return_value=3)
        connector._sync_help_center_articles = AsyncMock(return_value=4)

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_emits_user_groups_before_record_groups(
        self, _filters, zendesk_connector, mock_data_entities_processor
    ):
        """Order is load-bearing: permission edges need the user group to exist first."""
        _ready(zendesk_connector)
        self._stub_stages(zendesk_connector, _app_user())

        order: list[str] = []
        mock_data_entities_processor.on_new_app_users.side_effect = (
            lambda *a: order.append("users")
        )
        mock_data_entities_processor.on_new_user_groups.side_effect = (
            lambda *a: order.append("user_groups")
        )
        mock_data_entities_processor.on_new_record_groups.side_effect = (
            lambda *a: order.append("record_groups")
        )

        await zendesk_connector.run_sync()

        assert order[0] == "users"
        assert order.index("user_groups") < order.index("record_groups")

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_creator_access_runs_after_users_before_records(
        self, _filters, zendesk_connector, mock_data_entities_processor
    ):
        _ready(zendesk_connector)
        self._stub_stages(zendesk_connector, _app_user())

        order: list[str] = []
        mock_data_entities_processor.on_new_app_users.side_effect = (
            lambda *a: order.append("users")
        )
        zendesk_connector._ensure_creator_access.side_effect = (
            lambda: order.append("creator")
        )
        zendesk_connector._sync_tickets.side_effect = lambda _ds: order.append("tickets") or 3

        await zendesk_connector.run_sync()

        assert order == ["users", "creator", "tickets"]

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_skips_empty_collections(
        self, _filters, zendesk_connector, mock_data_entities_processor
    ):
        _ready(zendesk_connector)
        zendesk_connector._fetch_users = AsyncMock(return_value=([], {}))
        zendesk_connector._ensure_creator_access = AsyncMock(return_value=None)
        zendesk_connector._fetch_groups = AsyncMock(return_value=([], []))
        zendesk_connector._fetch_organizations = AsyncMock(return_value=([], []))
        zendesk_connector._sync_tickets = AsyncMock(return_value=0)
        zendesk_connector._sync_help_center_articles = AsyncMock(return_value=0)

        await zendesk_connector.run_sync()

        mock_data_entities_processor.on_new_app_users.assert_not_awaited()
        mock_data_entities_processor.on_new_user_groups.assert_not_awaited()
        mock_data_entities_processor.on_new_record_groups.assert_not_awaited()

    async def test_incremental_sync_delegates_to_full_sync(self, zendesk_connector):
        zendesk_connector.run_sync = AsyncMock()
        await zendesk_connector.run_incremental_sync()
        zendesk_connector.run_sync.assert_awaited_once()


# ===========================================================================
# Creator access
# ===========================================================================


class TestEnsureCreatorAccess:
    async def test_returns_cached_permission_without_relookup(self, zendesk_connector):
        sentinel = MagicMock()
        zendesk_connector._connector_group_permission = sentinel
        zendesk_connector.ensure_connector_group_permission = AsyncMock()

        assert await zendesk_connector._ensure_creator_access() is sentinel
        zendesk_connector.ensure_connector_group_permission.assert_not_awaited()

    async def test_resolves_creator_email_from_store(
        self, zendesk_connector, mock_tx_store
    ):
        zendesk_connector._connector_group_permission = None
        zendesk_connector.creator_email = None
        zendesk_connector.created_by = "user-42"
        mock_tx_store.get_user_by_user_id = AsyncMock(
            return_value={"email": "boss@acme.com"}
        )
        zendesk_connector.ensure_connector_group_permission = AsyncMock(
            return_value=MagicMock()
        )

        await zendesk_connector._ensure_creator_access()

        assert zendesk_connector.creator_email == "boss@acme.com"

    async def test_survives_store_failure(self, zendesk_connector, mock_tx_store):
        zendesk_connector._connector_group_permission = None
        zendesk_connector.creator_email = None
        zendesk_connector.created_by = "user-42"
        mock_tx_store.get_user_by_user_id = AsyncMock(side_effect=Exception("boom"))
        zendesk_connector.ensure_connector_group_permission = AsyncMock(
            return_value=None
        )

        assert await zendesk_connector._ensure_creator_access() is None

    async def test_warns_when_no_connector_group(self, zendesk_connector):
        zendesk_connector._connector_group_permission = None
        zendesk_connector.creator_email = "boss@acme.com"
        zendesk_connector.ensure_connector_group_permission = AsyncMock(
            return_value=None
        )
        assert await zendesk_connector._ensure_creator_access() is None


# ===========================================================================
# Groups
# ===========================================================================


class TestFetchGroups:
    async def test_builds_record_group_and_user_group_per_group(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7, "name": "Technical Support",
                        "created_at": "2026-01-01T00:00:00Z"}],
        }))
        datasource.list_group_memberships = AsyncMock(return_value=_make_response(data={
            "group_memberships": [{"group_id": 7, "user_id": 1}],
        }))
        user = _app_user()

        record_groups, user_groups = await zendesk_connector._fetch_groups(
            datasource, {"1": user}
        )

        assert len(record_groups) == 1
        record_group, permissions = record_groups[0]
        assert record_group.external_group_id == "group_7"
        assert record_group.group_type == RecordGroupType.PROJECT
        assert permissions[0].entity_type == EntityType.GROUP

        assert len(user_groups) == 1
        user_group, members = user_groups[0]
        assert user_group.source_user_group_id == "group_7"
        assert members == [user]

    async def test_caches_group_data_for_later_lookups(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7, "name": "Billing"}],
        }))
        datasource.list_group_memberships = AsyncMock(
            return_value=_make_response(data={"group_memberships": []})
        )

        await zendesk_connector._fetch_groups(datasource, {})

        assert zendesk_connector._group_id_to_data["7"]["name"] == "Billing"

    async def test_membership_for_unknown_user_is_dropped(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7, "name": "Sales"}],
        }))
        datasource.list_group_memberships = AsyncMock(return_value=_make_response(data={
            "group_memberships": [{"group_id": 7, "user_id": 999}],
        }))

        _, user_groups = await zendesk_connector._fetch_groups(datasource, {})

        assert user_groups[0][1] == []

    async def test_falls_back_to_synthetic_name(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7}],
        }))
        datasource.list_group_memberships = AsyncMock(
            return_value=_make_response(data={"group_memberships": []})
        )

        record_groups, _ = await zendesk_connector._fetch_groups(datasource, {})

        assert record_groups[0][0].name == "Group 7"


# ===========================================================================
# Organizations
# ===========================================================================


class TestFetchOrganizations:
    async def test_builds_user_group_record_group_pair(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"id": 21, "name": "Acme Corp",
                                   "details": "Enterprise account"}],
                "end_of_stream": True,
            })
        )

        record_groups, user_groups = await zendesk_connector._fetch_organizations(datasource)

        record_group, _ = record_groups[0]
        assert record_group.external_group_id == "org_21"
        assert record_group.group_type == RecordGroupType.USER_GROUP
        assert record_group.description == "Enterprise account"
        assert user_groups[0][0].source_user_group_id == "org_21"

    async def test_membership_derived_from_cached_users_not_extra_calls(
        self, zendesk_connector
    ):
        """Zendesk has no bulk org-membership endpoint; a per-org call would be N+1."""
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"id": 21, "name": "Acme Corp"}],
                "end_of_stream": True,
            })
        )
        user = _app_user()
        zendesk_connector._user_id_to_data = {"1": {"organization_id": 21}}
        zendesk_connector._user_id_to_app_user = {"1": user}

        _, user_groups = await zendesk_connector._fetch_organizations(datasource)

        assert user_groups[0][1] == [user]
        assert datasource.incremental_organizations.await_count == 1

    async def test_attaches_creator_permission_when_present(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"id": 21, "name": "Acme Corp"}],
                "end_of_stream": True,
            })
        )
        creator = MagicMock()
        zendesk_connector._connector_group_permission = creator

        record_groups, _ = await zendesk_connector._fetch_organizations(datasource)

        assert creator in record_groups[0][1]

    async def test_stops_on_repeated_cursor(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"id": 21, "name": "Acme"}],
                "after_cursor": "stuck",
                "end_of_stream": False,
            })
        )

        await zendesk_connector._fetch_organizations(datasource)

        assert datasource.incremental_organizations.await_count == 2

    async def test_logs_and_stops_on_failure(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(success=False, error="401 Unauthorized")
        )

        record_groups, user_groups = await zendesk_connector._fetch_organizations(datasource)

        assert (record_groups, user_groups) == ([], [])

    async def test_skips_org_without_id(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"name": "No ID"}],
                "end_of_stream": True,
            })
        )

        record_groups, _ = await zendesk_connector._fetch_organizations(datasource)

        assert record_groups == []


# ===========================================================================
# Help Center
# ===========================================================================


class TestHelpCenterSync:
    async def test_sections_become_kb_record_groups(
        self, zendesk_connector, mock_data_entities_processor
    ):
        datasource = _ready(zendesk_connector)
        datasource.list_sections = AsyncMock(return_value=_make_response(data={
            "sections": [{"id": 31, "name": "Billing & Subscriptions",
                          "description": "Money things",
                          "html_url": "https://acme.zendesk.com/hc/s/31"}],
        }))

        await zendesk_connector._sync_help_center_sections(datasource)

        record_groups = mock_data_entities_processor.on_new_record_groups.await_args.args[0]
        record_group, permissions = record_groups[0]
        assert record_group.external_group_id == "section_31"
        assert record_group.group_type == RecordGroupType.KB
        assert permissions[0].entity_type == EntityType.ORG

    async def test_section_without_id_skipped(
        self, zendesk_connector, mock_data_entities_processor
    ):
        datasource = _ready(zendesk_connector)
        datasource.list_sections = AsyncMock(
            return_value=_make_response(data={"sections": [{"name": "Orphan"}]})
        )

        await zendesk_connector._sync_help_center_sections(datasource)

        mock_data_entities_processor.on_new_record_groups.assert_not_awaited()

    async def test_articles_synced_after_sections(
        self, zendesk_connector, mock_data_entities_processor
    ):
        """A record with no record group is unreachable from the App in the graph."""
        datasource = _ready(zendesk_connector)
        datasource.list_sections = AsyncMock(
            return_value=_make_response(data={"sections": [{"id": 31, "name": "S"}]})
        )
        datasource.list_articles = AsyncMock(return_value=_make_response(data={
            "articles": [{"id": 55, "title": "How to reset", "section_id": 31,
                          "html_url": "https://acme.zendesk.com/hc/a/55"}],
        }))

        order: list[str] = []
        mock_data_entities_processor.on_new_record_groups.side_effect = (
            lambda *a: order.append("sections")
        )
        mock_data_entities_processor.on_new_records.side_effect = (
            lambda *a: order.append("articles")
        )

        count = await zendesk_connector._sync_help_center_articles(datasource)

        assert count == 1
        assert order == ["sections", "articles"]

    async def test_returns_zero_when_knowledge_base_disabled(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        disabled = MagicMock()
        disabled.get_value.return_value = False
        zendesk_connector.indexing_filters = {
            IndexingFilterKey.KNOWLEDGE_BASE.value: disabled
        }

        assert await zendesk_connector._sync_help_center_articles(datasource) == 0
        datasource.list_sections.assert_not_called()


# ===========================================================================
# Streaming
# ===========================================================================


class TestStreamRecord:
    async def test_ticket_description_is_first_block_group(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_comments = AsyncMock(return_value=_make_response(data={
            "comments": [
                {"id": 1, "author_id": 9, "html_body": "<p>First</p>"},
                {"id": 2, "author_id": 9, "body": "Second"},
            ],
        }))
        zendesk_connector._user_id_to_data = {"9": {"name": "Sarah"}}
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "23"
        record.record_name = "Latency"
        record.weburl = "https://acme.zendesk.com/agent/tickets/23"

        payload = await zendesk_connector._process_ticket_blockgroups_for_streaming(record)
        body = payload.decode()

        assert '"name": "Description"' in body
        assert "Comment by Sarah" in body

    async def test_ticket_without_comments_gets_placeholder_block(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_comments = AsyncMock(
            return_value=_make_response(data={"comments": []})
        )
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "23"
        record.record_name = "Empty ticket"
        record.weburl = None

        body = (
            await zendesk_connector._process_ticket_blockgroups_for_streaming(record)
        ).decode()

        assert "Empty ticket" in body

    async def test_unsupported_record_type_raises(self, zendesk_connector):
        _ready(zendesk_connector)
        record = MagicMock()
        record.record_type = RecordType.MAIL

        with pytest.raises(ValueError, match="Unsupported Zendesk record type"):
            await zendesk_connector.stream_record(record)

    async def test_file_download_sends_auth_only_to_zendesk_host(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.http = MagicMock()
        datasource.http.headers = {"Authorization": "Basic secret"}
        response = MagicMock()
        response.status = 200
        response.bytes.return_value = b"filebytes"
        datasource.http.execute = AsyncMock(return_value=response)

        record = MagicMock()
        record.weburl = "https://acme.zendesk.com/attachments/9"
        record.external_record_id = "att-9"

        assert await zendesk_connector._process_file_for_streaming(record) == b"filebytes"
        sent = datasource.http.execute.await_args.args[0]
        assert sent.headers["Authorization"] == "Basic secret"

    async def test_file_download_raises_on_error_status(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.http = MagicMock()
        datasource.http.headers = {}
        response = MagicMock()
        response.status = 404
        datasource.http.execute = AsyncMock(return_value=response)

        record = MagicMock()
        record.weburl = "https://acme.zendesk.com/attachments/9"
        record.external_record_id = "att-9"

        with pytest.raises(Exception, match="Failed to download"):
            await zendesk_connector._process_file_for_streaming(record)


# ===========================================================================
# Attachment child records
# ===========================================================================


class TestAttachmentChildRecords:
    @staticmethod
    def _parent():
        return TicketRecord(
            id="rec-1",
            org_id="org-zd-1",
            record_name="Latency",
            record_type=RecordType.TICKET,
            external_record_id="23",
            external_record_group_id="group_7",
            record_group_type=RecordGroupType.PROJECT,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.ZENDESK,
            connector_id="zd-conn-1",
            mime_type=MimeTypes.BLOCKS.value,
        )

    async def test_builds_child_record_and_publishes_file(
        self, zendesk_connector, mock_data_entities_processor
    ):
        comment = {"id": 5, "attachments": [{
            "id": 88, "file_name": "trace.LOG", "content_type": "text/plain",
            "size": 120, "content_url": "https://acme.zendesk.com/attachments/88",
        }]}

        children = await zendesk_connector._build_attachment_child_records(
            comment, self._parent()
        )

        assert len(children) == 1
        assert children[0].child_name == "trace.LOG"
        file_record, _ = mock_data_entities_processor.on_new_records.await_args.args[0][0]
        assert file_record.extension == "log"
        assert file_record.external_record_group_id == "group_7"
        assert file_record.parent_external_record_id == "23"

    async def test_skips_attachment_without_content_url(self, zendesk_connector):
        comment = {"id": 5, "attachments": [{"id": 88, "file_name": "x.txt"}]}
        assert await zendesk_connector._build_attachment_child_records(
            comment, self._parent()
        ) == []

    async def test_existing_attachment_is_not_republished(
        self, zendesk_connector, mock_tx_store, mock_data_entities_processor
    ):
        existing = MagicMock()
        existing.id = "rec-att"
        existing.version = 4
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        comment = {"id": 5, "attachments": [{
            "id": 88, "file_name": "a.txt",
            "content_url": "https://acme.zendesk.com/attachments/88",
        }]}

        children = await zendesk_connector._build_attachment_child_records(
            comment, self._parent()
        )

        assert children[0].child_id == "rec-att"
        mock_data_entities_processor.on_new_records.assert_not_awaited()

    async def test_returns_empty_when_attachments_disabled(self, zendesk_connector):
        disabled = MagicMock()
        disabled.get_value.return_value = False
        zendesk_connector.indexing_filters = {
            IndexingFilterKey.ISSUE_ATTACHMENTS.value: disabled
        }
        comment = {"id": 5, "attachments": [{
            "id": 88, "content_url": "https://acme.zendesk.com/attachments/88",
        }]}

        assert await zendesk_connector._build_attachment_child_records(
            comment, self._parent()
        ) == []


# ===========================================================================
# Filter options
# ===========================================================================


class TestGetFilterOptions:
    async def test_lists_groups(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7, "name": "Technical Support"},
                       {"id": 8, "name": "Billing"}],
        }))

        result = await zendesk_connector.get_filter_options(
            SyncFilterKey.GROUP_IDS.value
        )

        assert result.success is True
        assert [o.label for o in result.options] == ["Technical Support", "Billing"]

    async def test_search_narrows_options(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7, "name": "Technical Support"},
                       {"id": 8, "name": "Billing"}],
        }))

        result = await zendesk_connector.get_filter_options(
            SyncFilterKey.GROUP_IDS.value, search="bill"
        )

        assert [o.id for o in result.options] == ["8"]

    async def test_pagination_reports_has_more(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": i, "name": f"G{i}"} for i in range(1, 4)],
        }))

        result = await zendesk_connector.get_filter_options(
            SyncFilterKey.GROUP_IDS.value, page=1, limit=2
        )

        assert len(result.options) == 2
        assert result.has_more is True

    async def test_group_without_name_skipped(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(
            return_value=_make_response(data={"groups": [{"id": 7}]})
        )

        result = await zendesk_connector.get_filter_options(
            SyncFilterKey.GROUP_IDS.value
        )

        assert result.options == []

    async def test_unknown_filter_key_returns_no_options(self, zendesk_connector):
        _ready(zendesk_connector)
        result = await zendesk_connector.get_filter_options("not_a_filter")
        assert result.options == []


# ===========================================================================
# Connection test, lifecycle, stubs
# ===========================================================================


class TestConnectionAndLifecycle:
    async def test_connection_ok(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response())
        assert await zendesk_connector.test_connection_and_access() is True

    async def test_connection_false_on_unsuccessful_response(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(success=False))
        assert await zendesk_connector.test_connection_and_access() is False

    async def test_connection_false_on_exception(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(side_effect=Exception("network down"))
        assert await zendesk_connector.test_connection_and_access() is False

    async def test_cleanup_closes_client(self, zendesk_connector):
        inner = MagicMock()
        inner.close = AsyncMock()
        client = MagicMock()
        client.get_client.return_value = inner
        zendesk_connector.external_client = client

        await zendesk_connector.cleanup()

        inner.close.assert_awaited_once()

    async def test_cleanup_noop_without_client(self, zendesk_connector):
        zendesk_connector.external_client = None
        await zendesk_connector.cleanup()

    async def test_cleanup_tolerates_client_without_close(self, zendesk_connector):
        client = MagicMock()
        client.get_client.return_value = object()
        zendesk_connector.external_client = client
        await zendesk_connector.cleanup()

    async def test_signed_url_is_empty(self, zendesk_connector):
        assert await zendesk_connector.get_signed_url(MagicMock()) == ""

    async def test_webhook_notification_is_noop(self, zendesk_connector):
        assert await zendesk_connector.handle_webhook_notification({"a": 1}) is None

    async def test_fresh_datasource_raises_when_uninitialised(self, zendesk_connector):
        zendesk_connector.init = AsyncMock(return_value=False)
        zendesk_connector.external_client = None
        zendesk_connector.data_source = None

        with pytest.raises(Exception, match="not initialized"):
            await zendesk_connector._get_fresh_datasource()

    @patch("app.connectors.sources.zendesk.connector.DataSourceEntitiesProcessor")
    async def test_create_connector_initialises_processor(
        self, mock_processor_cls, mock_logger, mock_data_store_provider, mock_config_service
    ):
        processor = MagicMock()
        processor.initialize = AsyncMock()
        mock_processor_cls.return_value = processor

        with patch("app.connectors.sources.zendesk.connector.ZendeskApp"):
            connector = await ZendeskConnector.create_connector(
                mock_logger, mock_data_store_provider, mock_config_service,
                "zd-conn-9", "team", "user-1",
            )

        processor.initialize.assert_awaited_once()
        assert isinstance(connector, ZendeskConnector)
        assert connector.connector_id == "zd-conn-9"


# ===========================================================================
# Date filters
# ===========================================================================


class TestDateFilters:
    @staticmethod
    def _range_filter(value):
        f = MagicMock()
        f.get_value.return_value = value
        return f

    def test_allows_all_without_filters(self, zendesk_connector):
        zendesk_connector.sync_filters = None
        assert zendesk_connector._is_allowed_by_date_filters(1, 2) is True

    def test_created_outside_range_rejected(self, zendesk_connector):
        zendesk_connector.sync_filters = {
            SyncFilterKey.CREATED: self._range_filter((100, 200)),
        }
        assert zendesk_connector._is_allowed_by_date_filters(50, None) is False

    def test_created_inside_range_allowed(self, zendesk_connector):
        zendesk_connector.sync_filters = {
            SyncFilterKey.CREATED: self._range_filter((100, 200)),
        }
        assert zendesk_connector._is_allowed_by_date_filters(150, None) is True

    def test_modified_outside_range_rejected(self, zendesk_connector):
        zendesk_connector.sync_filters = {
            SyncFilterKey.MODIFIED: self._range_filter((100, 200)),
        }
        assert zendesk_connector._is_allowed_by_date_filters(None, 500) is False

    def test_non_tuple_filter_value_ignored(self, zendesk_connector):
        zendesk_connector.sync_filters = {
            SyncFilterKey.CREATED: self._range_filter("not-a-range"),
        }
        assert zendesk_connector._is_allowed_by_date_filters(1, 1) is True

    def test_missing_timestamp_passes_range_check(self, zendesk_connector):
        assert zendesk_connector._timestamp_in_range(None, (100, 200)) is True

    def test_open_ended_range(self, zendesk_connector):
        assert zendesk_connector._timestamp_in_range(500, (None, None)) is True

    def test_above_upper_bound_rejected(self, zendesk_connector):
        assert zendesk_connector._timestamp_in_range(500, (None, 200)) is False


# ===========================================================================
# URL helpers and block-group wiring
# ===========================================================================


class TestUrlHelpersAndBlockGroups:
    def test_ticket_url_uses_subdomain(self, zendesk_connector):
        client = MagicMock()
        client.get_subdomain.return_value = "acme"
        zendesk_connector.external_client = client
        assert zendesk_connector._ticket_web_url(23) == (
            "https://acme.zendesk.com/agent/tickets/23"
        )

    def test_group_url_uses_subdomain(self, zendesk_connector):
        client = MagicMock()
        client.get_subdomain.return_value = "acme"
        zendesk_connector.external_client = client
        assert zendesk_connector._agent_group_url("7") == (
            "https://acme.zendesk.com/admin/people/team/groups/7"
        )

    def test_urls_none_without_client(self, zendesk_connector):
        zendesk_connector.external_client = None
        assert zendesk_connector._ticket_web_url(23) is None
        assert zendesk_connector._agent_group_url("7") is None

    def test_subdomain_none_when_client_raises(self, zendesk_connector):
        client = MagicMock()
        client.get_subdomain.side_effect = Exception("no config")
        zendesk_connector.external_client = client
        assert zendesk_connector._subdomain() is None

    def test_children_wired_from_parent_index(self, zendesk_connector):
        parent = BlockGroup(id="a", index=0, name="Description",
                            type=GroupType.TEXT_SECTION, parent_index=None)
        child = BlockGroup(id="b", index=1, name="Comment",
                           type=GroupType.TEXT_SECTION, parent_index=0)

        zendesk_connector._populate_block_group_children([parent, child])

        assert parent.children is not None
        assert child.children is None

    def test_no_children_when_flat(self, zendesk_connector):
        only = BlockGroup(id="a", index=0, name="Description",
                          type=GroupType.TEXT_SECTION, parent_index=None)
        zendesk_connector._populate_block_group_children([only])
        assert only.children is None


# ===========================================================================
# Article streaming and sync-point start time
# ===========================================================================


class TestArticleStreaming:
    @staticmethod
    def _record():
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "article_55"
        record.record_name = "How to reset"
        record.weburl = "https://acme.zendesk.com/hc/a/55"
        return record

    async def test_converts_html_body_to_markdown(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.show_article = AsyncMock(return_value=_make_response(data={
            "article": {"title": "How to reset", "body": "<h1>Steps</h1><p>Click it</p>"},
        }))

        body = (
            await zendesk_connector._process_article_blockgroups_for_streaming(self._record())
        ).decode()

        assert "Steps" in body
        assert "<h1>" not in body

    async def test_strips_article_prefix_from_external_id(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.show_article = AsyncMock(
            return_value=_make_response(data={"article": {"title": "T", "body": ""}})
        )

        await zendesk_connector._process_article_blockgroups_for_streaming(self._record())

        assert datasource.show_article.await_args.kwargs["article_id"] == 55

    async def test_empty_body_still_produces_block_group(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.show_article = AsyncMock(
            return_value=_make_response(data={"article": {"title": "Stub", "body": None}})
        )

        body = (
            await zendesk_connector._process_article_blockgroups_for_streaming(self._record())
        ).decode()

        assert "Stub" in body

    async def test_raises_when_article_fetch_fails(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.show_article = AsyncMock(return_value=_make_response(success=False))

        with pytest.raises(Exception, match="Failed to fetch Zendesk article"):
            await zendesk_connector._process_article_blockgroups_for_streaming(self._record())

    async def test_stream_record_dispatches_webpage(self, zendesk_connector):
        _ready(zendesk_connector)
        zendesk_connector._process_article_blockgroups_for_streaming = AsyncMock(
            return_value=b"{}"
        )

        response = await zendesk_connector.stream_record(self._record())

        assert response.media_type == MimeTypes.BLOCKS.value

    async def test_stream_record_dispatches_file(self, zendesk_connector):
        _ready(zendesk_connector)
        record = MagicMock()
        record.record_type = RecordType.FILE
        zendesk_connector._process_file_for_streaming = AsyncMock(return_value=b"bytes")

        response = await zendesk_connector.stream_record(record)

        assert response.media_type == MimeTypes.BLOCKS.value


class TestStartTime:
    async def test_uses_epoch_default_without_sync_point(self, zendesk_connector):
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(return_value={})
        zendesk_connector.sync_filters = None

        assert await zendesk_connector._get_start_time() == DEFAULT_INCREMENTAL_START_TIME

    async def test_resumes_from_stored_end_time(self, zendesk_connector):
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(
            return_value={"lastEndTime": 1767312000}
        )
        zendesk_connector.sync_filters = None

        assert await zendesk_connector._get_start_time() == 1767312000

    async def test_modified_filter_can_only_move_start_forward(self, zendesk_connector):
        """A filter start earlier than the checkpoint must not re-pull old tickets."""
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(
            return_value={"lastEndTime": 1767312000}
        )
        modified = MagicMock()
        modified.get_value.return_value = (1000000000000, None)  # ms → 1000000000 s
        zendesk_connector.sync_filters = {SyncFilterKey.MODIFIED: modified}

        assert await zendesk_connector._get_start_time() == 1767312000

    async def test_modified_filter_advances_start_when_later(self, zendesk_connector):
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(
            return_value={"lastEndTime": 100}
        )
        modified = MagicMock()
        modified.get_value.return_value = (1767312000000, None)
        zendesk_connector.sync_filters = {SyncFilterKey.MODIFIED: modified}

        assert await zendesk_connector._get_start_time() == 1767312000


class TestExtractObject:
    def test_returns_nested_object(self, zendesk_connector):
        assert zendesk_connector._extract_object({"article": {"id": 1}}, "article") == {"id": 1}

    def test_falls_back_to_whole_payload(self, zendesk_connector):
        assert zendesk_connector._extract_object({"id": 1}, "article") == {"id": 1}

    def test_non_dict_payload_returns_empty(self, zendesk_connector):
        assert zendesk_connector._extract_object([1, 2], "article") == {}
