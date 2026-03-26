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
