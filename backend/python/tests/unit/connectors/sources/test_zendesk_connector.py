"""Tests for Zendesk connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, RecordRelations
from app.connectors.core.registry.filters import IndexingFilterKey, SyncFilterKey
from app.connectors.sources.zendesk.connector import (
    DEFAULT_INCREMENTAL_START_TIME,
    MAX_INLINE_IMAGE_BYTES,
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


def _attachment_lookup(datasource, content_url):
    """Attachment URLs are resolved per download, not read off the record."""
    datasource.show_attachment = AsyncMock(
        return_value=_make_response(data={"attachment": {"content_url": content_url}})
    )


def _make_response(success=True, data=None, error=None, status_code=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    resp.status_code = status_code
    return resp


# ===========================================================================
# Init
# ===========================================================================


class TestZendeskConnectorInit:
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

    async def test_reports_incomplete_on_failed_page(self, zendesk_connector):
        """Callers that rebuild state from the full list must not treat a truncated
        one as authoritative."""
        api = AsyncMock(return_value=_make_response(success=False, error="400"))

        items, complete = await zendesk_connector._fetch_paginated_list_checked(
            api, "group_memberships"
        )

        assert (items, complete) == ([], False)

    async def test_caches_sideloaded_users(self, zendesk_connector):
        """include= sideloads ride in the same payload; dropping them makes comment
        headers render the raw author id."""
        api = AsyncMock(return_value=_make_response(data={
            "comments": [{"id": 1, "author_id": 9}],
            "users": [{"id": 9, "name": "Sarah"}],
        }))

        await zendesk_connector._fetch_paginated_list(api, "comments")

        assert zendesk_connector._user_id_to_data["9"]["name"] == "Sarah"

    async def test_retries_a_rate_limited_page(self, zendesk_connector):
        """Zendesk's incremental export allows 10 req/min, so 429s are routine. The
        data source folds them into a ZendeskResponse instead of raising, so they
        have to be re-raised for the shared retry helper to see them."""
        api = AsyncMock(side_effect=[
            _make_response(success=False, error="Too Many Requests", status_code=429),
            _make_response(data={"groups": [{"id": 1}]}),
        ])
        api.__name__ = "list_groups"

        result = await zendesk_connector._fetch_paginated_list(api, "groups")

        assert api.await_count == 2
        assert result == [{"id": 1}]

    async def test_does_not_retry_an_auth_failure(self, zendesk_connector):
        api = AsyncMock(return_value=_make_response(
            success=False, error="Unauthorized", status_code=401,
        ))
        api.__name__ = "list_groups"

        items, complete = await zendesk_connector._fetch_paginated_list_checked(
            api, "groups"
        )

        assert api.await_count == 1
        assert (items, complete) == ([], False)


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

    async def test_shared_organization_grant_is_attached(self, zendesk_connector):
        """Zendesk's shared-organization setting lets an org's members see each
        other's tickets; without this grant only the requester can."""
        result = await zendesk_connector._ticket_to_record({
            "id": 555, "subject": "Printer", "group_id": 7, "organization_id": 21,
        })

        _, permissions = result
        group_ids = {p.external_id for p in permissions if p.entity_type == EntityType.GROUP}
        assert group_ids == {"group_7", "org_21"}

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

    async def test_submitter_drives_the_creator_fields(self, zendesk_connector):
        """Zendesk's submitter is the person who opened the ticket, which is not the
        requester when an agent files it for a customer. Feeds the CREATED_BY edge."""
        zendesk_connector._user_id_to_data = {
            "10": {"email": "customer@acme.com", "name": "Customer"},
            "30": {"email": "agent@acme.com", "name": "Support Agent"},
        }

        record, _ = await zendesk_connector._ticket_to_record({
            "id": 555,
            "subject": "Printer on fire",
            "group_id": 7,
            "requester_id": 10,
            "submitter_id": 30,
            "created_at": "2026-01-01T00:00:00Z",
        })

        assert record.creator_email == "agent@acme.com"
        assert record.creator_name == "Support Agent"
        assert record.reporter_email == "customer@acme.com"

    async def test_no_creator_when_submitter_absent(self, zendesk_connector):
        record, _ = await zendesk_connector._ticket_to_record({
            "id": 555, "subject": "No submitter", "group_id": 7,
        })
        assert record.creator_email is None

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
# Ticket-to-ticket links
# ===========================================================================


class TestTicketLinks:
    """Without these the graph holds no Record→Record edge, so "which ticket is
    related to this problem" has nothing to traverse."""

    def test_problem_id_becomes_a_causes_link(self, zendesk_connector):
        related = zendesk_connector._parse_ticket_links({"id": 2, "problem_id": 9})

        assert len(related) == 1
        assert related[0].external_record_id == "9"
        assert related[0].relation_type == RecordRelations.CAUSES
        assert related[0].record_type == RecordType.TICKET

    def test_followups_become_related_links(self, zendesk_connector):
        related = zendesk_connector._parse_ticket_links({"id": 2, "followup_ids": [11, 12]})

        assert [r.external_record_id for r in related] == ["11", "12"]
        assert {r.relation_type for r in related} == {RecordRelations.RELATED}

    def test_via_source_links_back_to_the_original(self, zendesk_connector):
        related = zendesk_connector._parse_ticket_links({
            "id": 2,
            "via": {"channel": "follow_up", "source": {"from": {"ticket_id": 3}}},
        })

        assert related[0].external_record_id == "3"
        assert related[0].relation_type == RecordRelations.RELATED

    def test_no_links_yields_empty_list(self, zendesk_connector):
        assert zendesk_connector._parse_ticket_links({"id": 2}) == []

    def test_malformed_via_is_tolerated(self, zendesk_connector):
        assert zendesk_connector._parse_ticket_links({"id": 2, "via": None}) == []
        assert zendesk_connector._parse_ticket_links({"id": 2, "via": {"source": None}}) == []

    def test_self_link_is_dropped(self, zendesk_connector):
        """An edge from a ticket to itself is one a traversal never leaves."""
        assert zendesk_connector._parse_ticket_links({"id": 2, "problem_id": 2}) == []

    def test_duplicate_target_emitted_once(self, zendesk_connector):
        related = zendesk_connector._parse_ticket_links({
            "id": 2, "problem_id": 9, "followup_ids": [9],
        })

        assert len(related) == 1
        assert related[0].relation_type == RecordRelations.CAUSES

    async def test_links_reach_the_record(self, zendesk_connector):
        record, _ = await zendesk_connector._ticket_to_record({
            "id": 2, "subject": "Incident", "group_id": 7, "problem_id": 9,
        })

        assert [r.external_record_id for r in record.related_external_records] == ["9"]


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

    async def test_draft_is_not_published_org_wide(self, zendesk_connector):
        assert await zendesk_connector._article_to_record({
            "id": 42, "title": "Unfinished runbook", "draft": True,
        }) is None

    async def test_segment_restricted_article_is_not_published_org_wide(
        self, zendesk_connector
    ):
        assert await zendesk_connector._article_to_record({
            "id": 42, "title": "Agents only", "user_segment_id": 7,
        }) is None
        assert await zendesk_connector._article_to_record({
            "id": 43, "title": "Agents only", "user_segment_ids": [7],
        }) is None


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

    def test_never_falls_back_to_org_when_nothing_known(self, zendesk_connector):
        # Fail closed: an unresolvable record stays invisible rather than org-wide.
        assert zendesk_connector._record_permissions(None, {}) == []


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
        _ready(zendesk_connector)
        assert zendesk_connector._is_safe_zendesk_asset_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://evil.com/steal",
        "https://zendesk.com.evil.com/steal",
        "https://notzendesk.com/x",
        # Another tenant's subdomain must not qualify as ours.
        "https://other-tenant.zendesk.com/attachments/1",
        # Plaintext would expose the credential in transit.
        "http://acme.zendesk.com/attachments/1",
    ])
    def test_rejects_foreign_hosts(self, zendesk_connector, url):
        _ready(zendesk_connector)
        assert zendesk_connector._is_safe_zendesk_asset_url(url) is False

    async def test_download_refuses_untrusted_host(self, zendesk_connector):
        """Regression: credentials must never be sent to an API-supplied foreign host."""
        datasource = _ready(zendesk_connector)
        datasource.http = MagicMock()
        datasource.http.execute = AsyncMock()

        _attachment_lookup(datasource, "https://evil.com/payload")
        record = MagicMock()
        record.external_record_id = "ticket_1_comment_2_attachment_3"

        with pytest.raises(ValueError, match="untrusted host"):
            await zendesk_connector._process_file_for_streaming(record)
        datasource.http.execute.assert_not_awaited()

    async def test_download_raises_without_url(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        _attachment_lookup(datasource, None)
        record = MagicMock()
        record.external_record_id = "ticket_1_comment_2_attachment_3"
        with pytest.raises(ValueError):
            await zendesk_connector._process_file_for_streaming(record)

    async def test_download_rejects_unparseable_record_id(self, zendesk_connector):
        _ready(zendesk_connector)
        record = MagicMock()
        record.external_record_id = "not-an-attachment"
        with pytest.raises(ValueError, match="Unrecognised"):
            await zendesk_connector._process_file_for_streaming(record)


# ===========================================================================
# Incremental cursor handling
# ===========================================================================


class TestIncrementalCursor:
    async def test_fetch_users_stops_on_repeated_cursor(self, zendesk_connector):
        """Regression: a non-advancing cursor used to loop forever."""
        datasource = _ready(zendesk_connector)
        datasource.incremental_users = AsyncMock(return_value=_make_response(data={
            "users": [{"id": 1, "email": "a@acme.com", "name": "A"}],
            "after_cursor": "stuck",
            "end_of_stream": False,
        }))
        users, _, complete = await zendesk_connector._fetch_users()
        # First page consumed, second call detects the repeated cursor and stops.
        assert datasource.incremental_users.await_count == 2
        assert len(users) == 2
        assert complete is True

    async def test_fetch_users_reports_incomplete_on_failed_page(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_users = AsyncMock(
            return_value=_make_response(success=False, error="429 Too Many Requests")
        )

        users, _, complete = await zendesk_connector._fetch_users()

        assert users == []
        assert complete is False

    async def test_fetch_users_starts_from_epoch(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_users = AsyncMock(return_value=_make_response(data={
            "users": [], "end_of_stream": True,
        }))
        await zendesk_connector._fetch_users()
        assert datasource.incremental_users.await_args.kwargs["start_time"] == (
            DEFAULT_INCREMENTAL_START_TIME
        )

    async def test_incremental_users_retries_a_rate_limited_page(self, zendesk_connector):
        """Incremental exports allow 10 req/min, so a 429 here is routine — and it used
        to end the export outright, truncating the user list."""
        datasource = _ready(zendesk_connector)
        datasource.incremental_users = AsyncMock(side_effect=[
            _make_response(success=False, error="Too Many Requests", status_code=429),
            _make_response(data={
                "users": [{"id": 1, "email": "a@acme.com", "name": "A"}],
                "end_of_stream": True,
            }),
        ])
        datasource.incremental_users.__name__ = "incremental_users"

        users, _, complete = await zendesk_connector._fetch_users()

        assert datasource.incremental_users.await_count == 2
        assert complete is True
        assert len(users) == 1

    async def test_truncated_ticket_export_leaves_sync_point_alone(self, zendesk_connector):
        """Advancing past a failed page skips every ticket it held, permanently."""
        datasource = _ready(zendesk_connector)
        datasource.incremental_tickets = AsyncMock(side_effect=[
            _make_response(data={
                "tickets": [{"id": 1, "subject": "a", "updated_at": "2026-01-01T00:00:00Z"}],
                "after_cursor": "page2",
                "end_of_stream": False,
            }),
            _make_response(success=False, error="500 Internal Server Error"),
        ])
        zendesk_connector.records_sync_point.update_sync_point = AsyncMock()
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(return_value={})

        await zendesk_connector._sync_tickets()

        zendesk_connector.records_sync_point.update_sync_point.assert_not_awaited()

    async def test_sync_tickets_persists_end_time(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_tickets = AsyncMock(return_value=_make_response(data={
            "tickets": [
                {"id": 1, "subject": "a", "updated_at": "2026-01-01T00:00:00Z"},
                {"id": 2, "subject": "b", "updated_at": "2026-01-02T00:00:00Z"},
            ],
            "end_of_stream": True,
        }))
        zendesk_connector.records_sync_point.update_sync_point = AsyncMock()
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(return_value={})

        await zendesk_connector._sync_tickets()

        args = zendesk_connector.records_sync_point.update_sync_point.await_args
        assert args.args[0] == SYNC_POINT_KEY
        assert args.args[1]["lastEndTime"] == 1767312000


# ===========================================================================
# Sideload caching
# ===========================================================================


class TestCacheSideloads:
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
    """Mark the connector initialised so `_get_fresh_datasource` succeeds."""
    connector.external_client = MagicMock()
    connector.external_client.get_subdomain.return_value = "acme"
    connector.data_source = MagicMock()
    return connector.data_source


# ===========================================================================
# run_sync orchestration
# ===========================================================================


class TestRunSync:
    @staticmethod
    def _stub_stages(connector, user):
        connector.records_sync_point.read_sync_point = AsyncMock(
            return_value={"lastEndTime": 1767312000}
        )
        connector._fetch_users = AsyncMock(return_value=([user], {"1": user}, True))
        connector._fetch_groups = AsyncMock(
            return_value=([("g_rg", [])], [("g_ug", [])], True)
        )
        connector._fetch_organizations = AsyncMock(return_value=([("o_ug", [])], True))
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
    async def test_truncated_user_export_skips_membership_sync(
        self, _filters, zendesk_connector, mock_data_entities_processor
    ):
        """on_new_user_groups rebuilds membership from scratch, so a partial user
        list would silently revoke access for everyone it missed."""
        _ready(zendesk_connector)
        self._stub_stages(zendesk_connector, _app_user())
        user = _app_user()
        zendesk_connector._fetch_users = AsyncMock(return_value=([user], {"1": user}, False))

        await zendesk_connector.run_sync()

        mock_data_entities_processor.on_new_user_groups.assert_not_awaited()
        # Record groups are unaffected — only membership rebuilds are destructive.
        mock_data_entities_processor.on_new_record_groups.assert_awaited()

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_truncated_membership_export_skips_membership_sync(
        self, _filters, zendesk_connector, mock_data_entities_processor
    ):
        """A 400 past Zendesk's 10,000-record offset cap truncates the membership
        list; rebuilding groups from it revokes whoever fell off the end."""
        _ready(zendesk_connector)
        self._stub_stages(zendesk_connector, _app_user())
        zendesk_connector._fetch_groups = AsyncMock(
            return_value=([("g_rg", [])], [("g_ug", [])], False)
        )

        await zendesk_connector.run_sync()

        assert all(
            call.args[0] != [("g_ug", [])]
            for call in mock_data_entities_processor.on_new_user_groups.await_args_list
        )

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_skips_empty_collections(
        self, _filters, zendesk_connector, mock_data_entities_processor
    ):
        _ready(zendesk_connector)
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(return_value={})
        zendesk_connector._fetch_users = AsyncMock(return_value=([], {}, True))
        zendesk_connector._fetch_groups = AsyncMock(return_value=([], [], True))
        zendesk_connector._fetch_organizations = AsyncMock(return_value=([], True))
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

        record_groups, user_groups, _ = await zendesk_connector._fetch_groups(
            {"1": user}
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

        await zendesk_connector._fetch_groups({})

        assert zendesk_connector._group_id_to_data["7"]["name"] == "Billing"

    async def test_membership_for_unknown_user_is_dropped(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7, "name": "Sales"}],
        }))
        datasource.list_group_memberships = AsyncMock(return_value=_make_response(data={
            "group_memberships": [{"group_id": 7, "user_id": 999}],
        }))

        _, user_groups, _ = await zendesk_connector._fetch_groups({})

        assert user_groups[0][1] == []

    async def test_falls_back_to_synthetic_name(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.list_groups = AsyncMock(return_value=_make_response(data={
            "groups": [{"id": 7}],
        }))
        datasource.list_group_memberships = AsyncMock(
            return_value=_make_response(data={"group_memberships": []})
        )

        record_groups, _, _ = await zendesk_connector._fetch_groups({})

        assert record_groups[0][0].name == "Group 7"


# ===========================================================================
# Organizations
# ===========================================================================


class TestFetchOrganizations:
    """Organizations are user groups only. They are not RecordGroups: nothing files
    a record under one, so they would render permanently empty."""

    async def test_builds_user_group(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"id": 21, "name": "Acme Corp",
                                   "details": "Enterprise account"}],
                "end_of_stream": True,
            })
        )

        user_groups, _ = await zendesk_connector._fetch_organizations()

        assert user_groups[0][0].source_user_group_id == "org_21"
        assert user_groups[0][0].name == "Acme Corp"

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

        user_groups, _ = await zendesk_connector._fetch_organizations()

        assert user_groups[0][1] == [user]
        assert datasource.incremental_organizations.await_count == 1

    async def test_pages_on_end_time(self, zendesk_connector):
        """The time-based export has no cursor — the next window starts at end_time."""
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(side_effect=[
            _make_response(data={
                "organizations": [{"id": 21, "name": "Acme"}],
                "end_time": 1767312000,
                "end_of_stream": False,
            }),
            _make_response(data={
                "organizations": [{"id": 22, "name": "Beta"}],
                "end_time": 1767398400,
                "end_of_stream": True,
            }),
        ])

        user_groups, _ = await zendesk_connector._fetch_organizations()

        assert datasource.incremental_organizations.await_count == 2
        assert datasource.incremental_organizations.await_args_list[1].kwargs[
            "start_time"
        ] == 1767312000
        assert [ug.source_user_group_id for ug, _ in user_groups] == ["org_21", "org_22"]

    async def test_stops_on_non_advancing_end_time(self, zendesk_connector):
        """A repeated end_time would page over the same window forever."""
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"id": 21, "name": "Acme"}],
                "end_time": DEFAULT_INCREMENTAL_START_TIME,
                "end_of_stream": False,
            })
        )

        await zendesk_connector._fetch_organizations()

        assert datasource.incremental_organizations.await_count == 1

    async def test_truncated_org_export_skips_membership_sync(
        self, zendesk_connector, mock_data_entities_processor
    ):
        """on_new_user_groups rebuilds each org from scratch, so partial membership
        revokes access for whoever fell off the failed page."""
        _ready(zendesk_connector)
        zendesk_connector._fetch_users = AsyncMock(return_value=([], {}, True))
        zendesk_connector._fetch_groups = AsyncMock(return_value=([], [], True))
        zendesk_connector._fetch_organizations = AsyncMock(
            return_value=([("o_ug", [])], False)
        )
        zendesk_connector._sync_tickets = AsyncMock(return_value=0)
        zendesk_connector._sync_help_center_articles = AsyncMock(return_value=0)
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(
            return_value={"lastEndTime": 1767312000}
        )

        with patch("app.connectors.sources.zendesk.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=({}, {})):
            await zendesk_connector.run_sync()

        mock_data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_logs_and_stops_on_failure(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(success=False, error="401 Unauthorized")
        )

        assert await zendesk_connector._fetch_organizations() == ([], False)

    async def test_skips_org_without_id(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.incremental_organizations = AsyncMock(
            return_value=_make_response(data={
                "organizations": [{"name": "No ID"}],
                "end_of_stream": True,
            })
        )

        assert await zendesk_connector._fetch_organizations() == ([], True)


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

        await zendesk_connector._sync_help_center_sections()

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

        await zendesk_connector._sync_help_center_sections()

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

        count = await zendesk_connector._sync_help_center_articles()

        assert count == 1
        assert order == ["sections", "articles"]

    async def test_returns_zero_when_knowledge_base_disabled(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        disabled = MagicMock()
        disabled.get_value.return_value = False
        zendesk_connector.indexing_filters = {
            IndexingFilterKey.KNOWLEDGE_BASE.value: disabled
        }

        assert await zendesk_connector._sync_help_center_articles() == 0
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

    async def test_internal_notes_are_not_indexed(self, zendesk_connector):
        """The record carries a requester grant, so an internal note here leaks
        agent-only text to the customer who opened the ticket."""
        datasource = _ready(zendesk_connector)
        datasource.list_comments = AsyncMock(return_value=_make_response(data={
            "comments": [
                {"id": 1, "author_id": 9, "body": "Customer visible", "public": True},
                {"id": 2, "author_id": 9, "body": "Refund risk, escalate", "public": False},
            ],
        }))
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "23"
        record.record_name = "Latency"
        record.weburl = None

        body = (
            await zendesk_connector._process_ticket_blockgroups_for_streaming(record)
        ).decode()

        assert "Customer visible" in body
        assert "Refund risk, escalate" not in body

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

        _attachment_lookup(datasource, "https://acme.zendesk.com/attachments/9")
        record = MagicMock()
        record.external_record_id = "ticket_1_comment_2_attachment_9"

        assert await zendesk_connector._process_file_for_streaming(record) == b"filebytes"
        sent = datasource.http.execute.await_args.args[0]
        assert sent.headers["Authorization"] == "Basic secret"

    async def test_cdn_download_withholds_api_credentials(self, zendesk_connector):
        """CDN URLs are pre-signed and the host is shared across tenants — sending
        the API token there leaks this tenant's credentials."""
        datasource = _ready(zendesk_connector)
        datasource.http = MagicMock()
        datasource.http.headers = {"Authorization": "Basic secret"}
        response = MagicMock()
        response.status = 200
        response.bytes.return_value = b"filebytes"
        datasource.http.execute = AsyncMock(return_value=response)

        _attachment_lookup(datasource, "https://p1.zdusercontent.com/attachment/9")
        record = MagicMock()
        record.external_record_id = "ticket_1_comment_2_attachment_9"

        await zendesk_connector._process_file_for_streaming(record)

        assert datasource.http.execute.await_args.args[0].headers == {}

    async def test_file_download_raises_on_error_status(self, zendesk_connector):
        datasource = _ready(zendesk_connector)
        datasource.http = MagicMock()
        datasource.http.headers = {}
        response = MagicMock()
        response.status = 404
        datasource.http.execute = AsyncMock(return_value=response)

        _attachment_lookup(datasource, "https://acme.zendesk.com/attachments/9")
        record = MagicMock()
        record.external_record_id = "ticket_1_comment_2_attachment_9"

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

    async def test_attachment_inherits_parent_requester_grant(
        self, zendesk_connector, mock_data_entities_processor
    ):
        parent = self._parent()
        parent.reporter_email = "req@acme.com"
        comment = {"id": 5, "public": True, "attachments": [{
            "id": 88, "file_name": "a.txt",
            "content_url": "https://acme.zendesk.com/attachments/88",
        }]}

        await zendesk_connector._build_attachment_child_records(comment, parent)

        _, permissions = mock_data_entities_processor.on_new_records.await_args.args[0][0]
        assert {p.entity_type for p in permissions} == {EntityType.GROUP, EntityType.USER}
        assert next(p for p in permissions if p.entity_type == EntityType.USER).email == (
            "req@acme.com"
        )

    async def test_internal_note_attachments_are_skipped(self, zendesk_connector):
        comment = {"id": 5, "public": False, "attachments": [{
            "id": 88, "file_name": "internal.pdf",
            "content_url": "https://acme.zendesk.com/attachments/88",
        }]}

        assert await zendesk_connector._build_attachment_child_records(
            comment, self._parent()
        ) == []

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

    async def test_stream_record_serves_file_with_its_own_mime_type(
        self, zendesk_connector
    ):
        """Labelling attachment bytes as a BlocksContainer makes every download an
        unopenable JSON blob."""
        _ready(zendesk_connector)
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "trace.log"
        record.mime_type = "text/plain"
        record.external_record_id = "att-9"
        zendesk_connector._process_file_for_streaming = AsyncMock(return_value=b"bytes")

        response = await zendesk_connector.stream_record(record)

        assert response.media_type == "text/plain"


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


# ===========================================================================
# Inline image embedding
# ===========================================================================


def _img_response(status=200, body=b"\x89PNG\r\n", content_type="image/png"):
    resp = MagicMock()
    resp.status = status
    resp.bytes.return_value = body
    resp.content_type = content_type
    return resp


class TestInlineImages:
    @staticmethod
    def _ds(connector, response=None):
        datasource = _ready(connector)
        datasource.http = MagicMock()
        datasource.http.headers = {"Authorization": "Basic secret"}
        datasource.http.execute = AsyncMock(return_value=response or _img_response())
        return datasource

    async def test_tenant_image_becomes_data_uri(self, zendesk_connector):
        self._ds(zendesk_connector)
        html = '<p>x</p><img src="https://acme.zendesk.com/attachments/token/a/?name=s.png">'
        zendesk_connector.external_client = MagicMock()
        zendesk_connector.external_client.get_subdomain.return_value = "acme"

        out = await zendesk_connector._inline_images_as_base64(html)

        assert "data:image/png;base64," in out
        assert "attachments/token" not in out

    async def test_untrusted_host_is_left_alone_and_never_fetched(self, zendesk_connector):
        """Credentials must never reach a host the API happened to return."""
        datasource = self._ds(zendesk_connector)
        html = '<img src="https://zendesk.com.evil.com/steal.png">'

        out = await zendesk_connector._inline_images_as_base64(html)

        assert out == html
        datasource.http.execute.assert_not_awaited()

    async def test_oversized_image_is_skipped(self, zendesk_connector):
        big = _img_response(body=b"x" * (MAX_INLINE_IMAGE_BYTES + 1))
        self._ds(zendesk_connector, big)
        zendesk_connector.external_client = MagicMock()
        zendesk_connector.external_client.get_subdomain.return_value = "acme"
        html = '<img src="https://acme.zendesk.com/attachments/token/a/?name=s.png">'

        assert await zendesk_connector._inline_images_as_base64(html) == html

    async def test_non_image_content_type_is_skipped(self, zendesk_connector):
        self._ds(zendesk_connector, _img_response(content_type="text/html"))
        zendesk_connector.external_client = MagicMock()
        zendesk_connector.external_client.get_subdomain.return_value = "acme"
        html = '<img src="https://acme.zendesk.com/attachments/token/a/?name=s.png">'

        assert await zendesk_connector._inline_images_as_base64(html) == html

    async def test_fetch_failure_leaves_url_for_the_pipeline(self, zendesk_connector):
        """A URL we cannot fetch stays put so the unauthenticated pipeline can retry."""
        self._ds(zendesk_connector, _img_response(status=404))
        zendesk_connector.external_client = MagicMock()
        zendesk_connector.external_client.get_subdomain.return_value = "acme"
        html = '<img src="https://acme.zendesk.com/attachments/token/a/?name=s.png">'

        assert await zendesk_connector._inline_images_as_base64(html) == html

    async def test_repeated_url_fetched_once(self, zendesk_connector):
        datasource = self._ds(zendesk_connector)
        zendesk_connector.external_client = MagicMock()
        zendesk_connector.external_client.get_subdomain.return_value = "acme"
        url = "https://acme.zendesk.com/attachments/token/a/?name=s.png"
        html = f'<img src="{url}"><p>y</p><img src="{url}">'

        out = await zendesk_connector._inline_images_as_base64(html)

        assert datasource.http.execute.await_count == 1
        assert out.count("data:image/png;base64,") == 2

    async def test_no_img_tag_skips_datasource_entirely(self, zendesk_connector):
        zendesk_connector.data_source = None
        zendesk_connector.external_client = None
        assert await zendesk_connector._inline_images_as_base64("<p>text only</p>") == "<p>text only</p>"

    async def test_existing_data_uri_untouched(self, zendesk_connector):
        datasource = self._ds(zendesk_connector)
        html = '<img src="data:image/png;base64,AAAA">'

        assert await zendesk_connector._inline_images_as_base64(html) == html
        datasource.http.execute.assert_not_awaited()


# ===========================================================================
# OAuth token rotation
# ===========================================================================


class TestOAuthTokenRotation:
    @staticmethod
    def _oauth_ready(connector, in_use="old-token"):
        inner = MagicMock()
        inner.access_token = in_use
        client = MagicMock()
        client.get_client.return_value = inner
        connector.external_client = client
        connector.data_source = MagicMock()
        return connector

    async def test_rebuilds_client_when_stored_token_changed(self, zendesk_connector):
        """Regression: the refresh service rotates the token in config every ~20 min but
        cannot reach this object, so the cached client kept 401ing."""
        self._oauth_ready(zendesk_connector, in_use="old-token")
        zendesk_connector.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "new-token"}}
        )
        zendesk_connector.init = AsyncMock(return_value=True)

        await zendesk_connector._get_fresh_datasource()

        zendesk_connector.init.assert_awaited_once()

    async def test_does_not_rebuild_when_token_unchanged(self, zendesk_connector):
        self._oauth_ready(zendesk_connector, in_use="same-token")
        zendesk_connector.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "same-token"}}
        )
        zendesk_connector.init = AsyncMock(return_value=True)

        await zendesk_connector._get_fresh_datasource()

        zendesk_connector.init.assert_not_awaited()

    async def test_api_token_auth_never_rebuilds(self, zendesk_connector):
        """API-token clients carry no access_token, so there is nothing to rotate."""
        inner = MagicMock(spec=[])  # no access_token attribute
        client = MagicMock()
        client.get_client.return_value = inner
        zendesk_connector.external_client = client
        zendesk_connector.data_source = MagicMock()
        zendesk_connector.config_service.get_config = AsyncMock()
        zendesk_connector.init = AsyncMock()

        await zendesk_connector._get_fresh_datasource()

        zendesk_connector.init.assert_not_awaited()
        zendesk_connector.config_service.get_config.assert_not_awaited()

    async def test_config_read_failure_keeps_serving_cached_client(self, zendesk_connector):
        """A config blip must not take the sync down; the cached token may still be valid."""
        self._oauth_ready(zendesk_connector)
        zendesk_connector.config_service.get_config = AsyncMock(side_effect=Exception("redis down"))
        zendesk_connector.init = AsyncMock()

        assert await zendesk_connector._get_fresh_datasource() is zendesk_connector.data_source
        zendesk_connector.init.assert_not_awaited()

    async def test_raises_when_uninitialised(self, zendesk_connector):
        zendesk_connector.external_client = None
        zendesk_connector.data_source = None

        with pytest.raises(RuntimeError, match="not initialized"):
            await zendesk_connector._get_fresh_datasource()

    async def test_failed_rebuild_raises_instead_of_serving_stale_token(
        self, zendesk_connector
    ):
        """init() reports failure by returning False; serving the cached client then
        401s on every call while the log blames the export."""
        self._oauth_ready(zendesk_connector, in_use="old-token")
        zendesk_connector.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "new-token"}}
        )
        zendesk_connector.init = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="could not be rebuilt"):
            await zendesk_connector._get_fresh_datasource()


# ===========================================================================
# Full-sync re-emission
# ===========================================================================


class TestFullSyncReemission:
    """A full sync deletes the sync point and every BELONGS_TO edge with it. Skipping
    unchanged records then leaves them orphaned — present but unreachable from the App,
    which is what made the UI report zero records after every full sync."""

    @staticmethod
    def _unchanged(mock_tx_store, updated_ms=1767398400000):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.version = 3
        existing.source_updated_at = updated_ms
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        return existing

    async def test_unchanged_ticket_reemitted_without_sync_point(
        self, zendesk_connector, mock_tx_store
    ):
        self._unchanged(mock_tx_store)
        zendesk_connector._reemit_unchanged = True

        result = await zendesk_connector._ticket_to_record({
            "id": 7,
            "subject": "PIPESHUB TEST - Password Reset",
            "group_id": 1,
            "updated_at": "2026-01-03T00:00:00Z",
        })

        assert result is not None

    async def test_unchanged_article_reemitted_without_sync_point(
        self, zendesk_connector, mock_tx_store
    ):
        self._unchanged(mock_tx_store)
        zendesk_connector._reemit_unchanged = True

        result = await zendesk_connector._article_to_record({
            "id": 55,
            "title": "How to reset",
            "section_id": 31,
            "updated_at": "2026-01-03T00:00:00Z",
        })

        assert result is not None

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_missing_sync_point_enables_reemission(self, _f, zendesk_connector):
        _ready(zendesk_connector)
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(return_value={})
        zendesk_connector._fetch_users = AsyncMock(return_value=([], {}, True))
        zendesk_connector._fetch_groups = AsyncMock(return_value=([], [], True))
        zendesk_connector._fetch_organizations = AsyncMock(return_value=([], True))
        zendesk_connector._sync_tickets = AsyncMock(return_value=0)
        zendesk_connector._sync_help_center_articles = AsyncMock(return_value=0)

        await zendesk_connector.run_sync()

        assert zendesk_connector._reemit_unchanged is True

    @patch("app.connectors.sources.zendesk.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=({}, {}))
    async def test_existing_sync_point_keeps_skip_optimisation(self, _f, zendesk_connector):
        _ready(zendesk_connector)
        zendesk_connector.records_sync_point.read_sync_point = AsyncMock(
            return_value={"lastEndTime": 1767312000}
        )
        zendesk_connector._fetch_users = AsyncMock(return_value=([], {}, True))
        zendesk_connector._fetch_groups = AsyncMock(return_value=([], [], True))
        zendesk_connector._fetch_organizations = AsyncMock(return_value=([], True))
        zendesk_connector._sync_tickets = AsyncMock(return_value=0)
        zendesk_connector._sync_help_center_articles = AsyncMock(return_value=0)

        await zendesk_connector.run_sync()

        assert zendesk_connector._reemit_unchanged is False
