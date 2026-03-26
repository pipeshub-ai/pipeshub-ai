"""Tests for the Linear connector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    LinearConnector,
)
from app.models.entities import (
    AppUser,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a LinearConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_store_provider = MagicMock()
    # data_store_provider.transaction returns an async context manager
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())
    config_service = AsyncMock()
    connector_id = "linear-conn-1"
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
    )
    return connector


def _mock_linear_org():
    """Build a mock organization response."""
    response = MagicMock()
    response.success = True
    response.data = {
        "organization": {
            "id": "org-linear-1",
            "name": "Test Org",
            "urlKey": "test-org",
        }
    }
    return response


def _mock_users_response(users_list, has_next=False, end_cursor=None):
    """Build a mock users response."""
    response = MagicMock()
    response.success = True
    response.data = {
        "users": {
            "nodes": users_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_teams_response(teams_list, has_next=False, end_cursor=None):
    """Build a mock teams response."""
    response = MagicMock()
    response.success = True
    response.data = {
        "teams": {
            "nodes": teams_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_issues_response(issues_list, has_next=False, end_cursor=None):
    """Build a mock issues response."""
    response = MagicMock()
    response.success = True
    response.data = {
        "issues": {
            "nodes": issues_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


# ===================================================================
# LinearConnector - Initialization
# ===================================================================

class TestLinearConnectorInit:
    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        mock_client = AsyncMock()
        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(return_value=_mock_linear_org())

        with patch(
            "app.connectors.sources.linear.connector.LinearClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch(
                "app.connectors.sources.linear.connector.LinearDataSource"
            ) as MockDS:
                MockDS.return_value = mock_ds
                result = await connector.init()
                assert result is True
                assert connector.organization_name == "Test Org"
                assert connector.organization_url_key == "test-org"

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector = _make_connector()
        with patch(
            "app.connectors.sources.linear.connector.LinearClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(
                side_effect=Exception("Auth failed")
            )
            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_init_org_fetch_fails(self):
        connector = _make_connector()
        mock_client = AsyncMock()
        mock_ds = MagicMock()
        failed_response = MagicMock()
        failed_response.success = False
        failed_response.message = "Unauthorized"
        mock_ds.organization = AsyncMock(return_value=failed_response)

        with patch(
            "app.connectors.sources.linear.connector.LinearClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch(
                "app.connectors.sources.linear.connector.LinearDataSource"
            ) as MockDS:
                MockDS.return_value = mock_ds
                result = await connector.init()
                assert result is False


# ===================================================================
# LinearConnector - Token refresh
# ===================================================================

class TestLinearConnectorTokenRefresh:
    @pytest.mark.asyncio
    async def test_get_fresh_datasource_no_client(self):
        connector = _make_connector()
        connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_no_config(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_oauth_token_update(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "new-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_called_once_with("new-token")


# ===================================================================
# LinearConnector - Filter Options
# ===================================================================

class TestLinearConnectorFilterOptions:
    @pytest.mark.asyncio
    async def test_get_filter_options_unknown_key(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="Unknown filter field"):
            await connector.get_filter_options("unknown_key")

    @pytest.mark.asyncio
    async def test_get_team_options_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_teams_response(
                [
                    {"id": "team-1", "name": "Engineering", "key": "ENG"},
                    {"id": "team-2", "name": "Design", "key": "DES"},
                ],
                has_next=False,
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.get_filter_options("team_ids")
            assert len(result.options) == 2
            assert result.options[0].label == "Engineering (ENG)"
            assert result.has_more is False


# ===================================================================
# LinearConnector - User Fetching
# ===================================================================

class TestLinearConnectorUsers:
    @pytest.mark.asyncio
    async def test_fetch_users_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(
            return_value=_mock_users_response(
                [
                    {"id": "u1", "email": "a@test.com", "name": "Alice", "active": True},
                    {"id": "u2", "email": "b@test.com", "name": "Bob", "active": True},
                    {"id": "u3", "email": None, "name": "NoEmail", "active": True},
                    {"id": "u4", "email": "d@test.com", "name": "Deactivated", "active": False},
                ],
                has_next=False,
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            users = await connector._fetch_users()
            assert len(users) == 2  # Only active users with email
            assert users[0].email == "a@test.com"

    @pytest.mark.asyncio
    async def test_fetch_users_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        page1 = _mock_users_response(
            [{"id": "u1", "email": "a@test.com", "name": "Alice", "active": True}],
            has_next=True,
            end_cursor="cursor-1",
        )
        page2 = _mock_users_response(
            [{"id": "u2", "email": "b@test.com", "name": "Bob", "active": True}],
            has_next=False,
        )
        mock_ds.users = AsyncMock(side_effect=[page1, page2])

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            users = await connector._fetch_users()
            assert len(users) == 2


# ===================================================================
# LinearConnector - Team Fetching
# ===================================================================

class TestLinearConnectorTeams:
    @pytest.mark.asyncio
    async def test_fetch_teams_basic(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_teams_response(
                [
                    {
                        "id": "team-1",
                        "name": "Engineering",
                        "key": "ENG",
                        "description": "Eng team",
                        "private": False,
                        "parent": None,
                        "members": {"nodes": []},
                    }
                ],
                has_next=False,
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            user_groups, record_groups = await connector._fetch_teams()
            assert len(user_groups) == 1
            assert len(record_groups) == 1
            # Public team -> org-level permissions
            _, perms = record_groups[0]
            assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_fetch_teams_private(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_teams_response(
                [
                    {
                        "id": "team-priv",
                        "name": "Secret",
                        "key": "SEC",
                        "description": "Private team",
                        "private": True,
                        "parent": None,
                        "members": {"nodes": [{"email": "a@test.com"}]},
                    }
                ],
                has_next=False,
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            user_groups, record_groups = await connector._fetch_teams()
            _, perms = record_groups[0]
            assert perms[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_fetch_teams_with_parent(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_teams_response(
                [
                    {
                        "id": "team-child",
                        "name": "Frontend",
                        "key": "FE",
                        "description": "FE team",
                        "private": False,
                        "parent": {"id": "team-parent", "name": "Engineering"},
                        "members": {"nodes": []},
                    }
                ],
                has_next=False,
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            _, record_groups = await connector._fetch_teams()
            rg, _ = record_groups[0]
            assert rg.parent_external_group_id == "team-parent"


# ===================================================================
# LinearConnector - Sync orchestration
# ===================================================================

class TestLinearConnectorSync:
    @pytest.mark.asyncio
    async def test_run_sync_no_active_users(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={})

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                from app.connectors.core.registry.filters import FilterCollection
                mock_load.return_value = (FilterCollection(), FilterCollection())
                connector.data_entities_processor.get_all_active_users = AsyncMock(
                    return_value=[]
                )
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_sync_issues_for_teams_empty(self):
        connector = _make_connector()
        await connector._sync_issues_for_teams([])
        # Should not raise, just log

    @pytest.mark.asyncio
    async def test_sync_attachments_empty(self):
        connector = _make_connector()
        await connector._sync_attachments([])


# ===================================================================
# LinearConnector - Date parsing
# ===================================================================

class TestLinearConnectorDateParsing:
    def test_parse_linear_datetime_valid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("2024-01-15T10:30:00.000Z")
        assert result is not None
        assert isinstance(result, int)
        assert result > 0

    def test_parse_linear_datetime_none(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime(None)
        assert result is None

    def test_parse_linear_datetime_empty(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("")
        assert result is None

    def test_parse_linear_datetime_invalid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("not-a-date")
        assert result is None


# ===================================================================
# LinearConnector - Config path
# ===================================================================

class TestLinearConnectorConfig:
    def test_config_path_template(self):
        assert "{connector_id}" in LINEAR_CONFIG_PATH
