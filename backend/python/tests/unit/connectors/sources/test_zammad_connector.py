"""Tests for Zammad connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus, RecordRelations
from app.connectors.sources.zammad.connector import (
    ZAMMAD_LINK_OBJECT_MAP,
    ZAMMAD_LINK_TYPE_MAP,
    ZammadConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
import base64
from app.connectors.sources.zammad.connector import (
    ATTACHMENT_ID_PARTS_COUNT,
    BATCH_SIZE_KB_ANSWERS,
    KB_ANSWER_ATTACHMENT_PARTS_COUNT,
    ZAMMAD_CONFIG_PATH,
    ZAMMAD_LINK_OBJECT_MAP,
    ZAMMAD_LINK_TYPE_MAP,
    ZammadConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    ItemType,
    MimeTypes,
    OriginTypes,
    Priority,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.zammad")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-zm-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_new_app_roles = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": "https://zammad.example.com",
            "token": "test-zammad-token",
        },
    })
    return svc


@pytest.fixture()
def zammad_connector(mock_logger, mock_data_entities_processor,
                     mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="zm-conn-1",
            scope="team",
            created_by="test-user",
        )
    return connector


def _make_response(success=True, data=None, error=None, message=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    resp.message = message
    return resp


# ===========================================================================
# Constants
# ===========================================================================

class TestZammadConstants:
    def test_link_type_map_has_expected_keys(self):
        assert "normal" in ZAMMAD_LINK_TYPE_MAP
        assert "parent" in ZAMMAD_LINK_TYPE_MAP
        assert "child" in ZAMMAD_LINK_TYPE_MAP

    def test_link_type_normal(self):
        assert ZAMMAD_LINK_TYPE_MAP["normal"] == RecordRelations.RELATED

    def test_link_type_parent(self):
        assert ZAMMAD_LINK_TYPE_MAP["parent"] == RecordRelations.DEPENDS_ON

    def test_link_type_child(self):
        assert ZAMMAD_LINK_TYPE_MAP["child"] == RecordRelations.LINKED_TO

    def test_link_object_map_ticket(self):
        assert ZAMMAD_LINK_OBJECT_MAP["Ticket"] == RecordType.TICKET

    def test_link_object_map_kb_answer(self):
        assert ZAMMAD_LINK_OBJECT_MAP["KnowledgeBase::Answer::Translation"] == RecordType.WEBPAGE


# ===========================================================================
# ZammadConnector
# ===========================================================================

class TestZammadConnectorInit:
    def test_constructor(self, zammad_connector):
        assert zammad_connector.connector_id == "zm-conn-1"
        assert zammad_connector.data_source is None
        assert zammad_connector.external_client is None
        assert zammad_connector._state_map == {}
        assert zammad_connector._priority_map == {}

    @patch("app.connectors.sources.zammad.connector.ZammadClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.zammad.connector.ZammadDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, zammad_connector):
        mock_client = MagicMock()
        mock_client.get_base_url.return_value = "https://zammad.example.com"
        mock_build.return_value = mock_client

        mock_ds = MagicMock()
        # Mock successful state/priority loading
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"id": 1, "name": "open"}, {"id": 2, "name": "closed"}]
        mock_ds.list_ticket_states = AsyncMock(return_value=states_resp)

        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = [{"id": 1, "name": "low"}, {"id": 2, "name": "high"}]
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        mock_ds_cls.return_value = mock_ds

        result = await zammad_connector.init()
        assert result is True
        assert zammad_connector.base_url == "https://zammad.example.com"
        assert zammad_connector._state_map == {1: "open", 2: "closed"}
        assert zammad_connector._priority_map == {1: "low", 2: "high"}

    @patch("app.connectors.sources.zammad.connector.ZammadClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_exception(self, mock_build, zammad_connector):
        mock_build.side_effect = Exception("Auth failed")
        result = await zammad_connector.init()
        assert result is False


class TestZammadGetFreshDatasource:
    async def test_raises_if_not_initialized(self, zammad_connector):
        zammad_connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await zammad_connector._get_fresh_datasource()

    async def test_raises_if_no_config(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        zammad_connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await zammad_connector._get_fresh_datasource()

    async def test_raises_if_no_token(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        zammad_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "token": ""}
        })
        with pytest.raises(Exception, match="No access token"):
            await zammad_connector._get_fresh_datasource()

    async def test_returns_existing_datasource_if_token_unchanged(self, zammad_connector):
        mock_client = MagicMock()
        internal = MagicMock()
        internal.token = "test-zammad-token"
        mock_client.get_client.return_value = internal
        zammad_connector.external_client = mock_client
        zammad_connector.data_source = MagicMock()

        result = await zammad_connector._get_fresh_datasource()
        assert result is zammad_connector.data_source

    @patch("app.connectors.sources.zammad.connector.ZammadClient.build_from_services", new_callable=AsyncMock)
    async def test_rebuilds_client_if_token_changed(self, mock_build, zammad_connector):
        """When token changes, client is rebuilt."""
        mock_client = MagicMock()
        internal = MagicMock()
        internal.token = "old-token"
        mock_client.get_client.return_value = internal
        zammad_connector.external_client = mock_client
        zammad_connector.data_source = MagicMock()

        new_client = MagicMock()
        new_client.get_base_url.return_value = "https://zammad.example.com"
        mock_build.return_value = new_client

        result = await zammad_connector._get_fresh_datasource()
        mock_build.assert_awaited_once()
        assert zammad_connector.external_client == new_client


class TestZammadGroupFilter:
    def test_no_filters_allows_all(self, zammad_connector):
        zammad_connector.sync_filters = None
        assert zammad_connector._is_group_allowed_by_filter("1") is True

    def test_empty_filter_allows_all(self, zammad_connector):
        mock_filters = MagicMock()
        mock_filters.get.return_value = None
        zammad_connector.sync_filters = mock_filters
        assert zammad_connector._is_group_allowed_by_filter("1") is True

    def test_in_filter_matches(self, zammad_connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["1", "2", "3"]
        mock_operator = MagicMock()
        mock_operator.value = "in"
        mock_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        zammad_connector.sync_filters = mock_filters

        assert zammad_connector._is_group_allowed_by_filter("1") is True
        assert zammad_connector._is_group_allowed_by_filter("99") is False

    def test_not_in_filter_excludes(self, zammad_connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["5", "6"]
        mock_operator = MagicMock()
        mock_operator.value = "not_in"
        mock_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        zammad_connector.sync_filters = mock_filters

        assert zammad_connector._is_group_allowed_by_filter("5") is False
        assert zammad_connector._is_group_allowed_by_filter("1") is True

    def test_empty_value_allows_all(self, zammad_connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = []
        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        zammad_connector.sync_filters = mock_filters
        assert zammad_connector._is_group_allowed_by_filter("1") is True


class TestZammadLoadLookupTables:
    async def test_loads_states_and_priorities(self, zammad_connector):
        mock_ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"id": 1, "name": "new"}, {"id": 2, "name": "pending"}]
        mock_ds.list_ticket_states = AsyncMock(return_value=states_resp)

        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = [{"id": 1, "name": "normal"}]
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        zammad_connector.data_source = mock_ds
        await zammad_connector._load_lookup_tables()
        assert zammad_connector._state_map == {1: "new", 2: "pending"}
        assert zammad_connector._priority_map == {1: "normal"}

    async def test_handles_state_error_gracefully(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_ticket_states = AsyncMock(side_effect=Exception("API error"))
        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = []
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        zammad_connector.data_source = mock_ds
        # Should not raise
        await zammad_connector._load_lookup_tables()
        assert zammad_connector._state_map == {}

    async def test_skips_entries_without_name(self, zammad_connector):
        mock_ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"id": 1, "name": ""}, {"id": 2, "name": "open"}]
        mock_ds.list_ticket_states = AsyncMock(return_value=states_resp)

        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = []
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        zammad_connector.data_source = mock_ds
        await zammad_connector._load_lookup_tables()
        assert 1 not in zammad_connector._state_map  # Empty name skipped
        assert zammad_connector._state_map == {2: "open"}

    async def test_no_datasource_returns_early(self, zammad_connector):
        zammad_connector.data_source = None
        await zammad_connector._load_lookup_tables()
        assert zammad_connector._state_map == {}
        assert zammad_connector._priority_map == {}


class TestZammadFetchUsers:
    async def test_fetch_users_single_page(self, zammad_connector):
        """Fetches users, builds email map, skips inactive and system users."""
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "email": "alice@example.com", "active": True, "firstname": "Alice", "lastname": "Smith", "role_ids": [1, 2]},
                {"id": 2, "email": "noreply@example.com", "active": True, "firstname": "No", "lastname": "Reply", "role_ids": []},
                {"id": 3, "email": "bob@example.com", "active": False, "firstname": "Bob", "lastname": "Inactive", "role_ids": []},
                {"id": 4, "email": "", "active": True, "firstname": "No", "lastname": "Email", "role_ids": []},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, email_map = await zammad_connector._fetch_users()

        # Only alice should be in the result (bob is inactive, noreply is system, no-email lacks email)
        assert len(users) == 1
        assert "alice@example.com" in email_map
        # User ID to data should have alice's mapping
        assert 1 in zammad_connector._user_id_to_data
        assert zammad_connector._user_id_to_data[1]["role_ids"] == [1, 2]

    async def test_fetch_users_pagination(self, zammad_connector):
        """Fetches users across multiple pages."""
        page1_data = [{"id": i, "email": f"user{i}@example.com", "active": True, "firstname": f"User{i}", "lastname": "", "role_ids": []} for i in range(1, 101)]
        page2_data = [{"id": 101, "email": "user101@example.com", "active": True, "firstname": "User101", "lastname": "", "role_ids": []}]

        call_count = 0

        async def mock_list_users(page=1, per_page=100):
            nonlocal call_count
            call_count += 1
            if page == 1:
                return _make_response(success=True, data=page1_data)
            else:
                return _make_response(success=True, data=page2_data)

        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(side_effect=mock_list_users)
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, email_map = await zammad_connector._fetch_users()
        assert len(users) == 101
        assert call_count == 2

    async def test_fetch_users_api_failure(self, zammad_connector):
        """Returns empty when first page fails."""
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, email_map = await zammad_connector._fetch_users()
        assert users == []
        assert email_map == {}


class TestZammadFetchGroups:
    async def test_fetch_groups_creates_record_and_user_groups(self, zammad_connector):
        """Fetches groups, creates RecordGroups with permissions, and UserGroups with members."""
        zammad_connector.base_url = "https://zammad.example.com"
        zammad_connector.sync_filters = MagicMock()
        zammad_connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        # User mapping for member resolution
        alice = AppUser(app_name=Connectors.ZAMMAD, connector_id="zm-conn-1", source_user_id="1", email="alice@example.com", full_name="Alice")
        user_email_map = {"alice@example.com": alice}
        zammad_connector._user_id_to_data = {1: {"email": "alice@example.com", "role_ids": []}}

        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 10, "name": "Support", "active": True, "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-02T00:00:00Z"}]
        ))
        mock_ds.get_group = AsyncMock(return_value=_make_response(
            success=True,
            data={"user_ids": [1]}
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record_groups, user_groups = await zammad_connector._fetch_groups(user_email_map)

        assert len(record_groups) == 1
        rg, perms = record_groups[0]
        assert rg.external_group_id == "group_10"
        assert rg.name == "Support"
        assert len(perms) == 1  # Group permission

        assert len(user_groups) == 1
        ug, members = user_groups[0]
        assert ug.source_user_group_id == "10"
        assert len(members) == 1
        assert members[0].email == "alice@example.com"

    async def test_fetch_groups_skips_filtered(self, zammad_connector):
        """Groups excluded by filter are skipped."""
        zammad_connector.sync_filters = MagicMock()
        zammad_connector._is_group_allowed_by_filter = MagicMock(return_value=False)

        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 10, "name": "Support", "active": True}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record_groups, user_groups = await zammad_connector._fetch_groups({})
        assert len(record_groups) == 0
        assert len(user_groups) == 0


class TestZammadSyncRoles:
    async def test_sync_roles_with_user_mapping(self, zammad_connector):
        """Syncs roles and maps users to roles via role_ids."""
        alice = AppUser(app_name=Connectors.ZAMMAD, connector_id="zm-conn-1", source_user_id="1", email="alice@example.com", full_name="Alice")
        user_email_map = {"alice@example.com": alice}
        zammad_connector._user_id_to_data = {1: {"email": "alice@example.com", "role_ids": [5]}}

        mock_ds = MagicMock()
        mock_ds.list_roles = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 5, "name": "Agent", "active": True, "created_at": "", "updated_at": ""}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await zammad_connector._sync_roles([alice], user_email_map)
        zammad_connector.data_entities_processor.on_new_app_roles.assert_awaited_once()
        args = zammad_connector.data_entities_processor.on_new_app_roles.call_args[0][0]
        assert len(args) == 1
        role, role_users = args[0]
        assert role.name == "Agent"
        assert len(role_users) == 1

    async def test_sync_roles_skips_inactive(self, zammad_connector):
        """Inactive roles are skipped."""
        mock_ds = MagicMock()
        mock_ds.list_roles = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 5, "name": "Retired", "active": False}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await zammad_connector._sync_roles([], {})
        # No roles found (inactive skipped)
        zammad_connector.data_entities_processor.on_new_app_roles.assert_not_awaited()


class TestZammadParseDateTime:
    def test_valid_iso_datetime(self, zammad_connector):
        result = zammad_connector._parse_zammad_datetime("2024-06-15T12:30:00Z")
        assert result > 0
        # Verify it's roughly correct (June 15 2024)
        dt = datetime.fromtimestamp(result / 1000, tz=timezone.utc)
        assert dt.year == 2024
        assert dt.month == 6

    def test_empty_string(self, zammad_connector):
        assert zammad_connector._parse_zammad_datetime("") == 0

    def test_invalid_string(self, zammad_connector):
        assert zammad_connector._parse_zammad_datetime("not-a-date") == 0


class TestZammadSyncCheckpoints:
    async def test_get_group_sync_checkpoint(self, zammad_connector):
        zammad_connector.tickets_sync_point = MagicMock()
        zammad_connector.tickets_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 12345})
        result = await zammad_connector._get_group_sync_checkpoint("Support")
        assert result == 12345

    async def test_get_group_sync_checkpoint_none(self, zammad_connector):
        zammad_connector.tickets_sync_point = MagicMock()
        zammad_connector.tickets_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await zammad_connector._get_group_sync_checkpoint("Support")
        assert result is None

    async def test_update_group_sync_checkpoint(self, zammad_connector):
        zammad_connector.tickets_sync_point = MagicMock()
        zammad_connector.tickets_sync_point.update_sync_point = AsyncMock()
        await zammad_connector._update_group_sync_checkpoint("Support", 99999)
        zammad_connector.tickets_sync_point.update_sync_point.assert_awaited_once_with(
            "Support", {"last_sync_time": 99999}
        )

    async def test_update_group_sync_checkpoint_defaults_to_now(self, zammad_connector):
        zammad_connector.tickets_sync_point = MagicMock()
        zammad_connector.tickets_sync_point.update_sync_point = AsyncMock()
        await zammad_connector._update_group_sync_checkpoint("Support")
        call_args = zammad_connector.tickets_sync_point.update_sync_point.call_args[0]
        assert call_args[0] == "Support"
        assert call_args[1]["last_sync_time"] > 0

    async def test_get_kb_sync_checkpoint(self, zammad_connector):
        zammad_connector.kb_sync_point = MagicMock()
        zammad_connector.kb_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 54321})
        result = await zammad_connector._get_kb_sync_checkpoint()
        assert result == 54321

    async def test_update_kb_sync_checkpoint(self, zammad_connector):
        zammad_connector.kb_sync_point = MagicMock()
        zammad_connector.kb_sync_point.update_sync_point = AsyncMock()
        await zammad_connector._update_kb_sync_checkpoint(88888)
        zammad_connector.kb_sync_point.update_sync_point.assert_awaited_once_with(
            "kb_sync", {"last_sync_time": 88888}
        )


class TestZammadTransformTicket:
    async def test_transform_ticket_basic(self, zammad_connector):
        """Transforms a basic ticket with group, state, priority, creator, and assignee."""
        zammad_connector.base_url = "https://zammad.example.com"
        zammad_connector._state_map = {1: "open"}
        zammad_connector._priority_map = {2: "high"}
        zammad_connector._user_id_to_data = {
            100: {"email": "creator@example.com", "role_ids": []},
            200: {"email": "assignee@example.com", "role_ids": []},
        }

        # Mock datasource for user lookups
        mock_ds = MagicMock()

        async def mock_get_user(user_id):
            if user_id == 100:
                return _make_response(success=True, data={"firstname": "Alice", "lastname": "Creator"})
            elif user_id == 200:
                return _make_response(success=True, data={"firstname": "Bob", "lastname": "Assignee"})
            return _make_response(success=False)

        mock_ds.get_user = AsyncMock(side_effect=mock_get_user)
        mock_ds.list_links = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        ticket_data = {
            "id": 42,
            "title": "Server crash",
            "group_id": 10,
            "state_id": 1,
            "priority_id": 2,
            "customer_id": 100,
            "owner_id": 200,
            "created_at": "2024-06-01T10:00:00Z",
            "updated_at": "2024-06-02T15:00:00Z",
        }

        result = await zammad_connector._transform_ticket_to_ticket_record(ticket_data)

        assert result is not None
        assert result.record_name == "Server crash"
        assert result.external_record_id == "42"
        assert result.external_record_group_id == "group_10"
        assert "ticket/zoom/42" in result.weburl
        assert result.creator_email == "creator@example.com"
        assert result.assignee_email == "assignee@example.com"

    async def test_transform_ticket_no_id(self, zammad_connector):
        result = await zammad_connector._transform_ticket_to_ticket_record({})
        assert result is None

    async def test_transform_ticket_no_group(self, zammad_connector):
        """Ticket without group_id gets None external_record_group_id."""
        zammad_connector.base_url = "https://zammad.example.com"
        zammad_connector._state_map = {}
        zammad_connector._priority_map = {}
        zammad_connector._user_id_to_data = {}

        mock_ds = MagicMock()
        mock_ds.list_links = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await zammad_connector._transform_ticket_to_ticket_record({"id": 1, "title": "Test"})
        assert result is not None
        assert result.external_record_group_id is None


class TestZammadTransformAttachment:
    async def test_transform_attachment_basic(self, zammad_connector):
        """Transforms a ticket attachment to FileRecord."""
        from app.connectors.core.registry.filters import FilterCollection
        zammad_connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=TicketRecord)
        parent.id = "parent-id"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = "https://zammad.example.com/#ticket/zoom/42"
        parent.source_created_at = 1000
        parent.source_updated_at = 2000

        attachment_data = {
            "id": 99,
            "filename": "report.pdf",
            "size": 1024,
            "preferences": {"Content-Type": "application/pdf"},
        }

        result = await zammad_connector._transform_attachment_to_file_record(
            attachment_data=attachment_data,
            external_record_id="42_1_99",
            parent_record=parent,
            parent_record_type=RecordType.TICKET,
            indexing_filter_key=MagicMock(),
        )

        assert result is not None
        assert result.record_name == "report.pdf"
        assert result.external_record_id == "42_1_99"
        assert result.mime_type == "application/pdf"
        assert result.size_in_bytes == 1024
        assert result.extension == "pdf"
        assert result.parent_record_id == "parent-id"

    async def test_transform_attachment_no_id(self, zammad_connector):
        parent = MagicMock()
        result = await zammad_connector._transform_attachment_to_file_record(
            attachment_data={},
            external_record_id="test",
            parent_record=parent,
            parent_record_type=RecordType.TICKET,
            indexing_filter_key=MagicMock(),
        )
        assert result is None


class TestZammadFetchTicketLinks:
    async def test_fetch_links_normal_and_parent(self, zammad_connector):
        """Fetches ticket links with proper direction filtering."""
        mock_ds = MagicMock()
        mock_ds.list_links = AsyncMock(return_value=_make_response(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "Ticket", "link_object_value": 100},
                    {"link_type": "parent", "link_object": "Ticket", "link_object_value": 50},
                    {"link_type": "child", "link_object": "Ticket", "link_object_value": 200},
                ],
                "assets": {},
            }
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # ticket_id=42, so normal link to 100 (42 < 100, creates edge)
        # parent link to 50 (always creates edge)
        # child link to 200 (skipped)
        result = await zammad_connector._fetch_ticket_links(42)
        assert len(result) == 2

    async def test_fetch_links_normal_skips_larger_id(self, zammad_connector):
        """Normal links skip when current_id >= linked_id (dedup)."""
        mock_ds = MagicMock()
        mock_ds.list_links = AsyncMock(return_value=_make_response(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "Ticket", "link_object_value": 10},
                ],
                "assets": {},
            }
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # ticket_id=42 >= linked_id=10, skip
        result = await zammad_connector._fetch_ticket_links(42)
        assert len(result) == 0

    async def test_fetch_links_kb_answer(self, zammad_connector):
        """KB answer links resolve answer_id from assets."""
        mock_ds = MagicMock()
        mock_ds.list_links = AsyncMock(return_value=_make_response(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "KnowledgeBase::Answer::Translation", "link_object_value": 99},
                ],
                "assets": {
                    "KnowledgeBaseAnswerTranslation": {
                        "99": {"answer_id": 7},
                    },
                },
            }
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await zammad_connector._fetch_ticket_links(1)
        assert len(result) == 1
        assert result[0].external_record_id == "kb_answer_7"
        assert result[0].record_type == RecordType.WEBPAGE


class TestZammadSyncTicketsForGroups:
    async def test_empty_groups(self, zammad_connector):
        """No-op when no groups provided."""
        await zammad_connector._sync_tickets_for_groups([])
        zammad_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_skips_group_without_id(self, zammad_connector):
        """Skips groups missing group_id."""
        rg = RecordGroup(
            external_group_id=None,
            name="Bad Group",
            group_type=RecordGroupType.PROJECT,
            connector_name=Connectors.ZAMMAD,
            connector_id="zm-conn-1",
        )
        zammad_connector._get_group_sync_checkpoint = AsyncMock(return_value=None)

        await zammad_connector._sync_tickets_for_groups([(rg, [])])
        zammad_connector.data_entities_processor.on_new_records.assert_not_awaited()


class TestZammadRunSync:
    @patch("app.connectors.sources.zammad.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_full_flow(self, mock_load_filters, zammad_connector):
        """run_sync orchestrates all sync steps."""
        from app.connectors.core.registry.filters import FilterCollection
        mock_load_filters.return_value = (FilterCollection(), FilterCollection())

        zammad_connector.external_client = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        zammad_connector._fetch_users = AsyncMock(return_value=([], {}))
        zammad_connector._fetch_groups = AsyncMock(return_value=([], []))
        zammad_connector._sync_roles = AsyncMock()
        zammad_connector._sync_tickets_for_groups = AsyncMock()
        zammad_connector._sync_knowledge_bases = AsyncMock()

        await zammad_connector.run_sync()

        zammad_connector._fetch_users.assert_awaited_once()
        zammad_connector._fetch_groups.assert_awaited_once()
        zammad_connector._sync_roles.assert_awaited_once()
        zammad_connector._sync_tickets_for_groups.assert_awaited_once()
        zammad_connector._sync_knowledge_bases.assert_awaited_once()


class TestZammadFetchTicketAttachments:
    async def test_fetch_ticket_attachments_skips_system(self, zammad_connector):
        """Skips attachments from System sender articles."""
        mock_ds = MagicMock()
        mock_ds.list_ticket_articles = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "sender": "System", "from": "", "preferences": {}, "attachments": [{"id": 10}]},
                {"id": 2, "sender": "Customer", "from": "user@example.com", "preferences": {}, "attachments": [{"id": 20, "filename": "doc.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}]},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        from app.connectors.core.registry.filters import FilterCollection
        zammad_connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        result = await zammad_connector._fetch_ticket_attachments({"id": 42}, parent)
        # Only article 2's attachment should be returned
        assert len(result) == 1

    async def test_fetch_ticket_attachments_skips_mailer_daemon(self, zammad_connector):
        """Skips attachments from MAILER-DAEMON articles."""
        mock_ds = MagicMock()
        mock_ds.list_ticket_articles = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "sender": "Agent", "from": "MAILER-DAEMON@example.com", "preferences": {}, "attachments": [{"id": 10}]},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"

        result = await zammad_connector._fetch_ticket_attachments({"id": 42}, parent)
        assert len(result) == 0

    async def test_fetch_ticket_attachments_no_ticket_id(self, zammad_connector):
        result = await zammad_connector._fetch_ticket_attachments({}, MagicMock())
        assert result == []


# ===========================================================================
# DEEP SYNC LOOP TESTS — run_sync, _sync_tickets_for_groups,
# _fetch_tickets_for_group_batch, _fetch_users, _fetch_groups,
# _sync_roles, _sync_knowledge_bases
# ===========================================================================


class TestZammadRunSync:
    """Tests for run_sync orchestration."""

    async def test_full_sync_flow(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        mock_ds = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users = [MagicMock(email="user@test.com")]
        user_map = {"user@test.com": users[0]}
        zammad_connector._fetch_users = AsyncMock(return_value=(users, user_map))

        rg = MagicMock()
        rg.external_group_id = "group_1"
        rg.name = "Support"
        group_rg = [(rg, [])]
        ug = MagicMock()
        group_ug = [(ug, users)]
        zammad_connector._fetch_groups = AsyncMock(return_value=(group_rg, group_ug))
        zammad_connector._sync_roles = AsyncMock()
        zammad_connector._sync_tickets_for_groups = AsyncMock()
        zammad_connector._sync_knowledge_bases = AsyncMock()

        with patch(
            "app.connectors.sources.zammad.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await zammad_connector.run_sync()

        zammad_connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        zammad_connector.data_entities_processor.on_new_user_groups.assert_awaited_once()
        zammad_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        zammad_connector._sync_roles.assert_awaited_once()
        zammad_connector._sync_tickets_for_groups.assert_awaited_once()
        zammad_connector._sync_knowledge_bases.assert_awaited_once()

    async def test_run_sync_initializes_if_no_client(self, zammad_connector):
        zammad_connector.external_client = None
        zammad_connector.init = AsyncMock(return_value=True)
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        zammad_connector._fetch_users = AsyncMock(return_value=([], {}))
        zammad_connector._fetch_groups = AsyncMock(return_value=([], []))
        zammad_connector._sync_roles = AsyncMock()
        zammad_connector._sync_tickets_for_groups = AsyncMock()
        zammad_connector._sync_knowledge_bases = AsyncMock()

        with patch(
            "app.connectors.sources.zammad.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await zammad_connector.run_sync()

        zammad_connector.init.assert_awaited_once()

    async def test_run_sync_raises_on_error(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        zammad_connector._get_fresh_datasource = AsyncMock(
            side_effect=Exception("connection failed")
        )

        with patch(
            "app.connectors.sources.zammad.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            with pytest.raises(Exception, match="connection failed"):
                await zammad_connector.run_sync()


class TestZammadFetchUsers:
    """Tests for _fetch_users pagination loop."""

    async def test_single_page(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "email": "alice@test.com", "firstname": "Alice",
                 "lastname": "A", "active": True, "role_ids": [1]},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, user_map = await zammad_connector._fetch_users()
        assert len(users) == 1
        assert "alice@test.com" in user_map

    async def test_skips_inactive_users(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "email": "inactive@test.com", "active": False},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, _ = await zammad_connector._fetch_users()
        assert len(users) == 0

    async def test_skips_system_users(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "email": "mailer-daemon@test.com", "active": True,
                 "firstname": "", "lastname": ""},
                {"id": 2, "email": "noreply@test.com", "active": True,
                 "firstname": "", "lastname": ""},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, _ = await zammad_connector._fetch_users()
        assert len(users) == 0

    async def test_pagination(self, zammad_connector):
        # IDs start at 1 to avoid falsy user_id=0 being skipped
        page1 = _make_response(
            success=True,
            data=[{"id": i + 1, "email": f"u{i + 1}@t.com", "active": True,
                   "firstname": f"U{i + 1}", "lastname": ""} for i in range(100)]
        )
        page2 = _make_response(
            success=True,
            data=[{"id": 101, "email": "last@t.com", "active": True,
                   "firstname": "Last", "lastname": ""}]
        )
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(side_effect=[page1, page2])
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, _ = await zammad_connector._fetch_users()
        assert len(users) == 101

    async def test_api_failure_returns_empty(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_users = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users, _ = await zammad_connector._fetch_users()
        assert users == []


class TestZammadSyncTicketsForGroups:
    """Tests for _sync_tickets_for_groups loop."""

    async def test_no_groups(self, zammad_connector):
        await zammad_connector._sync_tickets_for_groups([])
        zammad_connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_single_group_with_tickets(self, zammad_connector):
        rg = MagicMock()
        rg.external_group_id = "group_5"
        rg.name = "Support"

        ticket_record = MagicMock(spec=TicketRecord)
        ticket_record.source_updated_at = 1700000000000

        zammad_connector._get_group_sync_checkpoint = AsyncMock(return_value=None)
        zammad_connector._update_group_sync_checkpoint = AsyncMock()

        async def _batch_gen(group_id, group_name, last_sync_time):
            yield [(ticket_record, [])]

        zammad_connector._fetch_tickets_for_group_batch = _batch_gen

        await zammad_connector._sync_tickets_for_groups([(rg, [])])
        zammad_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_group_error_continues_to_next(self, zammad_connector):
        rg1 = MagicMock()
        rg1.external_group_id = "group_1"
        rg1.name = "Sales"
        rg2 = MagicMock()
        rg2.external_group_id = "group_2"
        rg2.name = "Support"

        zammad_connector._get_group_sync_checkpoint = AsyncMock(return_value=None)
        zammad_connector._update_group_sync_checkpoint = AsyncMock()

        call_count = 0

        async def _batch_gen(group_id, group_name, last_sync_time):
            nonlocal call_count
            call_count += 1
            if group_id == 1:
                raise Exception("API error")
            ticket = MagicMock(spec=TicketRecord)
            ticket.source_updated_at = 1700000000000
            yield [(ticket, [])]

        zammad_connector._fetch_tickets_for_group_batch = _batch_gen

        await zammad_connector._sync_tickets_for_groups([(rg1, []), (rg2, [])])
        zammad_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_group_missing_id_skipped(self, zammad_connector):
        rg = MagicMock()
        rg.external_group_id = None
        rg.name = "BadGroup"

        zammad_connector._get_group_sync_checkpoint = AsyncMock(return_value=None)

        async def _batch_gen(group_id, group_name, last_sync_time):
            yield []

        zammad_connector._fetch_tickets_for_group_batch = _batch_gen

        await zammad_connector._sync_tickets_for_groups([(rg, [])])
        zammad_connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_incremental_sync_uses_checkpoint(self, zammad_connector):
        rg = MagicMock()
        rg.external_group_id = "group_3"
        rg.name = "Dev"

        zammad_connector._get_group_sync_checkpoint = AsyncMock(return_value=1700000000000)
        zammad_connector._update_group_sync_checkpoint = AsyncMock()

        received_last_sync = None

        async def _batch_gen(group_id, group_name, last_sync_time):
            nonlocal received_last_sync
            received_last_sync = last_sync_time
            return
            yield  # make it an async generator

        zammad_connector._fetch_tickets_for_group_batch = _batch_gen

        await zammad_connector._sync_tickets_for_groups([(rg, [])])
        assert received_last_sync == 1700000000000


class TestZammadFetchTicketsForGroupBatch:
    """Tests for _fetch_tickets_for_group_batch pagination."""

    async def test_single_page_of_tickets(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector.sync_filters.get.return_value = None
        zammad_connector.indexing_filters = MagicMock()
        zammad_connector.indexing_filters.is_enabled.return_value = True

        ticket = MagicMock(spec=TicketRecord)
        zammad_connector._transform_ticket_to_ticket_record = AsyncMock(return_value=ticket)
        zammad_connector._fetch_ticket_attachments = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.search_tickets = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "title": "Bug", "group_id": 5}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        batches = []
        async for batch in zammad_connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        assert len(batches) == 1
        assert len(batches[0]) == 1

    async def test_empty_response_stops(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector.sync_filters.get.return_value = None

        mock_ds = MagicMock()
        mock_ds.search_tickets = AsyncMock(return_value=_make_response(success=True, data=[]))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        batches = []
        async for batch in zammad_connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        assert len(batches) == 0

    async def test_api_failure_stops(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector.sync_filters.get.return_value = None

        mock_ds = MagicMock()
        mock_ds.search_tickets = AsyncMock(return_value=_make_response(success=False, error="err"))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        batches = []
        async for batch in zammad_connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        assert len(batches) == 0

    async def test_ticket_transform_error_continues(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector.sync_filters.get.return_value = None
        zammad_connector.indexing_filters = MagicMock()
        zammad_connector.indexing_filters.is_enabled.return_value = True

        zammad_connector._transform_ticket_to_ticket_record = AsyncMock(
            side_effect=Exception("transform error")
        )

        mock_ds = MagicMock()
        mock_ds.search_tickets = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "title": "Bug"}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        batches = []
        async for batch in zammad_connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        # Should yield empty batch (error skipped, but remaining records still yielded)
        total = sum(len(b) for b in batches)
        assert total == 0

    async def test_indexing_disabled_sets_auto_index_off(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector.sync_filters.get.return_value = None
        zammad_connector.indexing_filters = MagicMock()
        zammad_connector.indexing_filters.is_enabled.return_value = False

        ticket = MagicMock(spec=TicketRecord)
        zammad_connector._transform_ticket_to_ticket_record = AsyncMock(return_value=ticket)
        zammad_connector._fetch_ticket_attachments = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.search_tickets = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "title": "Bug"}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch in zammad_connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            pass

        assert ticket.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestZammadFetchGroups:
    """Tests for _fetch_groups pagination and group creation."""

    async def test_single_group(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "name": "Support", "active": True,
                   "created_at": "2024-01-01T00:00:00Z",
                   "updated_at": "2024-06-01T00:00:00Z"}]
        ))
        mock_ds.get_group = AsyncMock(return_value=_make_response(
            success=True,
            data={"id": 1, "user_ids": []}
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record_groups, user_groups = await zammad_connector._fetch_groups({})
        assert len(record_groups) == 1
        assert len(user_groups) == 1

    async def test_skips_filtered_groups(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector._is_group_allowed_by_filter = MagicMock(return_value=False)

        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "name": "Excluded", "active": True}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record_groups, user_groups = await zammad_connector._fetch_groups({})
        assert len(record_groups) == 0

    async def test_skips_inactive_groups(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "name": "Dead", "active": False}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record_groups, _ = await zammad_connector._fetch_groups({})
        assert len(record_groups) == 0

    async def test_api_failure(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record_groups, user_groups = await zammad_connector._fetch_groups({})
        assert record_groups == []
        assert user_groups == []

    async def test_group_members_mapped(self, zammad_connector):
        zammad_connector.sync_filters = MagicMock()
        zammad_connector._is_group_allowed_by_filter = MagicMock(return_value=True)
        zammad_connector._user_id_to_data = {
            10: {"email": "bob@test.com", "role_ids": []}
        }

        mock_ds = MagicMock()
        mock_ds.list_groups = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "name": "Support", "active": True}]
        ))
        mock_ds.get_group = AsyncMock(return_value=_make_response(
            success=True,
            data={"id": 1, "user_ids": [10]}
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        bob_user = MagicMock(email="bob@test.com")
        user_map = {"bob@test.com": bob_user}

        _, user_groups = await zammad_connector._fetch_groups(user_map)
        assert len(user_groups) == 1
        _, members = user_groups[0]
        assert bob_user in members


class TestZammadSyncRoles:
    """Tests for _sync_roles loop."""

    async def test_sync_roles_single_page(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_roles = AsyncMock(return_value=_make_response(
            success=True,
            data=[
                {"id": 1, "name": "Admin", "active": True,
                 "created_at": "2024-01-01T00:00:00Z",
                 "updated_at": "2024-06-01T00:00:00Z"},
            ]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        zammad_connector._user_id_to_data = {}

        await zammad_connector._sync_roles([], {})
        zammad_connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    async def test_sync_roles_skips_inactive(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_roles = AsyncMock(return_value=_make_response(
            success=True,
            data=[{"id": 1, "name": "Old Role", "active": False}]
        ))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        zammad_connector._user_id_to_data = {}

        await zammad_connector._sync_roles([], {})
        # Should still be called even if no active roles found
        # (on_new_app_roles not called because no active roles)
        zammad_connector.data_entities_processor.on_new_app_roles.assert_not_called()

    async def test_sync_roles_api_failure_logs_warning(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_roles = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await zammad_connector._sync_roles([], {})
        # No roles found when API fails, no error raised
        zammad_connector.data_entities_processor.on_new_app_roles.assert_not_called()


class TestZammadSyncKnowledgeBases:
    """Tests for _sync_knowledge_bases."""

    async def test_no_kb_answers(self, zammad_connector):
        zammad_connector._get_kb_sync_checkpoint = AsyncMock(return_value=None)
        mock_ds = MagicMock()
        mock_ds.search_kb_answers = AsyncMock(return_value=_make_response(success=True, data=None))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await zammad_connector._sync_knowledge_bases()

    async def test_kb_incremental_builds_query(self, zammad_connector):
        zammad_connector._get_kb_sync_checkpoint = AsyncMock(return_value=1700000000000)
        mock_ds = MagicMock()
        mock_ds.search_kb_answers = AsyncMock(return_value=_make_response(success=False))
        zammad_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await zammad_connector._sync_knowledge_bases()
        call_kwargs = mock_ds.search_kb_answers.await_args
        assert "updated_at" in call_kwargs.kwargs.get("query", "")

# =============================================================================
# Merged from test_zammad_connector_full_coverage.py
# =============================================================================

@pytest.fixture()
def mock_logger_fullcov():
    return logging.getLogger("test.zammad.full")


@pytest.fixture()
def mock_data_entities_processor_fullcov():
    proc = MagicMock()
    proc.org_id = "org-zm-fc"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_new_app_roles = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider_fullcov():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": "https://zammad.example.com",
            "token": "test-zammad-token",
        },
    })
    return svc


@pytest.fixture()
def connector(mock_logger_fullcov, mock_data_entities_processor_fullcov,
              mock_data_store_provider_fullcov, mock_config_service):
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        c = ZammadConnector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_data_entities_processor_fullcov,
            data_store_provider=mock_data_store_provider_fullcov,
            config_service=mock_config_service,
            connector_id="zm-fc-1",
            scope="personal",
            created_by="test-user-id",
        )
    return c


def _resp(success=True, data=None, error=None, message=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    r.message = message
    return r


def _mock_ds():
    ds = MagicMock()
    ds.list_users = AsyncMock(return_value=_resp(success=False))
    ds.list_groups = AsyncMock(return_value=_resp(success=False))
    ds.list_roles = AsyncMock(return_value=_resp(success=False))
    ds.get_group = AsyncMock(return_value=_resp(success=False))
    ds.get_user = AsyncMock(return_value=_resp(success=False))
    ds.get_ticket = AsyncMock(return_value=_resp(success=False))
    ds.list_ticket_articles = AsyncMock(return_value=_resp(success=False))
    ds.list_links = AsyncMock(return_value=_resp(success=False))
    ds.search_tickets = AsyncMock(return_value=_resp(success=False))
    ds.search_kb_answers = AsyncMock(return_value=_resp(success=False))
    ds.get_kb_answer = AsyncMock(return_value=_resp(success=False))
    ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=False))
    ds.get_ticket_attachment = AsyncMock(return_value=_resp(success=False))
    ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
    ds.get_kb_category_permissions = AsyncMock(return_value=_resp(success=False))
    ds.list_ticket_states = AsyncMock(return_value=_resp(success=True, data=[]))
    ds.list_ticket_priorities = AsyncMock(return_value=_resp(success=True, data=[]))
    return ds


class TestModuleConstants:
    def test_config_path(self):
        assert "{connector_id}" in ZAMMAD_CONFIG_PATH

    def test_batch_size(self):
        assert BATCH_SIZE_KB_ANSWERS == 50

    def test_attachment_id_parts(self):
        assert ATTACHMENT_ID_PARTS_COUNT == 3

    def test_kb_answer_attachment_parts(self):
        assert KB_ANSWER_ATTACHMENT_PARTS_COUNT == 2

    def test_link_type_map_values(self):
        assert ZAMMAD_LINK_TYPE_MAP["normal"] == RecordRelations.RELATED
        assert ZAMMAD_LINK_TYPE_MAP["parent"] == RecordRelations.DEPENDS_ON
        assert ZAMMAD_LINK_TYPE_MAP["child"] == RecordRelations.LINKED_TO

    def test_link_object_map_values(self):
        assert ZAMMAD_LINK_OBJECT_MAP["Ticket"] == RecordType.TICKET
        assert ZAMMAD_LINK_OBJECT_MAP["KnowledgeBase::Answer::Translation"] == RecordType.WEBPAGE


class TestDetermineVisibility:
    def test_published(self, connector):
        assert connector._determine_visibility({"published_at": "2024-01-01"}) == "PUBLIC"

    def test_internal(self, connector):
        assert connector._determine_visibility({"internal_at": "2024-01-01"}) == "INTERNAL"

    def test_archived(self, connector):
        assert connector._determine_visibility({"archived_at": "2024-01-01"}) == "ARCHIVED"

    def test_draft(self, connector):
        assert connector._determine_visibility({}) == "DRAFT"

    def test_published_takes_precedence(self, connector):
        assert connector._determine_visibility({
            "published_at": "2024-01-01",
            "internal_at": "2024-02-01",
            "archived_at": "2024-03-01",
        }) == "PUBLIC"

    def test_internal_over_archived(self, connector):
        assert connector._determine_visibility({
            "internal_at": "2024-02-01",
            "archived_at": "2024-03-01",
        }) == "INTERNAL"


class TestCreateAnswerWithPermissions:
    def test_no_answer_id(self, connector):
        rec, perms = connector._create_answer_with_permissions(
            answer_data={}, category_id=1, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=None
        )
        assert rec is None
        assert perms == []

    def test_public_visibility(self, connector):
        connector.base_url = "https://z.example.com"
        rec, perms = connector._create_answer_with_permissions(
            answer_data={"id": 10, "translations": [], "created_at": "", "updated_at": ""},
            category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=None
        )
        assert rec is not None
        assert rec.inherit_permissions is False
        assert any(p.entity_type == EntityType.ORG for p in perms)

    def test_internal_visibility(self, connector):
        connector.base_url = "https://z.example.com"
        rec, perms = connector._create_answer_with_permissions(
            answer_data={"id": 11, "translations": [], "created_at": "", "updated_at": ""},
            category_id=5, visibility="INTERNAL",
            editor_role_ids=[1], category_map={}, existing_record=None
        )
        assert rec is not None
        assert rec.inherit_permissions is True
        assert perms == []

    def test_archived_visibility(self, connector):
        connector.base_url = "https://z.example.com"
        rec, perms = connector._create_answer_with_permissions(
            answer_data={"id": 12, "translations": [], "created_at": "", "updated_at": ""},
            category_id=5, visibility="ARCHIVED",
            editor_role_ids=[1, 2], category_map={}, existing_record=None
        )
        assert rec is not None
        assert rec.inherit_permissions is False
        assert len(perms) == 2
        assert all(p.entity_type == EntityType.ROLE for p in perms)
        assert all(p.type == PermissionType.WRITE for p in perms)

    def test_draft_visibility(self, connector):
        connector.base_url = "https://z.example.com"
        rec, perms = connector._create_answer_with_permissions(
            answer_data={"id": 13, "translations": [], "created_at": "", "updated_at": ""},
            category_id=5, visibility="DRAFT",
            editor_role_ids=[3], category_map={}, existing_record=None
        )
        assert rec.inherit_permissions is False
        assert len(perms) == 1
        assert perms[0].external_id == "3"

    def test_with_translations(self, connector):
        connector.base_url = "https://z.example.com"
        rec, _ = connector._create_answer_with_permissions(
            answer_data={
                "id": 14,
                "translations": [
                    {"title": "My KB Article", "content": {"body": "<p>Hello</p>"}, "locale": "de-de"},
                ],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-06-01T00:00:00Z",
            },
            category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=None
        )
        assert rec.record_name == "My KB Article"

    def test_with_existing_record_versioning(self, connector):
        connector.base_url = "https://z.example.com"
        existing = MagicMock()
        existing.id = "existing-id-1"
        existing.version = 3
        existing.source_updated_at = 1000

        rec, _ = connector._create_answer_with_permissions(
            answer_data={
                "id": 15,
                "translations": [],
                "created_at": "",
                "updated_at": "2024-06-01T00:00:00Z",
            },
            category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=existing
        )
        assert rec.id == "existing-id-1"
        assert rec.version == 4

    def test_category_map_resolves_kb_id(self, connector):
        connector.base_url = "https://z.example.com"
        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_2"
        category_map = {5: cat_rg}

        rec, _ = connector._create_answer_with_permissions(
            answer_data={"id": 16, "translations": [], "created_at": "", "updated_at": ""},
            category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map=category_map, existing_record=None
        )
        assert "knowledge_base/2" in rec.weburl

    def test_translation_content_body_fallback(self, connector):
        connector.base_url = "https://z.example.com"
        rec, _ = connector._create_answer_with_permissions(
            answer_data={
                "id": 17,
                "translations": [{"title": "Fallback", "content_body": "<p>body</p>"}],
                "created_at": "", "updated_at": "",
            },
            category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=None
        )
        assert rec.record_name == "Fallback"

    def test_translation_body_field_fallback(self, connector):
        connector.base_url = "https://z.example.com"
        rec, _ = connector._create_answer_with_permissions(
            answer_data={
                "id": 18,
                "translations": [{"title": "Body", "body": "<p>direct body</p>"}],
                "created_at": "", "updated_at": "",
            },
            category_id=5, visibility="PUBLIC",
            editor_role_ids=[], category_map={}, existing_record=None
        )
        assert rec.record_name == "Body"


class TestProcessKBEntitiesFromFirstPage:
    async def test_processes_kb_and_categories(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        ds.get_kb_category_permissions = AsyncMock(return_value=_resp(
            success=True,
            data={"permissions": [{"role_id": 1, "access": "editor"}, {"role_id": 2, "access": "reader"}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        assets = {
            "KnowledgeBase": {"1": {"translation_ids": [10]}},
            "KnowledgeBaseTranslation": {"10": {"title": "My KB", "locale": "en-us"}},
            "KnowledgeBaseCategory": {"5": {"knowledge_base_id": 1, "translation_ids": [20], "parent_id": None, "permissions_effective": []}},
            "KnowledgeBaseCategoryTranslation": {"20": {"title": "FAQ"}},
        }
        kb_map = {}
        category_map = {}
        cat_perms_map = {}

        await connector._process_kb_entities_from_first_page(assets, kb_map, category_map, cat_perms_map)

        assert 1 in kb_map
        assert kb_map[1].name == "My KB"
        assert 5 in category_map
        assert category_map[5].name == "FAQ"
        assert 5 in cat_perms_map
        assert 1 in cat_perms_map[5]["editor_role_ids"]
        assert 2 in cat_perms_map[5]["reader_role_ids"]
        connector.data_entities_processor.on_new_record_groups.assert_awaited()

    async def test_no_kb_assets(self, connector):
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        assets = {}
        kb_map = {}
        category_map = {}
        cat_perms_map = {}

        await connector._process_kb_entities_from_first_page(assets, kb_map, category_map, cat_perms_map)
        assert len(kb_map) == 0

    async def test_category_with_permissions_effective(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        assets = {
            "KnowledgeBase": {"1": {"translation_ids": []}},
            "KnowledgeBaseTranslation": {},
            "KnowledgeBaseCategory": {
                "5": {
                    "knowledge_base_id": 1,
                    "translation_ids": [20],
                    "parent_id": None,
                    "permissions_effective": [
                        {"role_id": 10, "access": "editor"},
                        {"role_id": 20, "access": "reader"},
                    ],
                }
            },
            "KnowledgeBaseCategoryTranslation": {"20": {"title": "Guides"}},
        }
        kb_map = {}
        category_map = {}
        cat_perms_map = {}

        await connector._process_kb_entities_from_first_page(assets, kb_map, category_map, cat_perms_map)
        assert cat_perms_map[5]["editor_role_ids"] == [10]
        assert cat_perms_map[5]["reader_role_ids"] == [20]

    async def test_category_with_parent(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        parent_rg = MagicMock()
        parent_rg.external_group_id = "cat_3"
        category_map = {3: parent_rg}

        assets = {
            "KnowledgeBase": {"1": {"translation_ids": []}},
            "KnowledgeBaseTranslation": {},
            "KnowledgeBaseCategory": {
                "7": {
                    "knowledge_base_id": 1,
                    "translation_ids": [],
                    "parent_id": 3,
                    "permissions_effective": [],
                }
            },
            "KnowledgeBaseCategoryTranslation": {},
        }
        kb_map = {}
        cat_perms_map = {}

        await connector._process_kb_entities_from_first_page(assets, kb_map, category_map, cat_perms_map)
        assert category_map[7].parent_external_group_id == "cat_3"

    async def test_category_permission_api_error(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        ds.get_kb_category_permissions = AsyncMock(side_effect=Exception("API down"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        assets = {
            "KnowledgeBase": {"1": {"translation_ids": []}},
            "KnowledgeBaseTranslation": {},
            "KnowledgeBaseCategory": {
                "5": {
                    "knowledge_base_id": 1,
                    "translation_ids": [],
                    "parent_id": None,
                    "permissions_effective": [],
                }
            },
            "KnowledgeBaseCategoryTranslation": {},
        }
        kb_map = {}
        category_map = {}
        cat_perms_map = {}

        await connector._process_kb_entities_from_first_page(assets, kb_map, category_map, cat_perms_map)
        assert cat_perms_map[5]["editor_role_ids"] == []


class TestSyncKBAnswersPaginated:
    async def test_single_page_with_answers(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.indexing_filters = None

        first_assets = {
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1,
                    "category_id": 5,
                    "translation_ids": [],
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-06-01T00:00:00Z",
                    "published_at": "2024-01-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }

        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_1"
        category_map = {5: cat_rg}
        cat_perms_map = {5: {"kb_id": 1, "editor_role_ids": [], "reader_role_ids": []}}

        total, max_ts = await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=1,
            category_map=category_map, category_permissions_map=cat_perms_map
        )

        assert total == 1
        assert max_ts > 0
        connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_pagination_continues(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector.indexing_filters = None

        page2_data = {
            "_result_count": 1,
            "KnowledgeBaseAnswer": {
                "2": {
                    "id": 2,
                    "category_id": 5,
                    "translation_ids": [],
                    "created_at": "",
                    "updated_at": "2024-07-01T00:00:00Z",
                    "published_at": "2024-07-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }
        ds.search_kb_answers = AsyncMock(return_value=_resp(success=True, data=page2_data))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        first_assets = {
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1, "category_id": 5, "translation_ids": [],
                    "created_at": "", "updated_at": "2024-06-01T00:00:00Z",
                    "published_at": "2024-06-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }

        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_1"
        category_map = {5: cat_rg}
        cat_perms_map = {5: {"kb_id": 1, "editor_role_ids": [], "reader_role_ids": []}}

        total, _ = await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=50,
            category_map=category_map, category_permissions_map=cat_perms_map
        )

        assert total == 2

    async def test_empty_answer_assets_stops(self, connector):
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        first_assets = {}
        total, max_ts = await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=0,
            category_map={}, category_permissions_map={}
        )
        assert total == 0
        assert max_ts == 0

    async def test_answer_processing_error_continues(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.indexing_filters = None

        first_assets = {
            "KnowledgeBaseAnswer": {
                "1": {"id": 1, "category_id": None, "translation_ids": [], "created_at": "", "updated_at": ""},
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }

        connector._create_answer_with_permissions = MagicMock(side_effect=Exception("bad"))

        total, _ = await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=1,
            category_map={}, category_permissions_map={}
        )
        assert total == 0

    async def test_permission_update_for_existing_record(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.indexing_filters = None

        existing = MagicMock()
        existing.id = "existing-1"
        existing.version = 1
        existing.source_updated_at = 1000
        existing.inherit_permissions = True
        existing.external_revision_id = "1000"

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        first_assets = {
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1, "category_id": 5, "translation_ids": [],
                    "created_at": "", "updated_at": "2024-06-01T00:00:00Z",
                    "published_at": "2024-01-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }

        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_1"
        category_map = {5: cat_rg}
        cat_perms_map = {5: {"kb_id": 1, "editor_role_ids": [], "reader_role_ids": []}}

        await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=1,
            category_map=category_map, category_permissions_map=cat_perms_map
        )

        connector.data_entities_processor.on_updated_record_permissions.assert_awaited()

    async def test_indexing_filter_disables_indexing(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_filters = MagicMock()
        mock_filters.is_enabled.return_value = False
        connector.indexing_filters = mock_filters

        first_assets = {
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1, "category_id": 5, "translation_ids": [],
                    "created_at": "", "updated_at": "2024-06-01T00:00:00Z",
                    "published_at": "2024-01-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }

        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_1"
        category_map = {5: cat_rg}
        cat_perms_map = {5: {"kb_id": 1, "editor_role_ids": [], "reader_role_ids": []}}

        await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=1,
            category_map=category_map, category_permissions_map=cat_perms_map
        )

        connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_answer_with_translations_and_content(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.indexing_filters = None

        first_assets = {
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1, "category_id": 5, "translation_ids": [100],
                    "created_at": "", "updated_at": "2024-06-01T00:00:00Z",
                    "published_at": "2024-06-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {
                "100": {"title": "Translated Title", "content_id": 200}
            },
            "KnowledgeBaseAnswerTranslationContent": {
                "200": {"body": "<p>Hello World</p>", "attachments": []}
            },
        }

        cat_rg = MagicMock()
        cat_rg.parent_external_group_id = "kb_1"
        category_map = {5: cat_rg}
        cat_perms_map = {5: {"kb_id": 1, "editor_role_ids": [], "reader_role_ids": []}}

        total, _ = await connector._sync_kb_answers_paginated(
            query="*", limit=50, start_offset=0,
            first_page_assets=first_assets, first_result_count=1,
            category_map=category_map, category_permissions_map=cat_perms_map
        )
        assert total == 1


class TestSyncKnowledgeBases:
    async def test_full_sync_flow(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector.indexing_filters = None

        search_data = {
            "_result_count": 1,
            "KnowledgeBase": {"1": {"translation_ids": []}},
            "KnowledgeBaseTranslation": {},
            "KnowledgeBaseCategory": {},
            "KnowledgeBaseCategoryTranslation": {},
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1, "category_id": None, "translation_ids": [],
                    "created_at": "", "updated_at": "2024-06-01T00:00:00Z",
                    "published_at": "2024-01-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }
        ds.search_kb_answers = AsyncMock(return_value=_resp(success=True, data=search_data))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._get_kb_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_kb_sync_checkpoint = AsyncMock()

        await connector._sync_knowledge_bases()

        connector._update_kb_sync_checkpoint.assert_awaited()

    async def test_incremental_sync(self, connector):
        ds = _mock_ds()
        ds.search_kb_answers = AsyncMock(return_value=_resp(success=True, data=None))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._get_kb_sync_checkpoint = AsyncMock(return_value=1700000000000)

        await connector._sync_knowledge_bases()

        call_kwargs = ds.search_kb_answers.await_args
        assert "updated_at" in call_kwargs.kwargs.get("query", call_kwargs.args[0] if call_kwargs.args else "")

    async def test_no_data_returns_early(self, connector):
        ds = _mock_ds()
        ds.search_kb_answers = AsyncMock(return_value=_resp(success=True, data=None))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._get_kb_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_kb_sync_checkpoint = AsyncMock()

        await connector._sync_knowledge_bases()
        connector._update_kb_sync_checkpoint.assert_not_awaited()

    async def test_checkpoint_updated_with_current_time(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        connector.indexing_filters = None

        search_data = {
            "_result_count": 1,
            "KnowledgeBase": {},
            "KnowledgeBaseTranslation": {},
            "KnowledgeBaseCategory": {},
            "KnowledgeBaseCategoryTranslation": {},
            "KnowledgeBaseAnswer": {
                "1": {
                    "id": 1, "category_id": None, "translation_ids": [],
                    "created_at": "", "updated_at": "",
                    "published_at": "2024-01-01",
                }
            },
            "KnowledgeBaseAnswerTranslation": {},
            "KnowledgeBaseAnswerTranslationContent": {},
        }
        ds.search_kb_answers = AsyncMock(return_value=_resp(success=True, data=search_data))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._get_kb_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_kb_sync_checkpoint = AsyncMock()

        await connector._sync_knowledge_bases()
        connector._update_kb_sync_checkpoint.assert_awaited()


class TestStreamRecord:
    async def test_stream_ticket(self, connector):
        connector._process_ticket_blockgroups_for_streaming = AsyncMock(return_value=b'{"blocks":[]}')
        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "42"

        resp = await connector.stream_record(record)
        assert resp.status_code == 200

    async def test_stream_webpage(self, connector):
        connector._process_kb_answer_blockgroups_for_streaming = AsyncMock(return_value=b'{"blocks":[]}')
        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "kb_answer_1"

        resp = await connector.stream_record(record)
        assert resp.status_code == 200

    async def test_stream_file(self, connector):
        connector._process_file_for_streaming = AsyncMock(return_value=b'content')
        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "42_1_99"

        resp = await connector.stream_record(record)
        assert resp.status_code == 200

    async def test_stream_unsupported_type_raises(self, connector):
        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.id = "test"

        with pytest.raises(ValueError, match="Unsupported"):
            await connector.stream_record(record)

    async def test_stream_error_reraises(self, connector):
        connector._process_ticket_blockgroups_for_streaming = AsyncMock(
            side_effect=Exception("stream fail")
        )
        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.id = "test"

        with pytest.raises(Exception, match="stream fail"):
            await connector.stream_record(record)


class TestProcessFileForStreaming:
    async def test_ticket_attachment(self, connector):
        ds = _mock_ds()
        ds.get_ticket_attachment = AsyncMock(return_value=_resp(success=True, data=b"pdf-data"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42_1_99"
        result = await connector._process_file_for_streaming(record)
        assert result == b"pdf-data"

    async def test_kb_answer_attachment(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=True, data=b"img-data"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5_attachment_10"
        result = await connector._process_file_for_streaming(record)
        assert result == b"img-data"

    async def test_ticket_attachment_string_content(self, connector):
        ds = _mock_ds()
        ds.get_ticket_attachment = AsyncMock(return_value=_resp(success=True, data="text-data"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42_1_99"
        result = await connector._process_file_for_streaming(record)
        assert result == b"text-data"

    async def test_ticket_attachment_other_content(self, connector):
        ds = _mock_ds()
        ds.get_ticket_attachment = AsyncMock(return_value=_resp(success=True, data=12345))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42_1_99"
        result = await connector._process_file_for_streaming(record)
        assert result == b"12345"

    async def test_kb_attachment_string_content(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=True, data="kb-text"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5_attachment_10"
        result = await connector._process_file_for_streaming(record)
        assert result == b"kb-text"

    async def test_kb_attachment_other_content(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=True, data=99))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5_attachment_10"
        result = await connector._process_file_for_streaming(record)
        assert result == b"99"

    async def test_invalid_ticket_attachment_format(self, connector):
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "invalid_format"
        with pytest.raises(ValueError, match="Invalid attachment ID format"):
            await connector._process_file_for_streaming(record)

    async def test_invalid_kb_attachment_format(self, connector):
        ds = _mock_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5_attachment_10_attachment_extra"
        with pytest.raises(ValueError, match="Invalid KB answer attachment"):
            await connector._process_file_for_streaming(record)

    async def test_ticket_attachment_api_failure(self, connector):
        ds = _mock_ds()
        ds.get_ticket_attachment = AsyncMock(return_value=_resp(success=False, message="not found"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42_1_99"
        with pytest.raises(Exception, match="Failed to download attachment"):
            await connector._process_file_for_streaming(record)

    async def test_kb_attachment_api_failure(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=False, message="err"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5_attachment_10"
        with pytest.raises(Exception, match="Failed to download KB"):
            await connector._process_file_for_streaming(record)


class TestConvertHtmlImagesToBase64:
    async def test_no_images(self, connector):
        html = "<p>Hello world</p>"
        result = await connector._convert_html_images_to_base64(html)
        assert "Hello world" in result

    async def test_empty_html(self, connector):
        result = await connector._convert_html_images_to_base64("")
        assert result == ""

    async def test_non_zammad_image(self, connector):
        html = '<img src="https://other.com/image.png"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image" not in result

    async def test_zammad_image_converted(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'\x89PNG\r\nfakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<p>Text <img src="/api/v1/attachments/9"/> more</p>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/png;base64," in result

    async def test_jpeg_detection(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'\xff\xd8\xfffakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/10"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/jpeg;base64," in result

    async def test_gif_detection(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'GIF89afakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/11"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/gif;base64," in result

    async def test_svg_detection(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/12"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/svg+xml;base64," in result

    async def test_url_extension_jpg(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'fakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/13?file=photo.jpg"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/jpeg;base64," in result

    async def test_url_extension_webp(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'fakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/14?file=photo.webp"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/webp;base64," in result

    async def test_url_extension_gif(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'fakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/15?file=anim.gif"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/gif;base64," in result

    async def test_url_extension_svg(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'fakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/16?file=icon.svg"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/svg+xml;base64," in result

    async def test_url_extension_png_explicit(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'fakedata'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/17?file=img.png"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/png;base64," in result

    async def test_download_failure_skips(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=False, message="err"))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/20"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image" not in result

    async def test_empty_content_skips(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=True, data=b''))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/21"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image" not in result

    async def test_string_data_converted(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data="<svg></svg>"
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/22"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image" in result

    async def test_exception_during_download_skips(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(side_effect=Exception("network error"))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/23"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image" not in result

    async def test_img_without_src_skips(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())

        html = '<img alt="no source"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image" not in result

    async def test_init_called_when_no_datasource(self, connector):
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)

        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(success=True, data=b'\x89PNGdata'))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        def set_data_source():
            connector.data_source = ds

        connector.init.side_effect = lambda: set_data_source() or True

        html = '<img src="/api/v1/attachments/25"/>'
        await connector._convert_html_images_to_base64(html)
        connector.init.assert_awaited_once()

    async def test_xml_svg_detection(self, connector):
        ds = _mock_ds()
        ds.get_kb_answer_attachment = AsyncMock(return_value=_resp(
            success=True, data=b'<?xml version="1.0"?><svg></svg>'
        ))
        connector.data_source = ds
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        html = '<img src="/api/v1/attachments/26"/>'
        result = await connector._convert_html_images_to_base64(html)
        assert "data:image/svg+xml;base64," in result


class TestHandleWebhookNotification:
    async def test_noop(self, connector):
        await connector.handle_webhook_notification({"event": "ticket.updated"})


class TestReindexRecords:
    async def test_empty_records(self, connector):
        await connector.reindex_records([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_updated_records(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())

        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        record.record_type = RecordType.TICKET
        record.external_record_id = "42"

        updated_ticket = MagicMock(spec=TicketRecord)
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(updated_ticket, [])
        )

        await connector.reindex_records([record])
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_non_updated_records_reindex(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())

        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        record.__class__.__name__ = "TicketRecord"
        record.record_type = RecordType.TICKET

        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    async def test_base_record_class_skipped(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())

        record = Record(
            id="r1",
            org_id="org-1",
            record_type=RecordType.TICKET,
            record_name="test",
            connector_name=Connectors.ZAMMAD,
            connector_id="zm-fc-1",
            external_record_id="42",
            version=1,
            origin=OriginTypes.CONNECTOR,
        )

        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    async def test_check_error_continues(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())

        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=Exception("check failed")
        )

        await connector.reindex_records([record])

    async def test_init_called_when_no_datasource(self, connector):
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        type(record).__name__ = "TicketRecord"

        await connector.reindex_records([record])
        connector.init.assert_awaited_once()

    async def test_reindex_raises_on_error(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("boom"))

        with pytest.raises(Exception, match="boom"):
            await connector.reindex_records([MagicMock()])

    async def test_not_implemented_reindex(self, connector):
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(return_value=_mock_ds())

        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        record.__class__.__name__ = "TicketRecord"
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("to_kafka_record not implemented")
        )

        await connector.reindex_records([record])


class TestCheckAndFetchUpdatedRecord:
    async def test_ticket_record(self, connector):
        record = MagicMock()
        record.record_type = RecordType.TICKET
        connector._check_and_fetch_updated_ticket = AsyncMock(return_value=("ticket", []))

        result = await connector._check_and_fetch_updated_record(record)
        assert result == ("ticket", [])

    async def test_webpage_record(self, connector):
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        connector._check_and_fetch_updated_kb_answer = AsyncMock(return_value=("answer", []))

        result = await connector._check_and_fetch_updated_record(record)
        assert result == ("answer", [])

    async def test_file_record(self, connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    async def test_unknown_record_type(self, connector):
        record = MagicMock()
        record.record_type = "UNKNOWN"
        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    async def test_exception_returns_none(self, connector):
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.id = "r1"
        connector._check_and_fetch_updated_ticket = AsyncMock(
            side_effect=Exception("error")
        )
        result = await connector._check_and_fetch_updated_record(record)
        assert result is None


class TestCheckAndFetchUpdatedTicket:
    async def test_ticket_updated(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {}
        connector._priority_map = {}
        connector._user_id_to_data = {}

        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True,
            data={
                "id": 42, "title": "Updated", "group_id": 1,
                "updated_at": "2024-07-01T00:00:00Z",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ))
        ds.list_links = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42"
        record.source_updated_at = 1000

        result = await connector._check_and_fetch_updated_ticket(record)
        assert result is not None
        assert result[0].record_name == "Updated"

    async def test_ticket_not_changed(self, connector):
        ds = _mock_ds()
        ts = 1719792000000
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 42, "title": "Same", "updated_at": "2024-07-01T00:00:00Z"}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42"
        record.source_updated_at = ts

        result = await connector._check_and_fetch_updated_ticket(record)
        assert result is None

    async def test_ticket_not_found(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(success=False, message="not found"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42"
        result = await connector._check_and_fetch_updated_ticket(record)
        assert result is None

    async def test_ticket_no_data(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(success=True, data=None))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42"
        result = await connector._check_and_fetch_updated_ticket(record)
        assert result is None

    async def test_ticket_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "42"
        result = await connector._check_and_fetch_updated_ticket(record)
        assert result is None


class TestCheckAndFetchUpdatedKBAnswer:
    async def test_answer_updated(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(
            success=True, data={"KnowledgeBase": {"1": {}}}
        ))
        ds.get_kb_answer = AsyncMock(return_value=_resp(
            success=True,
            data={
                "assets": {
                    "KnowledgeBaseAnswer": {
                        "5": {
                            "id": 5, "category_id": 10,
                            "translation_ids": [],
                            "created_at": "",
                            "updated_at": "2024-07-01T00:00:00Z",
                            "published_at": "2024-01-01",
                        }
                    },
                    "KnowledgeBaseAnswerTranslation": {},
                    "KnowledgeBaseAnswerTranslationContent": {},
                }
            }
        ))
        ds.get_kb_category_permissions = AsyncMock(return_value=_resp(
            success=True,
            data={"permissions": [{"role_id": 1, "access": "editor"}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "kb_answer_5"
        record.source_updated_at = 1000
        record.external_record_group_id = "cat_10"
        record.record_group_type = RecordGroupType.KB

        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is not None

    async def test_answer_not_changed(self, connector):
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ts = 1719792000000
        ds.get_kb_answer = AsyncMock(return_value=_resp(
            success=True,
            data={
                "assets": {
                    "KnowledgeBaseAnswer": {"5": {"id": 5, "updated_at": "2024-07-01T00:00:00Z"}},
                    "KnowledgeBaseAnswerTranslation": {},
                    "KnowledgeBaseAnswerTranslationContent": {},
                }
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5"
        record.source_updated_at = ts
        record.external_record_group_id = None

        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is None

    async def test_answer_invalid_format(self, connector):
        record = MagicMock()
        record.external_record_id = "invalid_format"
        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is None

    async def test_answer_not_found(self, connector):
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ds.get_kb_answer = AsyncMock(return_value=_resp(success=False, message="not found"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5"
        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is None

    async def test_answer_no_data(self, connector):
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ds.get_kb_answer = AsyncMock(return_value=_resp(success=True, data=None))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5"
        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is None

    async def test_answer_direct_data_structure(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ds.get_kb_answer = AsyncMock(return_value=_resp(
            success=True,
            data={
                "id": 5, "category_id": 10,
                "translation_ids": [],
                "created_at": "",
                "updated_at": "2024-07-01T00:00:00Z",
                "published_at": "2024-01-01",
            }
        ))
        ds.get_kb_category_permissions = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "kb_answer_5"
        record.source_updated_at = 1000
        record.external_record_group_id = None

        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is not None

    async def test_answer_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "kb_answer_5"
        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is None

    async def test_answer_with_translations_enrichment(self, connector):
        connector.base_url = "https://z.example.com"
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ds.get_kb_answer = AsyncMock(return_value=_resp(
            success=True,
            data={
                "assets": {
                    "KnowledgeBaseAnswer": {
                        "5": {
                            "id": 5, "category_id": 10,
                            "translation_ids": [100],
                            "created_at": "",
                            "updated_at": "2024-07-01T00:00:00Z",
                            "published_at": "2024-01-01",
                        }
                    },
                    "KnowledgeBaseAnswerTranslation": {
                        "100": {"title": "My Article", "content_id": 200}
                    },
                    "KnowledgeBaseAnswerTranslationContent": {
                        "200": {"body": "<p>content</p>", "attachments": [{"id": 1}]}
                    },
                }
            }
        ))
        ds.get_kb_category_permissions = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "kb_answer_5"
        record.source_updated_at = 1000
        record.external_record_group_id = "cat_10"

        mock_tx = MagicMock()
        mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        result = await connector._check_and_fetch_updated_kb_answer(record)
        assert result is not None


class TestGetFilterOptions:
    async def test_group_ids_filter(self, connector):
        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "name": "Support", "active": True},
                {"id": 2, "name": "Sales", "active": True},
                {"id": 3, "name": "Inactive", "active": False},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.get_filter_options("group_ids")
        assert result.success is True
        assert len(result.options) == 2

    async def test_group_ids_with_search(self, connector):
        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "name": "Support", "active": True},
                {"id": 2, "name": "Sales", "active": True},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.get_filter_options("group_ids", search="sup")
        assert len(result.options) == 1
        assert result.options[0].label == "Support"

    async def test_group_ids_pagination(self, connector):
        ds = _mock_ds()
        groups = [{"id": i, "name": f"Group {i}", "active": True} for i in range(1, 6)]
        ds.list_groups = AsyncMock(return_value=_resp(success=True, data=groups))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.get_filter_options("group_ids", page=1, limit=2)
        assert len(result.options) == 2
        assert result.has_more is True

        result2 = await connector.get_filter_options("group_ids", page=3, limit=2)
        assert len(result2.options) == 1
        assert result2.has_more is False

    async def test_unknown_filter_key(self, connector):
        result = await connector.get_filter_options("unknown_key")
        assert result.success is True
        assert len(result.options) == 0

    async def test_group_ids_api_failure(self, connector):
        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.get_filter_options("group_ids")
        assert result.success is True
        assert len(result.options) == 0

    async def test_group_ids_multi_page_fetch(self, connector):
        ds = _mock_ds()
        page1 = [{"id": i, "name": f"G{i}", "active": True} for i in range(1, 101)]
        page2 = [{"id": 101, "name": "G101", "active": True}]
        ds.list_groups = AsyncMock(side_effect=[
            _resp(success=True, data=page1),
            _resp(success=True, data=page2),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.get_filter_options("group_ids", page=1, limit=200)
        assert len(result.options) == 101


class TestTestConnectionAndAccess:
    async def test_success(self, connector):
        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(success=True, data=[]))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.test_connection_and_access()
        assert result is True

    async def test_failure(self, connector):
        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(success=False, message="unauthorized"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector.test_connection_and_access()
        assert result is False

    async def test_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("network"))
        result = await connector.test_connection_and_access()
        assert result is False


class TestGetSignedUrl:
    async def test_returns_empty(self, connector):
        record = MagicMock()
        result = await connector.get_signed_url(record)
        assert result == ""


class TestRunIncrementalSync:
    async def test_calls_run_sync(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


class TestCleanup:
    async def test_closes_client(self, connector):
        internal = AsyncMock()
        internal.close = AsyncMock()
        mock_client = MagicMock()
        mock_client.get_client.return_value = internal
        connector.external_client = mock_client

        await connector.cleanup()
        internal.close.assert_awaited_once()

    async def test_no_client(self, connector):
        connector.external_client = None
        await connector.cleanup()

    async def test_close_error_swallowed(self, connector):
        internal = MagicMock()
        internal.close = AsyncMock(side_effect=Exception("already closed"))
        mock_client = MagicMock()
        mock_client.get_client.return_value = internal
        connector.external_client = mock_client

        await connector.cleanup()

    async def test_cleanup_overall_error(self, connector):
        connector.external_client = MagicMock()
        connector.external_client.get_client.side_effect = Exception("bad")
        await connector.cleanup()


class TestProcessTicketBlockgroupsForStreaming:
    async def test_ticket_with_articles(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True,
            data={"title": "Bug Report", "number": "1001"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "body": "<p>Description</p>", "from": "Alice", "subject": "", "sender": "Customer", "preferences": {}, "attachments": [], "created_at": "2024-01-01T00:00:00Z"},
                {"id": 2, "body": "<p>Comment</p>", "from": "Bob", "subject": "Re: Bug", "sender": "Agent", "preferences": {}, "attachments": [], "created_at": "2024-01-02T00:00:00Z"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "42"
        record.weburl = "https://z.example.com/#ticket/zoom/42"

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"Bug Report" in result

    async def test_ticket_no_articles(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True,
            data={"title": "Empty Ticket", "number": "1002"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(success=True, data=[]))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "43"
        record.weburl = None

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"Empty Ticket" in result

    async def test_ticket_fetch_failure(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "44"

        with pytest.raises(Exception, match="Failed to fetch ticket"):
            await connector._process_ticket_blockgroups_for_streaming(record)

    async def test_ticket_skips_system_articles(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True,
            data={"title": "Ticket", "number": "1003"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "body": "<p>First</p>", "from": "Alice", "sender": "Customer", "preferences": {}, "attachments": [], "created_at": "2024-01-01T00:00:00Z"},
                {"id": 2, "body": "<p>System msg</p>", "from": "System", "sender": "System", "preferences": {}, "attachments": [], "created_at": "2024-01-02T00:00:00Z"},
                {"id": 3, "body": "<p>Auto reply</p>", "from": "noreply", "sender": "Agent", "preferences": {"is-auto-response": True}, "attachments": [], "created_at": "2024-01-03T00:00:00Z"},
                {"id": 4, "body": "<p>Bounce</p>", "from": "MAILER-DAEMON@test.com", "sender": "Agent", "preferences": {}, "attachments": [], "created_at": "2024-01-04T00:00:00Z"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "45"
        record.weburl = None

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"System msg" not in result
        assert b"Auto reply" not in result
        assert b"Bounce" not in result

    async def test_ticket_skips_empty_body_articles(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True, data={"title": "T", "number": "1004"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "body": "<p>Desc</p>", "from": "", "sender": "Customer", "preferences": {}, "attachments": [], "created_at": "2024-01-01T00:00:00Z"},
                {"id": 2, "body": "", "from": "Bob", "sender": "Agent", "preferences": {}, "attachments": [], "created_at": "2024-01-02T00:00:00Z"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "46"
        record.weburl = None

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"block_groups" in result

    async def test_ticket_skips_article_without_id(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True, data={"title": "T", "number": "1005"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "body": "<p>Desc</p>", "from": "", "sender": "Customer", "preferences": {}, "attachments": [], "created_at": "2024-01-01T00:00:00Z"},
                {"body": "<p>No ID</p>", "from": "Bob", "sender": "Agent", "preferences": {}, "attachments": [], "created_at": "2024-01-02T00:00:00Z"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "47"
        record.weburl = None

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"block_groups" in result

    async def test_ticket_send_auto_response_false_skipped(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True, data={"title": "T", "number": "1006"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "body": "<p>Desc</p>", "from": "", "sender": "Customer", "preferences": {}, "attachments": [], "created_at": "2024-01-01T00:00:00Z"},
                {"id": 2, "body": "<p>Auto</p>", "from": "noreply", "sender": "Agent", "preferences": {"send-auto-response": False}, "attachments": [], "created_at": "2024-01-02T00:00:00Z"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "48"
        record.weburl = None

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"Auto" not in result

    async def test_mail_delivery_system_skipped(self, connector):
        ds = _mock_ds()
        ds.get_ticket = AsyncMock(return_value=_resp(
            success=True, data={"title": "T", "number": "1007"}
        ))
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "body": "<p>Desc</p>", "from": "", "sender": "Customer", "preferences": {}, "attachments": [], "created_at": "2024-01-01T00:00:00Z"},
                {"id": 2, "body": "<p>Bounce</p>", "from": "Mail Delivery System <bounce@t.com>", "sender": "Agent", "preferences": {}, "attachments": [], "created_at": "2024-01-02T00:00:00Z"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "49"
        record.weburl = None

        result = await connector._process_ticket_blockgroups_for_streaming(record)
        assert b"Bounce" not in result


class TestProcessKBAnswerBlockgroupsForStreaming:
    async def test_kb_answer_with_content(self, connector):
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(
            success=True, data={"KnowledgeBase": {"1": {}}}
        ))
        ds.get_kb_answer = AsyncMock(return_value=_resp(
            success=True,
            data={
                "assets": {
                    "KnowledgeBaseAnswer": {
                        "5": {
                            "id": 5, "translation_ids": [100],
                            "attachments": [],
                        }
                    },
                    "KnowledgeBaseAnswerTranslation": {
                        "100": {"title": "FAQ Answer", "content_id": 200}
                    },
                    "KnowledgeBaseAnswerTranslationContent": {
                        "200": {"body": "<p>Answer content</p>"}
                    },
                }
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5"
        record.record_name = "FAQ Answer"
        record.weburl = "https://z.example.com/#knowledge_base/1/locale/en-us/answer/5"
        record.inherit_permissions = False

        result = await connector._process_kb_answer_blockgroups_for_streaming(record)
        assert b"FAQ Answer" in result

    async def test_kb_answer_no_content(self, connector):
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ds.get_kb_answer = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5"
        record.record_name = "Empty Answer"
        record.weburl = None
        record.inherit_permissions = False

        result = await connector._process_kb_answer_blockgroups_for_streaming(record)
        assert b"Empty Answer" in result

    async def test_kb_answer_direct_data(self, connector):
        ds = _mock_ds()
        ds.init_knowledge_base = AsyncMock(return_value=_resp(success=False))
        ds.get_kb_answer = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 5, "translations": [{"title": "Direct", "body": "<p>body</p>"}], "attachments": []}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "kb_answer_5"
        record.record_name = "Direct"
        record.weburl = None
        record.inherit_permissions = False

        result = await connector._process_kb_answer_blockgroups_for_streaming(record)
        assert b"Direct" in result


class TestFetchKBAnswerAttachments:
    async def test_with_attachments(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "p1"
        parent.external_record_id = "kb_answer_5"
        parent.external_record_group_id = "cat_10"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0
        parent.inherit_permissions = False

        answer_data = {
            "id": 5,
            "attachments": [
                {"id": 1, "filename": "doc.pdf", "size": 1024, "preferences": {"Content-Type": "application/pdf"}},
            ],
            "translations": [],
        }

        result = await connector._fetch_kb_answer_attachments(answer_data, parent, [])
        assert len(result) == 1

    async def test_no_attachments(self, connector):
        parent = MagicMock(spec=WebpageRecord)
        parent.id = "p1"
        parent.external_record_id = "kb_answer_5"

        result = await connector._fetch_kb_answer_attachments({"id": 5, "translations": []}, parent, [])
        assert result == []

    async def test_no_answer_id(self, connector):
        result = await connector._fetch_kb_answer_attachments({}, MagicMock(), [])
        assert result == []

    async def test_translation_attachments(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "p1"
        parent.external_record_id = "kb_answer_5"
        parent.external_record_group_id = "cat_10"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0
        parent.inherit_permissions = True

        answer_data = {
            "id": 5,
            "attachments": [],
            "translations": [
                {"attachments": [{"id": 2, "filename": "img.png", "size": 512, "preferences": {"Content-Type": "image/png"}}]},
            ],
        }

        result = await connector._fetch_kb_answer_attachments(answer_data, parent, [])
        assert len(result) == 1

    async def test_attachment_processing_error(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "p1"
        parent.external_record_id = "kb_answer_5"
        parent.external_record_group_id = "cat_10"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0
        parent.inherit_permissions = False

        connector._transform_attachment_to_file_record = AsyncMock(side_effect=Exception("bad"))

        answer_data = {
            "id": 5,
            "attachments": [{"id": 1, "filename": "bad.pdf", "size": 0, "preferences": {}}],
            "translations": [],
        }

        result = await connector._fetch_kb_answer_attachments(answer_data, parent, [])
        assert result == []


class TestBuildTicketAttachmentChildRecords:
    async def test_existing_record_found(self, connector):
        existing = MagicMock()
        existing.id = "existing-att-1"
        existing.record_name = "file.pdf"

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        parent = MagicMock()
        attachments = [{"id": 99, "filename": "file.pdf"}]

        result = await connector._build_ticket_attachment_child_records("42", "1", attachments, parent)
        assert len(result) == 1
        assert result[0].child_id == "existing-att-1"

    async def test_creates_new_record(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        attachments = [{"id": 99, "filename": "new.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}]

        result = await connector._build_ticket_attachment_child_records("42", "1", attachments, parent)
        assert len(result) == 1
        connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_skips_attachment_without_id(self, connector):
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        parent = MagicMock()
        attachments = [{"filename": "no_id.pdf"}]

        result = await connector._build_ticket_attachment_child_records("42", "1", attachments, parent)
        assert len(result) == 0

    async def test_creation_failure(self, connector):
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._transform_attachment_to_file_record = AsyncMock(side_effect=Exception("bad"))

        parent = MagicMock()
        attachments = [{"id": 99, "filename": "bad.pdf"}]

        result = await connector._build_ticket_attachment_child_records("42", "1", attachments, parent)
        assert len(result) == 0


class TestBuildKBAnswerChildRecords:
    async def test_existing_record(self, connector):
        existing = MagicMock()
        existing.id = "existing-kb-att"
        existing.record_name = "kb-file.pdf"

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = MagicMock()
        record.inherit_permissions = False

        result = await connector._build_kb_answer_child_records(
            answer_id=5, answer_data={"published_at": "2024-01-01"},
            answer_attachments=[{"id": 10, "filename": "kb-file.pdf"}],
            record=record, kb_id=1
        )
        assert len(result) == 1
        assert result[0].child_id == "existing-kb-att"

    async def test_creates_new_record_public(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = MagicMock(spec=WebpageRecord)
        record.id = "p1"
        record.external_record_id = "kb_answer_5"
        record.external_record_group_id = "cat_10"
        record.record_group_type = RecordGroupType.KB
        record.weburl = None
        record.source_created_at = 0
        record.source_updated_at = 0
        record.inherit_permissions = False

        result = await connector._build_kb_answer_child_records(
            answer_id=5, answer_data={"published_at": "2024-01-01"},
            answer_attachments=[{"id": 10, "filename": "new.pdf", "size": 200, "preferences": {"Content-Type": "application/pdf"}}],
            record=record, kb_id=1
        )
        assert len(result) == 1
        connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_skips_attachment_without_id(self, connector):
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = MagicMock()
        record.inherit_permissions = False

        result = await connector._build_kb_answer_child_records(
            answer_id=5, answer_data={},
            answer_attachments=[{"filename": "no_id.pdf"}],
            record=record, kb_id=1
        )
        assert len(result) == 0

    async def test_creation_failure(self, connector):
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._transform_attachment_to_file_record = AsyncMock(side_effect=Exception("fail"))

        record = MagicMock()
        record.inherit_permissions = False

        result = await connector._build_kb_answer_child_records(
            answer_id=5, answer_data={"published_at": "2024-01-01"},
            answer_attachments=[{"id": 10, "filename": "bad.pdf"}],
            record=record, kb_id=1
        )
        assert len(result) == 0

    async def test_archived_visibility_with_permissions(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        ds = _mock_ds()
        ds.get_kb_category_permissions = AsyncMock(return_value=_resp(
            success=True,
            data={"permissions": [{"role_id": 99, "access": "editor"}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = MagicMock(spec=WebpageRecord)
        record.id = "p1"
        record.external_record_id = "kb_answer_5"
        record.external_record_group_id = "cat_10"
        record.record_group_type = RecordGroupType.KB
        record.weburl = None
        record.source_created_at = 0
        record.source_updated_at = 0
        record.inherit_permissions = False

        result = await connector._build_kb_answer_child_records(
            answer_id=5,
            answer_data={"archived_at": "2024-01-01", "category_id": 10},
            answer_attachments=[{"id": 10, "filename": "file.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}],
            record=record, kb_id=1
        )
        assert len(result) == 1


class TestTransformAttachmentEdgeCases:
    async def test_cat_prefix_group_type(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "p1"
        parent.external_record_id = "kb_answer_5"
        parent.external_record_group_id = "cat_10"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        att_data = {"id": 1, "filename": "test.txt", "size": 100, "preferences": {"Content-Type": "text/plain"}}
        result = await connector._transform_attachment_to_file_record(
            attachment_data=att_data,
            external_record_id="kb_answer_5_attachment_1",
            parent_record=parent,
            parent_record_type=RecordType.WEBPAGE,
            indexing_filter_key=MagicMock(),
        )
        assert result.record_group_type == RecordGroupType.KB

    async def test_no_extension(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        att_data = {"id": 1, "filename": "noext", "size": 0, "preferences": {}}
        result = await connector._transform_attachment_to_file_record(
            attachment_data=att_data,
            external_record_id="42_1_1",
            parent_record=parent,
            parent_record_type=RecordType.TICKET,
            indexing_filter_key=MagicMock(),
        )
        assert result.extension is None

    async def test_existing_record_versioning(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        existing = MagicMock()
        existing.id = "existing-att"
        existing.version = 5

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        att_data = {"id": 1, "filename": "file.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}
        result = await connector._transform_attachment_to_file_record(
            attachment_data=att_data,
            external_record_id="42_1_1",
            parent_record=parent,
            parent_record_type=RecordType.TICKET,
            indexing_filter_key=MagicMock(),
        )
        assert result.id == "existing-att"
        assert result.version == 5

    async def test_inherit_permissions_false(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=WebpageRecord)
        parent.id = "p1"
        parent.external_record_id = "kb_answer_5"
        parent.external_record_group_id = "cat_10"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        att_data = {"id": 1, "filename": "file.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}
        result = await connector._transform_attachment_to_file_record(
            attachment_data=att_data,
            external_record_id="kb_answer_5_attachment_1",
            parent_record=parent,
            parent_record_type=RecordType.WEBPAGE,
            indexing_filter_key=MagicMock(),
            inherit_permissions=False,
        )
        assert result.inherit_permissions is False

    async def test_parent_record_group_type_fallback(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock()
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "other_10"
        parent.record_group_type = RecordGroupType.KB
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        att_data = {"id": 1, "filename": "file.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}
        result = await connector._transform_attachment_to_file_record(
            attachment_data=att_data,
            external_record_id="42_1_1",
            parent_record=parent,
            parent_record_type=RecordType.TICKET,
            indexing_filter_key=MagicMock(),
        )
        assert result.record_group_type == RecordGroupType.KB

    async def test_indexing_filter_disabled(self, connector):
        mock_filters = MagicMock()
        mock_filters.is_enabled.return_value = False
        connector.indexing_filters = mock_filters

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        att_data = {"id": 1, "filename": "file.pdf", "size": 100, "preferences": {"Content-Type": "application/pdf"}}
        result = await connector._transform_attachment_to_file_record(
            attachment_data=att_data,
            external_record_id="42_1_1",
            parent_record=parent,
            parent_record_type=RecordType.TICKET,
            indexing_filter_key=MagicMock(),
        )
        assert result.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestGetFreshDatasourceUnsupportedAuth:
    async def test_unsupported_auth_type(self, connector):
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH2", "token": "tok"}
        })
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await connector._get_fresh_datasource()


class TestFetchTicketAttachmentsAutoResponse:
    async def test_skips_auto_response_articles(self, connector):
        ds = _mock_ds()
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "sender": "Agent", "from": "agent@test.com", "preferences": {"is-auto-response": True}, "attachments": [{"id": 10}]},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"

        result = await connector._fetch_ticket_attachments({"id": 42}, parent)
        assert len(result) == 0

    async def test_skips_send_auto_response_false(self, connector):
        ds = _mock_ds()
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "sender": "Agent", "from": "agent@test.com", "preferences": {"send-auto-response": False}, "attachments": [{"id": 10}]},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"

        result = await connector._fetch_ticket_attachments({"id": 42}, parent)
        assert len(result) == 0

    async def test_api_failure_returns_empty(self, connector):
        ds = _mock_ds()
        ds.list_ticket_articles = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"

        result = await connector._fetch_ticket_attachments({"id": 42}, parent)
        assert result == []

    async def test_skips_attachment_without_id(self, connector):
        ds = _mock_ds()
        ds.list_ticket_articles = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "sender": "Customer", "from": "user@test.com", "preferences": {}, "attachments": [{"filename": "no_id.pdf"}]},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        from app.connectors.core.registry.filters import FilterCollection
        connector.indexing_filters = FilterCollection()

        parent = MagicMock(spec=TicketRecord)
        parent.id = "p1"
        parent.external_record_id = "42"
        parent.external_record_group_id = "group_10"
        parent.record_group_type = RecordGroupType.PROJECT
        parent.weburl = ""
        parent.source_created_at = 0
        parent.source_updated_at = 0

        result = await connector._fetch_ticket_attachments({"id": 42}, parent)
        assert len(result) == 0


class TestFetchTicketLinksEdgeCases:
    async def test_kb_translation_fallback(self, connector):
        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "knowledgebase::answer::Translation", "link_object_value": 50},
                ],
                "assets": {"KnowledgeBaseAnswerTranslation": {}},
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_ticket_links(1)
        assert len(result) == 1
        assert result[0].record_type == RecordType.WEBPAGE

    async def test_unknown_link_object_skipped(self, connector):
        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "UnknownObject", "link_object_value": 50},
                ],
                "assets": {},
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_ticket_links(1)
        assert len(result) == 0

    async def test_no_link_object_value_skipped(self, connector):
        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "Ticket", "link_object_value": None},
                ],
                "assets": {},
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_ticket_links(1)
        assert len(result) == 0

    async def test_kb_answer_without_answer_id_fallback(self, connector):
        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "KnowledgeBase::Answer::Translation", "link_object_value": 99},
                ],
                "assets": {"KnowledgeBaseAnswerTranslation": {"99": {}}},
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_ticket_links(1)
        assert len(result) == 1
        assert result[0].external_record_id == "kb_answer_99"

    async def test_exception_returns_empty(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("network"))

        result = await connector._fetch_ticket_links(1)
        assert result == []

    async def test_normal_link_non_integer_value(self, connector):
        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(
            success=True,
            data={
                "links": [
                    {"link_type": "normal", "link_object": "Ticket", "link_object_value": "abc"},
                ],
                "assets": {},
            }
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_ticket_links(1)
        assert len(result) == 1


class TestFetchUsersEdgeCases:
    async def test_role_id_string_conversion(self, connector):
        ds = _mock_ds()
        ds.list_users = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "email": "user@test.com", "active": True, "firstname": "U", "lastname": "", "role_ids": ["1", "abc", 3]},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users, _ = await connector._fetch_users()
        assert len(users) == 1
        assert 1 in connector._user_id_to_data[1]["role_ids"]
        assert 3 in connector._user_id_to_data[1]["role_ids"]

    async def test_no_reply_email_skipped(self, connector):
        ds = _mock_ds()
        ds.list_users = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "email": "no-reply@test.com", "active": True, "firstname": "No", "lastname": "Reply"},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users, _ = await connector._fetch_users()
        assert len(users) == 0

    async def test_mail_delivery_system_skipped(self, connector):
        ds = _mock_ds()
        ds.list_users = AsyncMock(return_value=_resp(
            success=True,
            data=[
                {"id": 1, "email": "postmaster@test.com", "active": True, "firstname": "Mail Delivery System", "lastname": ""},
            ]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users, _ = await connector._fetch_users()
        assert len(users) == 0

    async def test_non_list_data_handled(self, connector):
        ds = _mock_ds()
        ds.list_users = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 1, "email": "single@test.com", "active": True, "firstname": "Single", "lastname": ""}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users, _ = await connector._fetch_users()
        assert len(users) == 1

    async def test_user_no_id_skipped(self, connector):
        ds = _mock_ds()
        ds.list_users = AsyncMock(return_value=_resp(
            success=True,
            data=[{"email": "noid@test.com", "active": True, "firstname": "No", "lastname": "ID"}]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users, _ = await connector._fetch_users()
        assert len(users) == 0


class TestFetchGroupsEdgeCases:
    async def test_member_string_id_conversion(self, connector):
        connector.sync_filters = MagicMock()
        connector._is_group_allowed_by_filter = MagicMock(return_value=True)
        connector._user_id_to_data = {10: {"email": "bob@test.com", "role_ids": []}}

        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data=[{"id": 1, "name": "Support", "active": True}]
        ))
        ds.get_group = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 1, "user_ids": ["10"]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        bob_user = MagicMock(email="bob@test.com")
        user_map = {"bob@test.com": bob_user}

        _, user_groups = await connector._fetch_groups(user_map)
        _, members = user_groups[0]
        assert bob_user in members

    async def test_get_group_api_failure(self, connector):
        connector.sync_filters = MagicMock()
        connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data=[{"id": 1, "name": "Support", "active": True}]
        ))
        ds.get_group = AsyncMock(side_effect=Exception("API error"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record_groups, user_groups = await connector._fetch_groups({})
        assert len(record_groups) == 1
        assert len(user_groups) == 1
        _, members = user_groups[0]
        assert len(members) == 0

    async def test_non_list_groups_data(self, connector):
        connector.sync_filters = MagicMock()
        connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 1, "name": "Single", "active": True}
        ))
        ds.get_group = AsyncMock(return_value=_resp(success=True, data={"user_ids": []}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record_groups, _ = await connector._fetch_groups({})
        assert len(record_groups) == 1

    async def test_group_no_id_skipped(self, connector):
        connector.sync_filters = MagicMock()
        connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data=[{"name": "No ID", "active": True}]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record_groups, _ = await connector._fetch_groups({})
        assert len(record_groups) == 0

    async def test_group_no_name_skipped(self, connector):
        connector.sync_filters = MagicMock()
        connector._is_group_allowed_by_filter = MagicMock(return_value=True)

        ds = _mock_ds()
        ds.list_groups = AsyncMock(return_value=_resp(
            success=True,
            data=[{"id": 1, "name": "", "active": True}]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        record_groups, _ = await connector._fetch_groups({})
        assert len(record_groups) == 0


class TestSyncRolesEdgeCases:
    async def test_no_role_id_skipped(self, connector):
        ds = _mock_ds()
        ds.list_roles = AsyncMock(return_value=_resp(
            success=True, data=[{"name": "NoID", "active": True}]
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._user_id_to_data = {}

        await connector._sync_roles([], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_called()

    async def test_roles_pagination(self, connector):
        ds = _mock_ds()
        page1 = [{"id": i, "name": f"Role{i}", "active": True, "created_at": "", "updated_at": ""} for i in range(1, 101)]
        page2 = [{"id": 101, "name": "Role101", "active": True, "created_at": "", "updated_at": ""}]
        ds.list_roles = AsyncMock(side_effect=[
            _resp(success=True, data=page1),
            _resp(success=True, data=page2),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._user_id_to_data = {}

        await connector._sync_roles([], {})
        args = connector.data_entities_processor.on_new_app_roles.call_args[0][0]
        assert len(args) == 101

    async def test_roles_exception_raises(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))

        with pytest.raises(Exception, match="fail"):
            await connector._sync_roles([], {})

    async def test_non_list_roles_data(self, connector):
        ds = _mock_ds()
        ds.list_roles = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 1, "name": "Single", "active": True, "created_at": "", "updated_at": ""}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._user_id_to_data = {}

        await connector._sync_roles([], {})
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    async def test_no_data_returns(self, connector):
        ds = _mock_ds()
        ds.list_roles = AsyncMock(return_value=_resp(success=True, data=None))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_roles([], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_called()


class TestTransformTicketEdgeCases:
    async def test_state_not_status_enum(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {1: "unknown_state"}
        connector._priority_map = {}
        connector._user_id_to_data = {}

        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._transform_ticket_to_ticket_record({
            "id": 1, "title": "Test", "state_id": 1
        })
        assert result is not None

    async def test_priority_not_priority_enum(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {}
        connector._priority_map = {1: "unknown_priority"}
        connector._user_id_to_data = {}

        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._transform_ticket_to_ticket_record({
            "id": 1, "title": "Test", "priority_id": 1
        })
        assert result is not None

    async def test_existing_record_same_updated_at(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {}
        connector._priority_map = {}
        connector._user_id_to_data = {}

        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 5
        existing.source_updated_at = 1719792000000

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        ds = _mock_ds()
        ds.list_links = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._transform_ticket_to_ticket_record({
            "id": 1, "title": "Test",
            "updated_at": "2024-07-01T00:00:00Z",
        })
        assert result.version == 5

    async def test_user_lookup_failure(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {}
        connector._priority_map = {}
        connector._user_id_to_data = {100: {"email": "user@test.com", "role_ids": []}}

        ds = _mock_ds()
        ds.get_user = AsyncMock(side_effect=Exception("user lookup fail"))
        ds.list_links = AsyncMock(return_value=_resp(success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._transform_ticket_to_ticket_record({
            "id": 1, "title": "Test", "customer_id": 100
        })
        assert result is not None
        assert result.creator_email == "user@test.com"

    async def test_link_fetch_failure(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {}
        connector._priority_map = {}
        connector._user_id_to_data = {}

        ds = _mock_ds()
        ds.list_links = AsyncMock(side_effect=Exception("links fail"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._transform_ticket_to_ticket_record({
            "id": 1, "title": "Test"
        })
        assert result is not None

    async def test_datasource_failure_for_user(self, connector):
        connector.base_url = "https://z.example.com"
        connector._state_map = {}
        connector._priority_map = {}
        connector._user_id_to_data = {100: {"email": "user@test.com", "role_ids": []}}

        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("ds fail"))

        result = await connector._transform_ticket_to_ticket_record({
            "id": 1, "title": "Test", "customer_id": 100
        })
        assert result is not None


class TestGroupFilterEdgeCases:
    def test_operator_without_value_attr(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["1"]
        mock_filter.get_operator.return_value = "in"

        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        connector.sync_filters = mock_filters

        assert connector._is_group_allowed_by_filter("1") is True

    def test_none_operator(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["1"]
        mock_filter.get_operator.return_value = None

        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        connector.sync_filters = mock_filters

        assert connector._is_group_allowed_by_filter("1") is True


class TestCreateConnector:
    @patch("app.connectors.sources.zammad.connector.DataSourceEntitiesProcessor")
    @patch("app.connectors.sources.zammad.connector.ZammadApp")
    async def test_factory_method(self, mock_app, mock_dep, mock_data_store_provider_fullcov, mock_config_service):
        mock_proc = MagicMock()
        mock_proc.org_id = "org-1"
        mock_proc.initialize = AsyncMock()
        mock_dep.return_value = mock_proc

        logger = logging.getLogger("test")
        result = await ZammadConnector.create_connector(
            logger=logger,
            data_store_provider=mock_data_store_provider_fullcov,
            config_service=mock_config_service,
            connector_id="zm-test"
        )
        assert isinstance(result, ZammadConnector)
        mock_proc.initialize.assert_awaited_once()


class TestFetchTicketsForGroupBatchDateFilters:
    async def test_with_modified_filter(self, connector):
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_value.return_value = (1700000000000, 1710000000000)

        mock_created_filter = MagicMock()
        mock_created_filter.get_value.return_value = (1690000000000, None)

        mock_sync_filters = MagicMock()

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.MODIFIED:
                return mock_modified_filter
            elif key == SyncFilterKey.CREATED:
                return mock_created_filter
            return None

        mock_sync_filters.get = MagicMock(side_effect=get_filter)
        connector.sync_filters = mock_sync_filters
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        ds = _mock_ds()
        ds.search_tickets = AsyncMock(return_value=_resp(success=True, data=[]))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = []
        async for batch in connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        call_kwargs = ds.search_tickets.await_args.kwargs
        assert "updated_at" in call_kwargs["query"]
        assert "created_at" in call_kwargs["query"]

    async def test_incremental_sync_max_filter(self, connector):
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_value.return_value = (1600000000000, None)

        mock_sync_filters = MagicMock()

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.MODIFIED:
                return mock_modified_filter
            return None

        mock_sync_filters.get = MagicMock(side_effect=get_filter)
        connector.sync_filters = mock_sync_filters

        ds = _mock_ds()
        ds.search_tickets = AsyncMock(return_value=_resp(success=True, data=[]))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = []
        async for batch in connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=1700000000000
        ):
            batches.append(batch)

        call_kwargs = ds.search_tickets.await_args.kwargs
        assert "updated_at" in call_kwargs["query"]

    async def test_non_list_tickets_data(self, connector):
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        ticket = MagicMock(spec=TicketRecord)
        connector._transform_ticket_to_ticket_record = AsyncMock(return_value=ticket)
        connector._fetch_ticket_attachments = AsyncMock(return_value=[])

        ds = _mock_ds()
        ds.search_tickets = AsyncMock(return_value=_resp(
            success=True,
            data={"id": 1, "title": "Single"}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = []
        async for batch in connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        assert len(batches) == 1

    async def test_null_data_returns_empty(self, connector):
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None

        ds = _mock_ds()
        ds.search_tickets = AsyncMock(return_value=_resp(success=True, data=None))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = []
        async for batch in connector._fetch_tickets_for_group_batch(
            group_id=5, group_name="Support", last_sync_time=None
        ):
            batches.append(batch)

        assert len(batches) == 0


class TestLoadLookupTablesEdgeCases:
    async def test_priority_error_graceful(self, connector):
        ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = []
        ds.list_ticket_states = AsyncMock(return_value=states_resp)
        ds.list_ticket_priorities = AsyncMock(side_effect=Exception("prio fail"))
        connector.data_source = ds

        await connector._load_lookup_tables()
        assert connector._priority_map == {}

    async def test_state_failure_response(self, connector):
        ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = False
        ds.list_ticket_states = AsyncMock(return_value=states_resp)

        prio_resp = MagicMock()
        prio_resp.success = True
        prio_resp.data = [{"id": 1, "name": "normal"}]
        ds.list_ticket_priorities = AsyncMock(return_value=prio_resp)
        connector.data_source = ds

        await connector._load_lookup_tables()
        assert connector._state_map == {}
        assert connector._priority_map == {1: "normal"}

    async def test_skips_entries_without_id(self, connector):
        ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"name": "open"}, {"id": None, "name": "closed"}]
        ds.list_ticket_states = AsyncMock(return_value=states_resp)

        prio_resp = MagicMock()
        prio_resp.success = True
        prio_resp.data = [{"name": "normal"}]
        ds.list_ticket_priorities = AsyncMock(return_value=prio_resp)
        connector.data_source = ds

        await connector._load_lookup_tables()
        assert connector._state_map == {}
        assert connector._priority_map == {}


class TestSyncTicketsForGroupsCheckpoint:
    async def test_no_tickets_keeps_checkpoint(self, connector):
        rg = MagicMock()
        rg.external_group_id = "group_5"
        rg.name = "Support"

        connector._get_group_sync_checkpoint = AsyncMock(return_value=1000)
        connector._update_group_sync_checkpoint = AsyncMock()

        async def _empty_gen(group_id, group_name, last_sync_time):
            return
            yield

        connector._fetch_tickets_for_group_batch = _empty_gen

        await connector._sync_tickets_for_groups([(rg, [])])
        connector._update_group_sync_checkpoint.assert_not_awaited()

    async def test_tickets_without_updated_at(self, connector):
        rg = MagicMock()
        rg.external_group_id = "group_5"
        rg.name = "Support"

        ticket = MagicMock(spec=TicketRecord)
        ticket.source_updated_at = None

        connector._get_group_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_group_sync_checkpoint = AsyncMock()

        async def _gen(group_id, group_name, last_sync_time):
            yield [(ticket, [])]

        connector._fetch_tickets_for_group_batch = _gen

        await connector._sync_tickets_for_groups([(rg, [])])
        connector._update_group_sync_checkpoint.assert_awaited()

    async def test_file_record_counted_as_attachment(self, connector):
        rg = MagicMock()
        rg.external_group_id = "group_5"
        rg.name = "Support"

        ticket = MagicMock(spec=TicketRecord)
        ticket.source_updated_at = 1700000000000
        file_rec = MagicMock(spec=FileRecord)

        connector._get_group_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_group_sync_checkpoint = AsyncMock()

        async def _gen(group_id, group_name, last_sync_time):
            yield [(ticket, []), (file_rec, [])]

        connector._fetch_tickets_for_group_batch = _gen

        await connector._sync_tickets_for_groups([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited()
