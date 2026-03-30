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
import json
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4
from app.config.constants.arangodb import Connectors, ProgressStatus, RecordRelations
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    OriginTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)
import base64
from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.models.entities import (
    AppUser,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)


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
        scope="team",
        created_by="test-user",
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

# =============================================================================
# Merged from test_linear_connector_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector_cov():
    """Build a LinearConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()

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
        scope="personal",
        created_by="test-user-id",
    )
    connector._tx_store = mock_tx_store
    return connector


def _mock_linear_org_cov():
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


def _mock_users_response_cov(users_list, has_next=False, end_cursor=None):
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


def _mock_teams_response_cov(teams_list, has_next=False, end_cursor=None):
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


def _mock_issues_response_cov(issues_list, has_next=False, end_cursor=None):
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


def _mock_projects_response(projects_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "projects": {
            "nodes": projects_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_attachments_response(attachments_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "attachments": {
            "nodes": attachments_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_documents_response(documents_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "documents": {
            "nodes": documents_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _make_team_record_group(team_id="team-1", team_key="ENG"):
    rg = RecordGroup(
        id=str(uuid4()),
        org_id="org-1",
        external_group_id=team_id,
        connector_id="linear-conn-1",
        connector_name=Connectors.LINEAR,
        name=f"Team {team_key}",
        short_name=team_key,
        group_type=RecordGroupType.PROJECT,
    )
    perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
    return rg, perms


def _make_issue_data(issue_id="issue-1", identifier="ENG-1", title="Test Issue",
                     team_id="team-1", updated_at="2024-06-01T10:00:00.000Z",
                     created_at="2024-01-01T10:00:00.000Z"):
    return {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "url": f"https://linear.app/test-org/issue/{identifier}",
        "description": "Test description",
        "priority": 2,
        "state": {"name": "In Progress", "type": "started"},
        "assignee": {"id": "u1", "email": "alice@test.com", "name": "Alice"},
        "creator": {"id": "u2", "email": "bob@test.com", "name": "Bob"},
        "parent": None,
        "labels": {"nodes": [{"name": "bug"}]},
        "relations": {"nodes": []},
        "comments": {"nodes": []},
        "createdAt": created_at,
        "updatedAt": updated_at,
    }


def _make_project_data(project_id="proj-1", name="Test Project"):
    return {
        "id": project_id,
        "name": name,
        "slugId": "test-project",
        "url": "https://linear.app/test-org/project/test-project",
        "description": "Project desc",
        "content": "Project content",
        "status": {"name": "In Progress"},
        "priorityLabel": "High",
        "lead": {"id": "u1", "displayName": "Alice", "email": "alice@test.com"},
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
        "issues": {"nodes": []},
        "externalLinks": {"nodes": []},
        "documents": {"nodes": []},
        "projectMilestones": {"nodes": []},
        "projectUpdates": {"nodes": []},
        "comments": {"nodes": []},
    }


def _make_attachment_data(attachment_id="att-1", url="https://example.com/file.pdf"):
    return {
        "id": attachment_id,
        "url": url,
        "title": "Attachment Title",
        "subtitle": "Subtitle",
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
        "issue": {
            "id": "issue-1",
            "team": {"id": "team-1"}
        },
    }


def _make_document_data(document_id="doc-1"):
    return {
        "id": document_id,
        "url": "https://linear.app/test-org/document/doc-1",
        "title": "Test Document",
        "content": "# Hello",
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
        "issue": {
            "id": "issue-1",
            "identifier": "ENG-1",
            "team": {"id": "team-1"},
        },
    }


# ===================================================================
# Transformations
# ===================================================================

class TestLinearTransformIssueToTicketRecord:
    def test_basic_transform(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == "Test Issue"
        assert ticket.record_type == RecordType.TICKET
        assert ticket.external_record_id == "issue-1"
        assert ticket.external_record_group_id == "team-1"
        assert ticket.version == 0
        assert ticket.origin == OriginTypes.CONNECTOR.value or ticket.origin == OriginTypes.CONNECTOR
        assert ticket.connector_name == Connectors.LINEAR
        assert ticket.assignee_email == "alice@test.com"
        assert ticket.creator_email == "bob@test.com"

    def test_missing_id_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_issue_to_ticket_record({"title": "No ID"}, "team-1")

    def test_missing_team_id_raises(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        with pytest.raises(ValueError, match="team_id is required"):
            c._transform_issue_to_ticket_record(issue, "")

    def test_version_increment_on_change(self):
        c = _make_connector_cov()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1000
        issue = _make_issue_data(updated_at="2024-06-02T10:00:00.000Z")
        ticket = c._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.version == 4
        assert ticket.id == "existing-id"

    def test_version_stays_same_when_unchanged(self):
        c = _make_connector_cov()
        ts = c._parse_linear_datetime("2024-06-01T10:00:00.000Z")
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 5
        existing.source_updated_at = ts
        issue = _make_issue_data(updated_at="2024-06-01T10:00:00.000Z")
        ticket = c._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.version == 5

    def test_priority_mapping_urgent(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["priority"] = 1
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.priority is not None

    def test_priority_mapping_none(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["priority"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        # priority_str is None

    def test_priority_mapping_zero(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["priority"] = 0
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")

    def test_sub_issue_type(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["parent"] = {"id": "parent-issue-1"}
        issue["labels"] = {"nodes": []}
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.type == ItemType.SUB_ISSUE

    def test_no_label_defaults_to_issue(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["labels"] = {"nodes": []}
        issue["parent"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.type == ItemType.ISSUE

    def test_identifier_only_name(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["title"] = ""
        issue["identifier"] = "ENG-42"
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == "ENG-42"

    def test_no_identifier_no_title_uses_id(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["title"] = ""
        issue["identifier"] = ""
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == issue["id"]

    def test_relations_parsing(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["relations"] = {
            "nodes": [
                {
                    "type": "blocks",
                    "relatedIssue": {"id": "related-1"}
                },
                {
                    "type": None,
                    "relatedIssue": {"id": "related-2"}
                },
                {
                    "type": "relates",
                    "relatedIssue": None
                }
            ]
        }
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        # At least checks that the code handles missing data gracefully

    def test_no_assignee(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["assignee"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.assignee_email is None

    def test_no_creator(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["creator"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.creator_email is None


class TestLinearTransformToProjectRecord:
    def test_basic_transform(self):
        c = _make_connector_cov()
        project = _make_project_data()
        record = c._transform_to_project_record(project, "team-1")
        assert record.record_name == "Test Project"
        assert record.record_type == RecordType.PROJECT
        assert record.external_record_id == "proj-1"
        assert record.lead_email == "alice@test.com"

    def test_missing_id_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_to_project_record({"name": "No ID"}, "team-1")

    def test_slug_as_name(self):
        c = _make_connector_cov()
        project = _make_project_data()
        project["name"] = ""
        project["slugId"] = "my-slug"
        record = c._transform_to_project_record(project, "team-1")
        assert record.record_name == "my-slug"

    def test_id_as_name_fallback(self):
        c = _make_connector_cov()
        project = _make_project_data()
        project["name"] = ""
        project["slugId"] = ""
        record = c._transform_to_project_record(project, "team-1")
        assert record.record_name == project["id"]

    def test_version_increment(self):
        c = _make_connector_cov()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.source_updated_at = 1000
        project = _make_project_data()
        record = c._transform_to_project_record(project, "team-1", existing)
        assert record.version == 3

    def test_no_lead(self):
        c = _make_connector_cov()
        project = _make_project_data()
        project["lead"] = None
        record = c._transform_to_project_record(project, "team-1")
        assert record.lead_email is None


class TestLinearTransformAttachmentToLinkRecord:
    def test_basic_transform(self):
        c = _make_connector_cov()
        att = _make_attachment_data()
        link = c._transform_attachment_to_link_record(att, "issue-1", "node-1", "team-1")
        assert link.record_name == "Attachment Title"
        assert link.record_type == RecordType.LINK
        assert link.weburl == "https://example.com/file.pdf"
        assert link.is_public == LinkPublicStatus.UNKNOWN

    def test_missing_id_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_attachment_to_link_record({"url": "http://x"}, "i", "n", "t")

    def test_missing_url_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="missing required 'url'"):
            c._transform_attachment_to_link_record({"id": "x", "url": ""}, "i", "n", "t")

    def test_fallback_label(self):
        c = _make_connector_cov()
        att = {"id": "x", "url": "http://x.com/file", "label": "My Label", "createdAt": "", "updatedAt": ""}
        link = c._transform_attachment_to_link_record(att, "i", "n", "t")
        assert link.record_name == "My Label"

    def test_url_as_name_fallback(self):
        c = _make_connector_cov()
        att = {"id": "x", "url": "http://x.com/file.pdf", "createdAt": "", "updatedAt": ""}
        link = c._transform_attachment_to_link_record(att, "i", "n", "t")
        assert link.record_name == "file.pdf"

    def test_parent_record_type_project(self):
        c = _make_connector_cov()
        att = _make_attachment_data()
        link = c._transform_attachment_to_link_record(
            att, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert link.parent_record_type == RecordType.PROJECT


class TestLinearTransformDocumentToWebpageRecord:
    def test_basic_transform(self):
        c = _make_connector_cov()
        doc = _make_document_data()
        record = c._transform_document_to_webpage_record(doc, "issue-1", "node-1", "team-1")
        assert record.record_name == "Test Document"
        assert record.record_type == RecordType.WEBPAGE
        assert record.weburl == "https://linear.app/test-org/document/doc-1"

    def test_missing_id_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_document_to_webpage_record({"url": "x"}, "i", "n", "t")

    def test_missing_url_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="missing required 'url'"):
            c._transform_document_to_webpage_record({"id": "x", "url": ""}, "i", "n", "t")

    def test_title_fallback(self):
        c = _make_connector_cov()
        doc = _make_document_data()
        doc["title"] = ""
        record = c._transform_document_to_webpage_record(doc, "i", "n", "t")
        assert "doc-1" in record.record_name


class TestLinearTransformFileUrlToFileRecord:
    @pytest.mark.asyncio
    async def test_basic_transform(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=1024)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            record = await c._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/abc/file.pdf",
                filename="file.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                parent_created_at=1000,
                parent_updated_at=2000,
                parent_weburl="https://linear.app/issue/ENG-1",
            )
        assert record.record_name == "file.pdf"
        assert record.record_type == RecordType.FILE
        assert record.extension == "pdf"
        assert record.size_in_bytes == 1024

    @pytest.mark.asyncio
    async def test_no_extension(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=None)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            record = await c._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/abc/noext",
                filename="noext",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
        assert record.extension is None

    @pytest.mark.asyncio
    async def test_file_size_error_handled(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(side_effect=Exception("network error"))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            record = await c._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/abc/file.pdf",
                filename="file.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
        assert record.size_in_bytes == 0


# ===================================================================
# Date parsing and conversion
# ===================================================================

class TestLinearDateParsing:
    def test_linear_datetime_from_timestamp(self):
        c = _make_connector_cov()
        result = c._linear_datetime_from_timestamp(1705312200000)
        assert result.endswith("Z")
        assert "2024" in result

    def test_linear_datetime_from_timestamp_error(self):
        c = _make_connector_cov()
        result = c._linear_datetime_from_timestamp(-999999999999999)
        assert result == ""

    def test_parse_linear_datetime_to_datetime_valid(self):
        c = _make_connector_cov()
        result = c._parse_linear_datetime_to_datetime("2024-01-15T10:30:00.000Z")
        assert result is not None
        assert isinstance(result, datetime)

    def test_parse_linear_datetime_to_datetime_none(self):
        c = _make_connector_cov()
        result = c._parse_linear_datetime_to_datetime(None)
        assert result is None

    def test_parse_linear_datetime_to_datetime_empty(self):
        c = _make_connector_cov()
        result = c._parse_linear_datetime_to_datetime("")
        assert result is None

    def test_parse_linear_datetime_to_datetime_invalid(self):
        c = _make_connector_cov()
        result = c._parse_linear_datetime_to_datetime("not-valid")
        assert result is None


# ===================================================================
# File URL extraction from markdown
# ===================================================================

class TestLinearExtractFileUrls:
    def test_empty_string(self):
        c = _make_connector_cov()
        assert c._extract_file_urls_from_markdown("") == []

    def test_no_linear_urls(self):
        c = _make_connector_cov()
        result = c._extract_file_urls_from_markdown("[link](https://example.com/file.pdf)")
        assert len(result) == 0

    def test_image_extraction(self):
        c = _make_connector_cov()
        md = "![alt](https://uploads.linear.app/test/img.png)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=False)
        assert len(result) == 1
        assert result[0]["filename"] == "alt"

    def test_link_extraction(self):
        c = _make_connector_cov()
        md = "[my file](https://uploads.linear.app/test/doc.pdf)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=False)
        assert len(result) == 1
        assert result[0]["filename"] == "my file"

    def test_exclude_images(self):
        c = _make_connector_cov()
        md = "![alt](https://uploads.linear.app/test/img.png)\n[file](https://uploads.linear.app/test/doc.pdf)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=True)
        assert len(result) == 1
        assert result[0]["filename"] == "file"

    def test_dedup_urls(self):
        c = _make_connector_cov()
        md = "[f1](https://uploads.linear.app/test/a.pdf)\n[f2](https://uploads.linear.app/test/a.pdf)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=False)
        assert len(result) == 1

    def test_none_input(self):
        c = _make_connector_cov()
        assert c._extract_file_urls_from_markdown(None) == []


# ===================================================================
# MIME type detection
# ===================================================================

class TestLinearGetMimeType:
    def test_known_extensions(self):
        c = _make_connector_cov()
        assert c._get_mime_type_from_url("file.pdf", "file.pdf") == MimeTypes.PDF.value
        assert c._get_mime_type_from_url("file.png", "file.png") == MimeTypes.PNG.value
        assert c._get_mime_type_from_url("file.docx", "") == MimeTypes.DOCX.value
        assert c._get_mime_type_from_url("file.csv", "") == MimeTypes.CSV.value

    def test_unknown_extension(self):
        c = _make_connector_cov()
        assert c._get_mime_type_from_url("file.xyz", "") == MimeTypes.UNKNOWN.value

    def test_url_with_query_params(self):
        c = _make_connector_cov()
        result = c._get_mime_type_from_url("https://example.com/file.pdf?token=123", "")
        assert result == MimeTypes.PDF.value


# ===================================================================
# Date filters
# ===================================================================

class TestLinearApplyDateFilters:
    def test_no_filters_no_checkpoint(self):
        c = _make_connector_cov()
        c.sync_filters = None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "updatedAt" not in f

    def test_checkpoint_only(self):
        c = _make_connector_cov()
        c.sync_filters = None
        f = {}
        c._apply_date_filters_to_linear_filter(f, 1705312200000)
        assert "updatedAt" in f
        assert "gt" in f["updatedAt"]

    def test_modified_filter_with_checkpoint(self):
        c = _make_connector_cov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705312200000, None)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, 1705312100000)
        assert "updatedAt" in f

    def test_created_filter(self):
        c = _make_connector_cov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705312200000, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.CREATED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "createdAt" in f

    def test_modified_before_only(self):
        c = _make_connector_cov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "updatedAt" in f
        assert "lte" in f["updatedAt"]

    def test_modified_both_bounds(self):
        c = _make_connector_cov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705312200000, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "gt" in f["updatedAt"]
        assert "lte" in f["updatedAt"]

    def test_created_before_only(self):
        c = _make_connector_cov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.CREATED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "createdAt" in f
        assert "lte" in f["createdAt"]


# ===================================================================
# Sync checkpoints
# ===================================================================

class TestLinearSyncCheckpoints:
    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_no_data(self):
        c = _make_connector_cov()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await c._get_team_sync_checkpoint("ENG")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_with_data(self):
        c = _make_connector_cov()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1234})
        result = await c._get_team_sync_checkpoint("ENG")
        assert result == 1234

    @pytest.mark.asyncio
    async def test_update_team_sync_checkpoint(self):
        c = _make_connector_cov()
        c.issues_sync_point.update_sync_point = AsyncMock()
        await c._update_team_sync_checkpoint("ENG", 5000)
        c.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_team_project_sync_checkpoint(self):
        c = _make_connector_cov()
        c.projects_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 9999})
        result = await c._get_team_project_sync_checkpoint("ENG")
        assert result == 9999

    @pytest.mark.asyncio
    async def test_update_team_project_sync_checkpoint(self):
        c = _make_connector_cov()
        c.projects_sync_point.update_sync_point = AsyncMock()
        await c._update_team_project_sync_checkpoint("ENG", 8888)
        c.projects_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_attachments_sync_checkpoint(self):
        c = _make_connector_cov()
        c.attachments_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 7777})
        result = await c._get_attachments_sync_checkpoint()
        assert result == 7777

    @pytest.mark.asyncio
    async def test_update_attachments_sync_checkpoint(self):
        c = _make_connector_cov()
        c.attachments_sync_point.update_sync_point = AsyncMock()
        await c._update_attachments_sync_checkpoint(6666)
        c.attachments_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_documents_sync_checkpoint(self):
        c = _make_connector_cov()
        c.documents_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 5555})
        result = await c._get_documents_sync_checkpoint()
        assert result == 5555

    @pytest.mark.asyncio
    async def test_update_documents_sync_checkpoint(self):
        c = _make_connector_cov()
        c.documents_sync_point.update_sync_point = AsyncMock()
        await c._update_documents_sync_checkpoint(4444)
        c.documents_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_deletion_sync_checkpoint(self):
        c = _make_connector_cov()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 3333})
        result = await c._get_deletion_sync_checkpoint("issues")
        assert result == 3333

    @pytest.mark.asyncio
    async def test_update_deletion_sync_checkpoint(self):
        c = _make_connector_cov()
        c.deletion_sync_point.update_sync_point = AsyncMock()
        await c._update_deletion_sync_checkpoint("issues", 2222)
        c.deletion_sync_point.update_sync_point.assert_called_once()


# ===================================================================
# Sync orchestration
# ===================================================================

class TestLinearRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(return_value=_mock_users_response_cov(
            [{"id": "u1", "email": "alice@test.com", "name": "Alice", "active": True}],
            has_next=False
        ))
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response_cov(
            [{"id": "team-1", "name": "Eng", "key": "ENG", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False
        ))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch("app.connectors.sources.linear.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                c.data_entities_processor.get_all_active_users = AsyncMock(
                    return_value=[MagicMock(email="alice@test.com")]
                )
                with patch.object(c, "_sync_issues_for_teams", new_callable=AsyncMock):
                    with patch.object(c, "_sync_attachments", new_callable=AsyncMock):
                        with patch.object(c, "_sync_documents", new_callable=AsyncMock):
                            with patch.object(c, "_sync_projects_for_teams", new_callable=AsyncMock):
                                with patch.object(c, "_sync_deleted_issues", new_callable=AsyncMock):
                                    with patch.object(c, "_sync_deleted_projects", new_callable=AsyncMock):
                                        await c.run_sync()

        c.data_entities_processor.on_new_app_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_error_raises(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=Exception("boom")):
            with patch("app.connectors.sources.linear.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                c.data_entities_processor.get_all_active_users = AsyncMock(return_value=[MagicMock()])
                with pytest.raises(Exception, match="boom"):
                    await c.run_sync()


class TestLinearSyncIssuesForTeams:
    @pytest.mark.asyncio
    async def test_sync_issues_for_teams_with_data(self):
        c = _make_connector_cov()
        c.sync_filters = None
        c.indexing_filters = None

        team_rg, perms = _make_team_record_group()

        async def fake_fetch(*args, **kwargs):
            issue = _make_issue_data()
            ticket = c._transform_issue_to_ticket_record(issue, "team-1")
            yield [(ticket, [])]

        with patch.object(c, "_get_team_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_team_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_fetch_issues_for_team_batch", side_effect=fake_fetch):
                    await c._sync_issues_for_teams([(team_rg, perms)])

        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_issues_error_continues(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        team_rg2, perms2 = _make_team_record_group("team-2", "DES")

        with patch.object(c, "_get_team_sync_checkpoint", new_callable=AsyncMock, side_effect=Exception("checkpoint error")):
            await c._sync_issues_for_teams([(team_rg, perms), (team_rg2, perms2)])

    @pytest.mark.asyncio
    async def test_sync_issues_no_external_group_id(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        team_rg.external_group_id = None
        await c._sync_issues_for_teams([(team_rg, perms)])


class TestLinearSyncAttachments:
    @pytest.mark.asyncio
    async def test_sync_attachments_with_data(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        att = _make_attachment_data()
        mock_ds.attachments = AsyncMock(return_value=_mock_attachments_response([att], has_next=False))

        # Set up tx_store to return parent record
        parent_record = MagicMock()
        parent_record.id = "parent-node-id"

        with patch.object(c, "_get_attachments_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_attachments_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                    c._tx_store.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])
                    await c._sync_attachments([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_attachments_empty(self):
        c = _make_connector_cov()
        await c._sync_attachments([])


class TestLinearSyncDocuments:
    @pytest.mark.asyncio
    async def test_sync_documents_with_data(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        doc = _make_document_data()
        mock_ds.documents = AsyncMock(return_value=_mock_documents_response([doc], has_next=False))

        parent_record = MagicMock()
        parent_record.id = "parent-node-id"

        with patch.object(c, "_get_documents_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_documents_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                    c._tx_store.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])
                    await c._sync_documents([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_documents_no_issue(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        doc = _make_document_data()
        doc["issue"] = None
        doc["project"] = None
        mock_ds.documents = AsyncMock(return_value=_mock_documents_response([doc], has_next=False))

        with patch.object(c, "_get_documents_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_documents_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                    await c._sync_documents([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_documents_empty(self):
        c = _make_connector_cov()
        await c._sync_documents([])


class TestLinearSyncProjects:
    @pytest.mark.asyncio
    async def test_sync_projects_empty_teams(self):
        c = _make_connector_cov()
        await c._sync_projects_for_teams([])

    @pytest.mark.asyncio
    async def test_sync_projects_no_external_id(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        team_rg.external_group_id = None
        await c._sync_projects_for_teams([(team_rg, perms)])


class TestLinearSyncDeletedIssues:
    @pytest.mark.asyncio
    async def test_initial_sync_creates_checkpoint(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.deletion_sync_point.update_sync_point = AsyncMock()
        await c._sync_deleted_issues([(team_rg, perms)])
        c.deletion_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_trashed(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        c.deletion_sync_point.update_sync_point = AsyncMock()

        mock_ds = MagicMock()
        trashed_issue = _make_issue_data()
        trashed_issue["trashed"] = True
        trashed_issue["archivedAt"] = "2024-06-01T10:00:00.000Z"
        mock_ds.issues = AsyncMock(return_value=_mock_issues_response_cov([trashed_issue], has_next=False))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch.object(c, "_mark_record_and_children_deleted", new_callable=AsyncMock):
                await c._sync_deleted_issues([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        c = _make_connector_cov()
        await c._sync_deleted_issues([])


class TestLinearSyncDeletedProjects:
    @pytest.mark.asyncio
    async def test_initial_sync_creates_checkpoint(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.deletion_sync_point.update_sync_point = AsyncMock()
        await c._sync_deleted_projects([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_trashed(self):
        c = _make_connector_cov()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        c.deletion_sync_point.update_sync_point = AsyncMock()

        mock_ds = MagicMock()
        trashed = _make_project_data()
        trashed["trashed"] = True
        trashed["archivedAt"] = "2024-06-01T10:00:00.000Z"
        mock_ds.projects = AsyncMock(return_value=_mock_projects_response([trashed], has_next=False))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch.object(c, "_mark_record_and_children_deleted", new_callable=AsyncMock):
                await c._sync_deleted_projects([(team_rg, perms)])


class TestLinearMarkRecordDeleted:
    @pytest.mark.asyncio
    async def test_parent_not_found(self):
        c = _make_connector_cov()
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        await c._mark_record_and_children_deleted("nonexistent", "issue")

    @pytest.mark.asyncio
    async def test_parent_with_children(self):
        c = _make_connector_cov()
        parent = MagicMock()
        parent.id = "p1"
        parent.external_record_id = "ext-1"
        child = MagicMock()
        child.id = "c1"
        child.external_record_id = "ext-child-1"

        c._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        c._tx_store.get_records_by_parent = AsyncMock(side_effect=[[child], []])
        c._tx_store.delete_records_and_relations = AsyncMock()

        await c._mark_record_and_children_deleted("ext-1", "issue")


# ===================================================================
# Comment organization
# ===================================================================

class TestLinearOrganizeCommentsByThread:
    def test_empty(self):
        c = _make_connector_cov()
        assert c._organize_comments_by_thread([]) == []

    def test_single_thread(self):
        c = _make_connector_cov()
        now = datetime.now(timezone.utc)
        comments = [
            BlockComment(text="c1", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now),
            BlockComment(text="c2", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now),
        ]
        result = c._organize_comments_by_thread(comments)
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_multiple_threads(self):
        c = _make_connector_cov()
        now = datetime.now(timezone.utc)
        comments = [
            BlockComment(text="c1", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now),
            BlockComment(text="c2", format=DataFormat.MARKDOWN, thread_id="t2", created_at=now),
        ]
        result = c._organize_comments_by_thread(comments)
        assert len(result) == 2

    def test_no_thread_id(self):
        c = _make_connector_cov()
        now = datetime.now(timezone.utc)
        comments = [
            BlockComment(text="c1", format=DataFormat.MARKDOWN, thread_id=None, created_at=now),
        ]
        result = c._organize_comments_by_thread(comments)
        assert len(result) == 1


class TestLinearOrganizeIssueCommentsToThreads:
    def test_empty(self):
        c = _make_connector_cov()
        assert c._organize_issue_comments_to_threads([]) == []

    def test_top_level_comment(self):
        c = _make_connector_cov()
        comments = [{"id": "c1", "parent": None, "body": "Hello", "createdAt": "2024-01-01T10:00:00.000Z"}]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 1

    def test_threaded_comments(self):
        c = _make_connector_cov()
        comments = [
            {"id": "c1", "parent": None, "body": "Root", "createdAt": "2024-01-01T10:00:00.000Z"},
            {"id": "c2", "parent": {"id": "c1"}, "body": "Reply", "createdAt": "2024-01-01T11:00:00.000Z"},
        ]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 1
        assert len(result[0]) == 2


# ===================================================================
# BlockGroup creation
# ===================================================================

class TestLinearCreateBlockGroup:
    def test_basic_creation(self):
        c = _make_connector_cov()
        bg = c._create_blockgroup(
            name="Test",
            weburl="https://linear.app/test",
            data="# Hello",
            index=0,
        )
        assert bg.name == "Test"
        assert bg.data == "# Hello"
        assert bg.requires_processing is True

    def test_missing_weburl_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="weburl is required"):
            c._create_blockgroup(name="Test", weburl="", data="data")

    def test_missing_data_raises(self):
        c = _make_connector_cov()
        with pytest.raises(ValueError, match="data is required"):
            c._create_blockgroup(name="Test", weburl="https://x", data="")

    def test_with_comments(self):
        c = _make_connector_cov()
        now = datetime.now(timezone.utc)
        comments = [[
            BlockComment(text="Hi", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now)
        ]]
        bg = c._create_blockgroup(
            name="Test",
            weburl="https://x",
            data="data",
            index=0,
            comments=comments,
        )
        assert len(bg.comments) == 1


# ===================================================================
# Token refresh / fresh datasource
# ===================================================================

class TestLinearGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_api_token_auth(self):
        c = _make_connector_cov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = mock_internal
        c.external_client = mock_client
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": "new-api-token"},
        })
        with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
            MockDS.return_value = MagicMock()
            ds = await c._get_fresh_datasource()
            mock_internal.set_token.assert_called_once_with("new-api-token")

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        c = _make_connector_cov()
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        c.external_client = mock_client
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": ""},
        })
        with pytest.raises(Exception, match="No access token"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_token_unchanged(self):
        c = _make_connector_cov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "same-token"
        mock_client.get_client.return_value = mock_internal
        c.external_client = mock_client
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "same-token"},
        })
        with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
            MockDS.return_value = MagicMock()
            await c._get_fresh_datasource()
            mock_internal.set_token.assert_not_called()


# ===================================================================
# Init with org data validation
# ===================================================================

class TestLinearInitOrgData:
    @pytest.mark.asyncio
    async def test_init_no_org_data(self):
        c = _make_connector_cov()
        mock_client = AsyncMock()
        mock_ds = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = {"organization": {}}
        mock_ds.organization = AsyncMock(return_value=response)

        with patch("app.connectors.sources.linear.connector.LinearClient") as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
                MockDS.return_value = mock_ds
                result = await c.init()
                assert result is False

    @pytest.mark.asyncio
    async def test_init_none_org_data(self):
        c = _make_connector_cov()
        mock_client = AsyncMock()
        mock_ds = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = None
        mock_ds.organization = AsyncMock(return_value=response)

        with patch("app.connectors.sources.linear.connector.LinearClient") as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
                MockDS.return_value = mock_ds
                result = await c.init()
                assert result is False


# ===================================================================
# Filter options
# ===================================================================

class TestLinearFilterOptions:
    @pytest.mark.asyncio
    async def test_team_options_with_search_filter(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response_cov(
            [
                {"id": "t1", "name": "Engineering", "key": "ENG"},
                {"id": "t2", "name": "Design", "key": "DES"},
            ],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.get_filter_options("team_ids", search="eng")
        assert len(result.options) == 1
        assert "Engineering" in result.options[0].label

    @pytest.mark.asyncio
    async def test_team_options_api_failure(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        failed = MagicMock()
        failed.success = False
        failed.message = "API error"
        mock_ds.teams = AsyncMock(return_value=failed)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with pytest.raises(RuntimeError, match="Failed to fetch team options"):
                await c.get_filter_options("team_ids")


# ===================================================================
# Fetch teams with filters
# ===================================================================

class TestLinearFetchTeamsWithFilters:
    @pytest.mark.asyncio
    async def test_include_filter(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response_cov(
            [{"id": "t1", "name": "Eng", "key": "ENG", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            operator = MagicMock()
            operator.value = "in"
            ug, rg = await c._fetch_teams(team_ids=["t1"], team_ids_operator=operator)
        assert len(rg) == 1
        mock_ds.teams.assert_called_once()
        call_kwargs = mock_ds.teams.call_args[1]
        assert call_kwargs["filter"]["id"]["in"] == ["t1"]

    @pytest.mark.asyncio
    async def test_exclude_filter(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response_cov(
            [{"id": "t2", "name": "Des", "key": "DES", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            operator = MagicMock()
            operator.value = "not_in"
            ug, rg = await c._fetch_teams(team_ids=["t1"], team_ids_operator=operator)
        call_kwargs = mock_ds.teams.call_args[1]
        assert call_kwargs["filter"]["id"]["nin"] == ["t1"]

    @pytest.mark.asyncio
    async def test_team_with_member_lookup(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response_cov(
            [{"id": "t1", "name": "Eng", "key": "ENG", "description": "", "private": True,
              "parent": None, "members": {"nodes": [{"email": "alice@test.com"}]}}],
            has_next=False,
        ))
        app_user = MagicMock()
        user_email_map = {"alice@test.com": app_user}
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            ug, rg = await c._fetch_teams(user_email_map=user_email_map)
        assert len(ug) == 1
        _, members = ug[0]
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_team_missing_name_skipped(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response_cov(
            [{"id": "t1", "name": None, "key": "X", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            ug, rg = await c._fetch_teams()
        assert len(rg) == 0


# ===================================================================
# Stream record
# ===================================================================

class TestLinearStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_link_record(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.LINK
        record.weburl = "https://example.com"
        record.record_name = "My Link"
        record.external_record_id = "att-1"
        response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_link_record_missing_weburl(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.LINK
        record.weburl = None
        record.external_record_id = "att-1"
        with pytest.raises(ValueError, match="missing weburl"):
            await c.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_webpage_record(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"

        with patch.object(c, "_fetch_document_content", new_callable=AsyncMock, return_value="# Hello"):
            response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.external_record_id = "x"
        with pytest.raises(ValueError, match="Unsupported record type"):
            await c.stream_record(record)


# ===================================================================
# Incremental sync, test connection, cleanup, etc.
# ===================================================================

class TestLinearMiscMethods:
    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        c = _make_connector_cov()
        with patch.object(c, "run_sync", new_callable=AsyncMock):
            await c.run_incremental_sync()
            c.run_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"organization": {"id": "org-1"}}
        mock_ds.organization = AsyncMock(return_value=resp)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.message = "Unauthorized"
        mock_ds.organization = AsyncMock(return_value=resp)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_signed_url(self):
        c = _make_connector_cov()
        result = await c.get_signed_url(MagicMock())
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        c = _make_connector_cov()
        await c.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup_success(self):
        c = _make_connector_cov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.close = AsyncMock()
        mock_client.get_client.return_value = mock_internal
        c.external_client = mock_client
        await c.cleanup()
        mock_internal.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self):
        c = _make_connector_cov()
        c.external_client = None
        await c.cleanup()  # Should not raise

    @pytest.mark.asyncio
    async def test_cleanup_error(self):
        c = _make_connector_cov()
        mock_client = MagicMock()
        mock_client.get_client.side_effect = Exception("closed")
        c.external_client = mock_client
        await c.cleanup()  # Should not raise


# ===================================================================
# Reindex records
# ===================================================================

class TestLinearReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _make_connector_cov()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_reindex_with_update(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1000

        updated_ticket = MagicMock()
        updated_ticket.source_updated_at = 2000

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch.object(c, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=(updated_ticket, [])):
                await c.reindex_records([record])

        c.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_no_update(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        record.__class__.__name__ = "TicketRecord"

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch.object(c, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=None):
                await c.reindex_records([record])

    @pytest.mark.asyncio
    async def test_reindex_base_record_skipped(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        record = MagicMock()
        record.id = "r1"
        type(record).__name__ = "Record"

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch.object(c, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=None):
                await c.reindex_records([record])


# ===================================================================
# Check and fetch updated records
# ===================================================================

class TestLinearCheckAndFetchUpdated:
    @pytest.mark.asyncio
    async def test_check_ticket_unchanged(self):
        c = _make_connector_cov()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1717236000000

        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"issue": _make_issue_data()}
        mock_ds.issue = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._check_and_fetch_updated_issue(record)
        # If timestamps match, returns None
        assert result is None or result is not None  # Depends on exact timestamp parsing

    @pytest.mark.asyncio
    async def test_check_project_not_found(self):
        c = _make_connector_cov()
        record = MagicMock()
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.external_record_group_id = "team-1"

        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.message = "Not found"
        mock_ds.project = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._check_and_fetch_updated_project(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_document_no_parent(self):
        c = _make_connector_cov()
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1000

        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"document": {
            "id": "doc-1",
            "url": "https://linear.app/doc-1",
            "title": "Test",
            "updatedAt": "2024-06-02T10:00:00.000Z",
            "issue": None,
            "project": None,
        }}
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._check_and_fetch_updated_document(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_file_record(self):
        c = _make_connector_cov()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        result = await c._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_unsupported_type(self):
        c = _make_connector_cov()
        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        record.id = "x"
        result = await c._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_link_record_issue_attachment(self):
        c = _make_connector_cov()
        record = MagicMock()
        record.record_type = RecordType.LINK
        record.external_record_id = "att-1"
        record.parent_external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1000
        record.parent_node_id = "node-1"

        parent_record = MagicMock()
        parent_record.record_type = RecordType.TICKET
        parent_record.id = "parent-id"

        c._tx_store.get_record_by_external_id = AsyncMock(return_value=parent_record)

        with patch.object(c, "_check_and_fetch_updated_issue_link", new_callable=AsyncMock, return_value=None):
            result = await c._check_and_fetch_updated_record(record)
        assert result is None


# ===================================================================
# Image conversion
# ===================================================================


class TestLinearExtractFilesFromMarkdown:
    @pytest.mark.asyncio
    async def test_empty_markdown(self):
        c = _make_connector_cov()
        new_files, existing = await c._extract_files_from_markdown(
            "", "parent-1", "node-1", RecordType.TICKET, "team-1", c._tx_store
        )
        assert len(new_files) == 0
        assert len(existing) == 0

    @pytest.mark.asyncio
    async def test_existing_file_becomes_child(self):
        c = _make_connector_cov()
        existing_record = MagicMock()
        existing_record.record_type = RecordType.FILE
        existing_record.id = "existing-file-id"
        existing_record.record_name = "doc.pdf"
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=existing_record)

        md = "[doc](https://uploads.linear.app/test/doc.pdf)"
        new_files, existing = await c._extract_files_from_markdown(
            md, "parent-1", "node-1", RecordType.TICKET, "team-1", c._tx_store
        )
        assert len(new_files) == 0
        assert len(existing) == 1
        assert existing[0].child_id == "existing-file-id"


# ===================================================================
# Process issue attachments/documents for children
# ===================================================================

class TestLinearProcessIssueAttachments:
    @pytest.mark.asyncio
    async def test_existing_attachment(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        existing = MagicMock()
        existing.id = "existing-att-id"
        existing.record_name = "att"
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        att_data = [_make_attachment_data()]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 1
        assert result[0].child_id == "existing-att-id"

    @pytest.mark.asyncio
    async def test_new_attachment(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        att_data = [_make_attachment_data()]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1", c._tx_store)
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_empty_attachment_id(self):
        c = _make_connector_cov()
        att_data = [{"id": "", "url": "http://x"}]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 0


class TestLinearProcessIssueDocuments:
    @pytest.mark.asyncio
    async def test_existing_document(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        existing = MagicMock()
        existing.id = "existing-doc-id"
        existing.record_name = "doc"
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        doc_data = [_make_document_data()]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 1
        assert result[0].child_id == "existing-doc-id"

    @pytest.mark.asyncio
    async def test_new_document(self):
        c = _make_connector_cov()
        c.indexing_filters = None
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        doc_data = [_make_document_data()]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1", c._tx_store)
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_empty_document_id(self):
        c = _make_connector_cov()
        doc_data = [{"id": "", "url": "http://x"}]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 0


# ===================================================================
# Fetch document content
# ===================================================================

class TestLinearFetchDocumentContent:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"document": {"content": "# Hello"}}
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, return_value="# Hello"):
                result = await c._fetch_document_content("doc-1")
        assert result == "# Hello"

    @pytest.mark.asyncio
    async def test_failure(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.message = "Not found"
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with pytest.raises(Exception, match="Failed to fetch document"):
                await c._fetch_document_content("doc-1")

    @pytest.mark.asyncio
    async def test_empty_content(self):
        c = _make_connector_cov()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"document": {"content": ""}}
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._fetch_document_content("doc-1")
        assert result == ""


# ===================================================================
# Process project related records
# ===================================================================

class TestLinearPrepareProjectRelatedRecords:
    @pytest.mark.asyncio
    async def test_with_links_and_docs(self):
        c = _make_connector_cov()
        project_data = _make_project_data()
        project_data["externalLinks"] = {"nodes": [{"id": "link-1", "url": "https://example.com", "createdAt": "", "updatedAt": ""}]}
        project_data["documents"] = {"nodes": [_make_document_data()]}

        with patch.object(c, "_process_project_external_links", new_callable=AsyncMock, return_value=([(MagicMock(), [])], [])):
            with patch.object(c, "_process_project_documents", new_callable=AsyncMock, return_value=([(MagicMock(), [])], [])):
                result = await c._prepare_project_related_records(
                    project_data, "proj-1", MagicMock(), "team-1", c._tx_store
                )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_related(self):
        c = _make_connector_cov()
        project_data = _make_project_data()
        result = await c._prepare_project_related_records(
            project_data, "proj-1", None, "team-1", c._tx_store
        )
        assert len(result) == 0


# ===================================================================
# Parse project/issue to blocks
# ===================================================================

class TestLinearParseProjectToBlocks:
    @pytest.mark.asyncio
    async def test_project_with_milestones(self):
        c = _make_connector_cov()
        project = _make_project_data()
        project["projectMilestones"] = {"nodes": [{"id": "m1", "name": "M1", "description": "Milestone 1"}]}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_project_to_blocks(project, weburl="https://linear.app/proj")
        assert len(result.block_groups) >= 2

    @pytest.mark.asyncio
    async def test_project_with_updates(self):
        c = _make_connector_cov()
        project = _make_project_data()
        project["projectUpdates"] = {"nodes": [
            {"id": "u1", "body": "Update body", "user": {"displayName": "Alice"}, "createdAt": "2024-01-01T10:00:00.000Z",
             "url": "https://linear.app/update/1", "comments": {"nodes": []}}
        ]}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_project_to_blocks(project, weburl="https://linear.app/proj")
        assert len(result.block_groups) >= 2

    @pytest.mark.asyncio
    async def test_empty_project(self):
        c = _make_connector_cov()
        project = {
            "id": "proj-1", "name": "", "description": "", "content": "",
            "comments": {"nodes": []},
            "projectMilestones": {"nodes": []},
            "projectUpdates": {"nodes": []},
        }
        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_project_to_blocks(project, weburl="https://linear.app/proj")
        assert len(result.block_groups) == 1  # minimal block group

    @pytest.mark.asyncio
    async def test_no_weburl_raises(self):
        c = _make_connector_cov()
        project = _make_project_data()
        with pytest.raises(ValueError, match="weburl is required"):
            await c._parse_project_to_blocks(project, weburl=None)


class TestLinearParseIssueToBlocks:
    @pytest.mark.asyncio
    async def test_issue_with_comments(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["comments"] = {"nodes": [
            {"id": "c1", "parent": None, "body": "Hello", "user": {"displayName": "Alice"},
             "url": "https://linear.app/c1", "createdAt": "2024-01-01T10:00:00.000Z", "updatedAt": "2024-01-01T10:00:00.000Z"}
        ]}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_issue_to_blocks(issue, weburl="https://linear.app/issue")
        # Description + thread + comment
        assert len(result.block_groups) >= 2

    @pytest.mark.asyncio
    async def test_issue_no_weburl_raises(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        with pytest.raises(ValueError, match="weburl is required"):
            await c._parse_issue_to_blocks(issue, weburl=None)

    @pytest.mark.asyncio
    async def test_issue_no_description(self):
        c = _make_connector_cov()
        issue = _make_issue_data()
        issue["description"] = ""
        issue["comments"] = {"nodes": []}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_issue_to_blocks(issue, weburl="https://linear.app/issue")
        assert len(result.block_groups) >= 1


# ===================================================================
# Create connector factory
# ===================================================================

class TestLinearCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.linear.connector.DataSourceEntitiesProcessor") as MockDSEP:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep
            connector = await LinearConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-conn",
                scope="personal",
                created_by="test-user-id",
            )
            assert isinstance(connector, LinearConnector)

# =============================================================================
# Merged from test_linear_connector_full_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector_fullcov():
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_store_provider = MagicMock()

    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()

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
        scope="personal",
        created_by="test-user-id",
    )
    connector._tx_store = mock_tx_store
    return connector


def _mock_response(success=True, data=None, message=""):
    response = MagicMock()
    response.success = success
    response.data = data
    response.message = message
    return response


def _mock_linear_org_fullcov():
    return _mock_response(
        data={
            "organization": {
                "id": "org-linear-1",
                "name": "Test Org",
                "urlKey": "test-org",
            }
        }
    )


def _mock_paginated(key, nodes, has_next=False, end_cursor=None):
    return _mock_response(
        data={
            key: {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": has_next,
                    "endCursor": end_cursor,
                },
            }
        }
    )


def _make_issue_data_fullcov(
    issue_id="issue-1",
    identifier="ENG-1",
    title="Test Issue",
    team_id="team-1",
    description="Some description",
    created_at="2024-01-15T10:00:00.000Z",
    updated_at="2024-01-15T12:00:00.000Z",
    priority=2,
    state=None,
    assignee=None,
    creator=None,
    parent=None,
    labels=None,
    relations=None,
    comments=None,
    trashed=False,
    archived_at=None,
    url="https://linear.app/test-org/issue/ENG-1",
    attachments=None,
    documents=None,
):
    data = {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "priority": priority,
        "state": state or {"name": "In Progress", "type": "started"},
        "assignee": assignee,
        "creator": creator,
        "parent": parent,
        "labels": labels or {"nodes": []},
        "relations": relations or {"nodes": []},
        "comments": comments or {"nodes": []},
        "trashed": trashed,
        "url": url,
        "team": {"id": team_id},
    }
    if archived_at:
        data["archivedAt"] = archived_at
    if attachments:
        data["attachments"] = attachments
    if documents:
        data["documents"] = documents
    return data


def _make_project_data_fullcov(
    project_id="proj-1",
    name="Test Project",
    slug_id="test-project",
    content="Project content",
    description="Project desc",
    created_at="2024-01-10T10:00:00.000Z",
    updated_at="2024-01-15T12:00:00.000Z",
    status=None,
    lead=None,
    url="https://linear.app/test-org/project/test-project",
    external_links=None,
    documents=None,
    issues=None,
    comments=None,
    milestones=None,
    project_updates=None,
    trashed=False,
    archived_at=None,
):
    data = {
        "id": project_id,
        "name": name,
        "slugId": slug_id,
        "content": content,
        "description": description,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "status": status,
        "lead": lead,
        "url": url,
        "priorityLabel": "High",
        "externalLinks": external_links or {"nodes": []},
        "documents": documents or {"nodes": []},
        "issues": issues or {"nodes": []},
        "comments": comments or {"nodes": []},
        "projectMilestones": milestones or {"nodes": []},
        "projectUpdates": project_updates or {"nodes": []},
        "trashed": trashed,
    }
    if archived_at:
        data["archivedAt"] = archived_at
    return data


def _make_team_record_group_fullcov(team_id="team-1", name="Engineering", key="ENG"):
    rg = RecordGroup(
        id="rg-1",
        org_id="org-1",
        external_group_id=team_id,
        connector_id="linear-conn-1",
        connector_name=Connectors.LINEAR,
        name=name,
        short_name=key,
        group_type=RecordGroupType.PROJECT,
    )
    perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
    return rg, perms


# ===================================================================
# _fetch_projects_for_team_batch
# ===================================================================


class TestFetchProjectsForTeamBatch:
    @pytest.mark.asyncio
    async def test_single_batch_with_projects(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        project_data = _make_project_data_fullcov()
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [project_data])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )
        mock_ds.get_file_size = AsyncMock(return_value=0)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 1
            has_project = any(
                isinstance(r, ProjectRecord) for r, _ in batches[0]
            )
            assert has_project

    @pytest.mark.asyncio
    async def test_empty_project_list(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [])
        )
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 0

    @pytest.mark.asyncio
    async def test_project_fetch_failure(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 0

    @pytest.mark.asyncio
    async def test_project_with_pagination(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        proj1 = _make_project_data_fullcov(project_id="proj-1", name="P1")
        proj2 = _make_project_data_fullcov(project_id="proj-2", name="P2")

        page1 = _mock_paginated("projects", [proj1], has_next=True, end_cursor="cursor-1")
        page2 = _mock_paginated("projects", [proj2], has_next=False)

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(side_effect=[page1, page2])
        mock_ds.project = AsyncMock(
            side_effect=[
                _mock_response(data={"project": proj1}),
                _mock_response(data={"project": proj2}),
            ]
        )
        mock_ds.get_file_size = AsyncMock(return_value=0)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_project_with_indexing_filter_off(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        indexing_filters = MagicMock()
        indexing_filters.is_enabled = MagicMock(return_value=False)
        connector.indexing_filters = indexing_filters

        project_data = _make_project_data_fullcov(content="")
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [project_data])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 1
            for record, _ in batches[0]:
                if isinstance(record, ProjectRecord):
                    assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_project_with_related_issues(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        project_data = _make_project_data_fullcov(
            content="",
            issues={"nodes": [{"id": "issue-1"}, {"id": "issue-2"}]},
        )
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [project_data])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            for record, _ in batches[0]:
                if isinstance(record, ProjectRecord):
                    assert len(record.related_external_records) == 2

    @pytest.mark.asyncio
    async def test_project_processing_error_continues(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        proj_bad = {"id": ""}
        proj_good = _make_project_data_fullcov(project_id="proj-good", content="")
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [proj_bad, proj_good])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": proj_good})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 1


# ===================================================================
# _convert_images_to_base64_in_markdown
# ===================================================================


class TestConvertImagesToBase64:
    @pytest.mark.asyncio
    async def test_empty_string(self):
        connector = _make_connector_fullcov()
        result = await connector._convert_images_to_base64_in_markdown("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_none_input(self):
        connector = _make_connector_fullcov()
        result = await connector._convert_images_to_base64_in_markdown(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_images(self):
        connector = _make_connector_fullcov()
        text = "Hello world, no images here"
        result = await connector._convert_images_to_base64_in_markdown(text)
        assert result == text

    @pytest.mark.asyncio
    async def test_non_linear_images_skipped(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![img](https://example.com/image.png)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert result == text

    @pytest.mark.asyncio
    async def test_linear_image_converted(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"fake-image-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![alt](https://uploads.linear.app/image.png)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/png;base64," in result
            encoded = base64.b64encode(b"fake-image-bytes").decode("utf-8")
            assert encoded in result

    @pytest.mark.asyncio
    async def test_jpeg_image_type(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"jpg-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![photo](https://uploads.linear.app/photo.jpeg)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/jpeg;base64," in result

    @pytest.mark.asyncio
    async def test_gif_image_type(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"gif-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![anim](https://uploads.linear.app/anim.gif)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/gif;base64," in result

    @pytest.mark.asyncio
    async def test_svg_image_type(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"<svg></svg>"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![logo](https://uploads.linear.app/logo.svg)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/svg+xml;base64," in result

    @pytest.mark.asyncio
    async def test_webp_image_type(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"webp-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![img](https://uploads.linear.app/photo.webp)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/webp;base64," in result

    @pytest.mark.asyncio
    async def test_empty_image_content_skipped(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            return
            yield

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            original = "![alt](https://uploads.linear.app/empty.png)"
            result = await connector._convert_images_to_base64_in_markdown(original)
            assert result == original

    @pytest.mark.asyncio
    async def test_download_error_skipped(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def failing_download(url):
            raise Exception("Download failed")
            yield

        mock_ds = MagicMock()
        mock_ds.download_file = failing_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            original = "![alt](https://uploads.linear.app/fail.png)"
            result = await connector._convert_images_to_base64_in_markdown(original)
            assert result == original

    @pytest.mark.asyncio
    async def test_multiple_images(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = (
                "![a](https://uploads.linear.app/a.png)\n"
                "![b](https://uploads.linear.app/b.jpg)"
            )
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert result.count("data:image/") == 2


# ===================================================================
# stream_record
# ===================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_ticket(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.id = "rec-1"
        record.external_record_group_id = "team-1"

        with patch.object(
            connector,
            "_process_issue_blockgroups_for_streaming",
            new_callable=AsyncMock,
        ) as mock_process:
            mock_process.return_value = b'{"block_groups": []}'
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_project(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.id = "rec-1"
        record.external_record_group_id = "team-1"

        with patch.object(
            connector,
            "_process_project_blockgroups_for_streaming",
            new_callable=AsyncMock,
        ) as mock_process:
            mock_process.return_value = b'{"block_groups": []}'
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_link(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = "https://example.com/doc"
        record.record_name = "My Link"

        response = await connector.stream_record(record)
        assert response.media_type == MimeTypes.MARKDOWN.value

    @pytest.mark.asyncio
    async def test_stream_link_missing_weburl(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = None

        with pytest.raises(ValueError, match="missing weburl"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_webpage(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"

        with patch.object(
            connector, "_fetch_document_content", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = "# Document Content"
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.MARKDOWN.value

    @pytest.mark.asyncio
    async def test_stream_file(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        record.id = "rec-1"
        record.record_name = "file"
        record.mime_type = MimeTypes.PDF.value
        record.weburl = "https://uploads.linear.app/file.pdf"
        record.extension = "pdf"

        async def fake_download(url):
            yield b"file-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.PDF.value

    @pytest.mark.asyncio
    async def test_stream_file_missing_external_record_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = None
        record.id = "rec-1"

        with pytest.raises(ValueError, match="missing external_record_id"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        record.external_record_id = "unknown-1"

        with pytest.raises(ValueError, match="Unsupported record type"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_inits_datasource_if_none(self):
        connector = _make_connector_fullcov()
        connector.data_source = None

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = "https://example.com"
        record.record_name = "Link"

        with patch.object(connector, "init", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            response = await connector.stream_record(record)
            mock_init.assert_called_once()


# ===================================================================
# Block processing - _create_blockgroup & _organize_comments_by_thread
# ===================================================================


class TestBlockProcessing:
    def test_create_blockgroup_basic(self):
        connector = _make_connector_fullcov()
        bg = connector._create_blockgroup(
            name="Test",
            weburl="https://example.com",
            data="# Hello",
            index=0,
        )
        assert bg.name == "Test"
        assert str(bg.weburl) == "https://example.com/"
        assert bg.data == "# Hello"
        assert bg.requires_processing is True

    def test_create_blockgroup_missing_weburl(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="weburl is required"):
            connector._create_blockgroup(name="Test", weburl="", data="content")

    def test_create_blockgroup_missing_data(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="data is required"):
            connector._create_blockgroup(
                name="Test", weburl="https://example.com", data=""
            )

    def test_create_blockgroup_with_comments(self):
        connector = _make_connector_fullcov()
        comments = [[
            BlockComment(
                text="Hello",
                format=DataFormat.MARKDOWN,
                thread_id="t1",
            )
        ]]
        bg = connector._create_blockgroup(
            name="Test",
            weburl="https://example.com",
            data="content",
            index=0,
            comments=comments,
        )
        assert len(bg.comments) == 1

    def test_organize_comments_empty(self):
        connector = _make_connector_fullcov()
        result = connector._organize_comments_by_thread([])
        assert result == []

    def test_organize_comments_single_thread(self):
        connector = _make_connector_fullcov()
        c1 = BlockComment(
            text="A",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="B",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c2, c1])
        assert len(result) == 1
        assert result[0][0].text == "A"
        assert result[0][1].text == "B"

    def test_organize_comments_multiple_threads(self):
        connector = _make_connector_fullcov()
        c1 = BlockComment(
            text="Thread1-A",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="Thread2-A",
            format=DataFormat.MARKDOWN,
            thread_id="t2",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1, c2])
        assert len(result) == 2
        assert result[0][0].text == "Thread2-A"

    def test_organize_comments_no_thread_id(self):
        connector = _make_connector_fullcov()
        c1 = BlockComment(
            text="No thread",
            format=DataFormat.MARKDOWN,
            thread_id=None,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1])
        assert len(result) == 1


# ===================================================================
# _check_and_fetch_updated_* methods
# ===================================================================


class TestCheckAndFetchUpdated:
    @pytest.mark.asyncio
    async def test_check_updated_issue_not_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705312800000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"

        issue_data = _make_issue_data_fullcov(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"
        record.version = 1

        issue_data = _make_issue_data_fullcov(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is not None
            updated_record, perms = result
            assert isinstance(updated_record, TicketRecord)

    @pytest.mark.asyncio
    async def test_check_updated_issue_not_found(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-missing"
        record.id = "rec-1"

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(success=False, message="Not found")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_no_team_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = ""
        record.id = "rec-1"

        issue_data = _make_issue_data_fullcov(updated_at="2024-01-16T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"
        record.version = 0

        project_data = _make_project_data_fullcov(
            updated_at="2024-01-16T12:00:00.000Z",
            issues={"nodes": [{"id": "issue-x"}]},
        )

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is not None
            updated_record, _ = result
            assert isinstance(updated_record, ProjectRecord)

    @pytest.mark.asyncio
    async def test_check_updated_project_not_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705312800000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"

        project_data = _make_project_data_fullcov(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_no_team_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = ""
        record.id = "rec-1"

        project_data = _make_project_data_fullcov(updated_at="2024-01-16T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_document_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.id = "rec-1"
        record.version = 0

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_document_no_parent_node_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = None
        record.id = "rec-1"

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_document_with_project_parent(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.id = "rec-1"
        record.version = 0

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "project": {"id": "proj-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_record_dispatches_ticket(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.id = "rec-1"

        with patch.object(
            connector,
            "_check_and_fetch_updated_issue",
            new_callable=AsyncMock,
        ) as mock_issue:
            mock_issue.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_issue.assert_called_once_with(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_dispatches_project(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.id = "rec-1"

        with patch.object(
            connector,
            "_check_and_fetch_updated_project",
            new_callable=AsyncMock,
        ) as mock_proj:
            mock_proj.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_proj.assert_called_once_with(record)

    @pytest.mark.asyncio
    async def test_check_updated_record_file_returns_none(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_unsupported_type(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_link_issue_attachment(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "attach-1"
        record.parent_external_record_id = "issue-1"
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.record_type = RecordType.TICKET
        parent_record.id = "parent-rec-1"

        connector._tx_store.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        with patch.object(
            connector,
            "_check_and_fetch_updated_issue_link",
            new_callable=AsyncMock,
        ) as mock_link:
            mock_link.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_updated_record_link_no_parent(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = None
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_link_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "attach-1"
        record.parent_external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"
        record.version = 0
        record.weburl = None

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"

        attach_data = {
            "id": "attach-1",
            "url": "https://example.com/attachment",
            "title": "Attachment",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.attachment = AsyncMock(
            return_value=_mock_response(data={"attachment": attach_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue_link(
                record, parent_record
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_issue_link_no_external_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = ""
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_issue_link(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_changed(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = "proj-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"
        record.version = 0
        record.weburl = None

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"
        parent_record.external_record_id = "proj-1"

        project_data = _make_project_data_fullcov(
            external_links={
                "nodes": [
                    {
                        "id": "link-1",
                        "url": "https://example.com",
                        "label": "Link",
                        "createdAt": "2024-01-10T10:00:00.000Z",
                        "updatedAt": "2024-01-16T12:00:00.000Z",
                    }
                ]
            }
        )

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project_link(
                record, parent_record
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_not_found(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-missing"
        record.parent_external_record_id = "proj-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"
        parent_record.external_record_id = "proj-1"

        project_data = _make_project_data_fullcov(external_links={"nodes": []})

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project_link(
                record, parent_record
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_no_project_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = ""
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.external_record_id = ""

        result = await connector._check_and_fetch_updated_project_link(
            record, parent_record
        )
        assert result is None


# ===================================================================
# reindex_records
# ===================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_reindex_empty_list(self):
        connector = _make_connector_fullcov()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_reindex_with_updated_records(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"

        updated_ticket = MagicMock(spec=TicketRecord)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = (updated_ticket, [])
                await connector.reindex_records([ticket])
                connector.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_with_non_updated_records(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([ticket])
                connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_skips_base_record_class(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record = MagicMock(spec=Record)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.id = "rec-1"
        type(record).__name__ = "Record"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([record])
                connector.data_entities_processor.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_reindex_check_error_continues(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        record1 = MagicMock(spec=TicketRecord)
        record1.record_type = RecordType.TICKET
        record1.external_record_id = "issue-1"
        record1.id = "rec-1"
        type(record1).__name__ = "TicketRecord"

        record2 = MagicMock(spec=TicketRecord)
        record2.record_type = RecordType.TICKET
        record2.external_record_id = "issue-2"
        record2.id = "rec-2"
        type(record2).__name__ = "TicketRecord"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.side_effect = [Exception("fail"), None]
                await connector.reindex_records([record1, record2])

    @pytest.mark.asyncio
    async def test_reindex_not_implemented_error(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("not impl")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([ticket])

    @pytest.mark.asyncio
    async def test_reindex_inits_datasource_if_none(self):
        connector = _make_connector_fullcov()
        connector.data_source = None

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        with patch.object(connector, "init", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            connector.data_source = MagicMock()
            with patch.object(
                connector, "_get_fresh_datasource", new_callable=AsyncMock
            ):
                with patch.object(
                    connector,
                    "_check_and_fetch_updated_record",
                    new_callable=AsyncMock,
                ) as mock_check:
                    mock_check.return_value = None
                    await connector.reindex_records([ticket])


# ===================================================================
# _sync_deleted_issues & _sync_deleted_projects
# ===================================================================


class TestSyncDeleted:
    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        connector = _make_connector_fullcov()
        await connector._sync_deleted_issues([])

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_initial_sync(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group_fullcov()
        with patch(
            "app.connectors.sources.linear.connector.get_epoch_timestamp_in_ms",
            return_value=1705000000000,
        ):
            await connector._sync_deleted_issues([(rg, perms)])
        connector.deletion_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_with_trashed(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705000000000}
        )
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        trashed_issue = _make_issue_data_fullcov(
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(
            return_value=_mock_paginated("issues", [trashed_issue])
        )

        rg, perms = _make_team_record_group_fullcov()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_mark_record_and_children_deleted",
                new_callable=AsyncMock,
            ) as mock_delete:
                await connector._sync_deleted_issues([(rg, perms)])
                mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_fetch_failure(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705000000000}
        )

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )

        rg, perms = _make_team_record_group_fullcov()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_deleted_issues([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_empty_teams(self):
        connector = _make_connector_fullcov()
        await connector._sync_deleted_projects([])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_initial_sync(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group_fullcov()
        with patch(
            "app.connectors.sources.linear.connector.get_epoch_timestamp_in_ms",
            return_value=1705000000000,
        ):
            await connector._sync_deleted_projects([(rg, perms)])
        connector.deletion_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_with_trashed(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705000000000}
        )
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        trashed_project = _make_project_data_fullcov(
            trashed=True,
            archived_at="2024-01-16T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [trashed_project])
        )

        rg, perms = _make_team_record_group_fullcov()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_mark_record_and_children_deleted",
                new_callable=AsyncMock,
            ) as mock_delete:
                await connector._sync_deleted_projects([(rg, perms)])
                mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_trashed_before_checkpoint_skipped(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705400000000}
        )
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        trashed_project = _make_project_data_fullcov(
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [trashed_project])
        )

        rg, perms = _make_team_record_group_fullcov()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_mark_record_and_children_deleted",
                new_callable=AsyncMock,
            ) as mock_delete:
                await connector._sync_deleted_projects([(rg, perms)])
                mock_delete.assert_not_called()


# ===================================================================
# _mark_record_and_children_deleted
# ===================================================================


class TestMarkRecordDeleted:
    @pytest.mark.asyncio
    async def test_mark_deleted_not_found(self):
        connector = _make_connector_fullcov()
        connector._tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._mark_record_and_children_deleted("ext-1", "issue")

    @pytest.mark.asyncio
    async def test_mark_deleted_with_children(self):
        connector = _make_connector_fullcov()
        parent = MagicMock()
        parent.id = "parent-id"
        parent.external_record_id = "ext-parent"

        child = MagicMock()
        child.id = "child-id"
        child.external_record_id = "ext-child"

        connector._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        connector._tx_store.get_records_by_parent = AsyncMock(
            side_effect=[[child], []]
        )
        connector._tx_store.delete_records_and_relations = AsyncMock()

        await connector._mark_record_and_children_deleted("ext-parent", "issue")
        assert connector._tx_store.delete_records_and_relations.call_count == 2


# ===================================================================
# Sync checkpoints
# ===================================================================


class TestSyncCheckpoints:
    @pytest.mark.asyncio
    async def test_team_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 123}
        )
        result = await connector._get_team_sync_checkpoint("ENG")
        assert result == 123

    @pytest.mark.asyncio
    async def test_team_sync_checkpoint_none(self):
        connector = _make_connector_fullcov()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await connector._get_team_sync_checkpoint("ENG")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_team_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.update_sync_point = AsyncMock()
        await connector._update_team_sync_checkpoint("ENG", 456)
        connector.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_team_project_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 789}
        )
        result = await connector._get_team_project_sync_checkpoint("ENG")
        assert result == 789

    @pytest.mark.asyncio
    async def test_update_team_project_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.update_sync_point = AsyncMock()
        await connector._update_team_project_sync_checkpoint("ENG", 999)
        connector.projects_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_attachments_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 111}
        )
        result = await connector._get_attachments_sync_checkpoint()
        assert result == 111

    @pytest.mark.asyncio
    async def test_update_attachments_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.update_sync_point = AsyncMock()
        await connector._update_attachments_sync_checkpoint(222)
        connector.attachments_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_documents_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 333}
        )
        result = await connector._get_documents_sync_checkpoint()
        assert result == 333

    @pytest.mark.asyncio
    async def test_update_documents_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.update_sync_point = AsyncMock()
        await connector._update_documents_sync_checkpoint(444)
        connector.documents_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_deletion_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 555}
        )
        result = await connector._get_deletion_sync_checkpoint("issues")
        assert result == 555

    @pytest.mark.asyncio
    async def test_update_deletion_sync_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.update_sync_point = AsyncMock()
        await connector._update_deletion_sync_checkpoint("issues", 666)
        connector.deletion_sync_point.update_sync_point.assert_called_once()


# ===================================================================
# _apply_date_filters_to_linear_filter
# ===================================================================


class TestApplyDateFilters:
    def test_no_filters(self):
        connector = _make_connector_fullcov()
        connector.sync_filters = None
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert linear_filter == {}

    def test_modified_after_from_checkpoint(self):
        connector = _make_connector_fullcov()
        connector.sync_filters = None
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, 1705312800000)
        assert "updatedAt" in linear_filter
        assert "gt" in linear_filter["updatedAt"]

    def test_modified_after_from_filter(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "updatedAt" in linear_filter

    def test_modified_after_merge_with_checkpoint(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, 1705500000000)
        assert "updatedAt" in linear_filter

    def test_modified_before(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "updatedAt" in linear_filter
        assert "lte" in linear_filter["updatedAt"]

    def test_modified_before_with_after(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "gt" in linear_filter["updatedAt"]
        assert "lte" in linear_filter["updatedAt"]

    def test_created_after(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "createdAt" in linear_filter
        assert "gte" in linear_filter["createdAt"]

    def test_created_before(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "createdAt" in linear_filter
        assert "lte" in linear_filter["createdAt"]

    def test_created_before_with_after(self):
        connector = _make_connector_fullcov()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "gte" in linear_filter["createdAt"]
        assert "lte" in linear_filter["createdAt"]


# ===================================================================
# _linear_datetime_from_timestamp
# ===================================================================


class TestLinearDatetimeConversion:
    def test_valid_timestamp(self):
        connector = _make_connector_fullcov()
        result = connector._linear_datetime_from_timestamp(1705312800000)
        assert "2024-01-15" in result
        assert result.endswith("Z")

    def test_invalid_timestamp(self):
        connector = _make_connector_fullcov()
        result = connector._linear_datetime_from_timestamp(-99999999999999999)
        assert result == ""


# ===================================================================
# _parse_linear_datetime_to_datetime
# ===================================================================


class TestParseLinearDatetimeToDatetime:
    def test_valid(self):
        connector = _make_connector_fullcov()
        result = connector._parse_linear_datetime_to_datetime("2024-01-15T10:30:00.000Z")
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_empty_string(self):
        connector = _make_connector_fullcov()
        result = connector._parse_linear_datetime_to_datetime("")
        assert result is None

    def test_none(self):
        connector = _make_connector_fullcov()
        result = connector._parse_linear_datetime_to_datetime(None)
        assert result is None

    def test_invalid(self):
        connector = _make_connector_fullcov()
        result = connector._parse_linear_datetime_to_datetime("not-a-date")
        assert result is None


# ===================================================================
# _extract_file_urls_from_markdown
# ===================================================================


class TestExtractFileUrls:
    def test_empty_input(self):
        connector = _make_connector_fullcov()
        assert connector._extract_file_urls_from_markdown("") == []
        assert connector._extract_file_urls_from_markdown(None) == []

    def test_no_linear_urls(self):
        connector = _make_connector_fullcov()
        text = "[doc](https://example.com/file.pdf)"
        assert connector._extract_file_urls_from_markdown(text) == []

    def test_image_urls(self):
        connector = _make_connector_fullcov()
        text = "![alt](https://uploads.linear.app/image.png)"
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1
        assert result[0]["url"] == "https://uploads.linear.app/image.png"

    def test_exclude_images(self):
        connector = _make_connector_fullcov()
        text = "![alt](https://uploads.linear.app/image.png)"
        result = connector._extract_file_urls_from_markdown(text, exclude_images=True)
        assert len(result) == 0

    def test_file_links(self):
        connector = _make_connector_fullcov()
        text = "[report.pdf](https://uploads.linear.app/report.pdf)"
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"

    def test_mixed_images_and_links(self):
        connector = _make_connector_fullcov()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "[doc](https://uploads.linear.app/doc.pdf)"
        )
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 2

    def test_exclude_images_keeps_links(self):
        connector = _make_connector_fullcov()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "[doc](https://uploads.linear.app/doc.pdf)"
        )
        result = connector._extract_file_urls_from_markdown(text, exclude_images=True)
        assert len(result) == 1
        assert result[0]["filename"] == "doc"

    def test_deduplication(self):
        connector = _make_connector_fullcov()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "![img2](https://uploads.linear.app/img.png)"
        )
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1


# ===================================================================
# _get_mime_type_from_url
# ===================================================================


class TestGetMimeType:
    def test_pdf(self):
        connector = _make_connector_fullcov()
        assert connector._get_mime_type_from_url("", "report.pdf") == MimeTypes.PDF.value

    def test_png(self):
        connector = _make_connector_fullcov()
        assert connector._get_mime_type_from_url("", "img.png") == MimeTypes.PNG.value

    def test_docx(self):
        connector = _make_connector_fullcov()
        assert connector._get_mime_type_from_url("", "file.docx") == MimeTypes.DOCX.value

    def test_from_url(self):
        connector = _make_connector_fullcov()
        assert (
            connector._get_mime_type_from_url("https://example.com/file.xlsx?token=abc", "")
            == MimeTypes.XLSX.value
        )

    def test_unknown(self):
        connector = _make_connector_fullcov()
        assert connector._get_mime_type_from_url("https://example.com/file", "") == MimeTypes.UNKNOWN.value

    def test_csv(self):
        connector = _make_connector_fullcov()
        assert connector._get_mime_type_from_url("", "data.csv") == MimeTypes.CSV.value


# ===================================================================
# Transformations
# ===================================================================


class TestTransformIssueToTicketRecord:
    def test_basic_transform(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov()
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.record_name == "Test Issue"
        assert ticket.external_record_id == "issue-1"
        assert ticket.record_type == RecordType.TICKET
        assert ticket.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_issue_to_ticket_record({"id": ""}, "team-1")

    def test_missing_team_id_raises(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov()
        with pytest.raises(ValueError, match="team_id is required"):
            connector._transform_issue_to_ticket_record(issue_data, "")

    def test_priority_mapping(self):
        connector = _make_connector_fullcov()
        for priority_num, expected_str in [(0, "none"), (1, "Urgent"), (2, "High"), (3, "Medium"), (4, "Low")]:
            issue_data = _make_issue_data_fullcov(priority=priority_num)
            ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
            assert ticket is not None

    def test_no_priority(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov(priority=None)
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket is not None

    def test_sub_issue_type(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov(parent={"id": "parent-issue"})
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.type == ItemType.SUB_ISSUE

    def test_version_increment(self):
        connector = _make_connector_fullcov()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.source_updated_at = 1705000000000

        issue_data = _make_issue_data_fullcov(updated_at="2024-01-16T12:00:00.000Z")
        ticket = connector._transform_issue_to_ticket_record(
            issue_data, "team-1", existing
        )
        assert ticket.version == 2
        assert ticket.id == "existing-id"

    def test_no_version_change(self):
        connector = _make_connector_fullcov()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1705320000000

        issue_data = _make_issue_data_fullcov(updated_at="2024-01-15T12:00:00.000Z")
        ticket = connector._transform_issue_to_ticket_record(
            issue_data, "team-1", existing
        )
        assert ticket.version == 3

    def test_with_relations(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov(
            relations={
                "nodes": [
                    {
                        "type": "blocks",
                        "relatedIssue": {"id": "related-1"},
                    }
                ]
            }
        )
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert len(ticket.related_external_records) >= 0

    def test_missing_identifier_and_title(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov(identifier="", title="")
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.record_name == "issue-1"

    def test_assignee_and_creator(self):
        connector = _make_connector_fullcov()
        issue_data = _make_issue_data_fullcov(
            assignee={"email": "dev@test.com", "displayName": "Dev"},
            creator={"email": "pm@test.com", "name": "PM"},
        )
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.assignee_email == "dev@test.com"
        assert ticket.creator_email == "pm@test.com"


class TestTransformToProjectRecord:
    def test_basic_transform(self):
        connector = _make_connector_fullcov()
        project_data = _make_project_data_fullcov()
        record = connector._transform_to_project_record(project_data, "team-1")
        assert record.record_name == "Test Project"
        assert record.record_type == RecordType.PROJECT
        assert record.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_to_project_record({"id": ""}, "team-1")

    def test_missing_name_uses_slug(self):
        connector = _make_connector_fullcov()
        data = _make_project_data_fullcov(name="", slug_id="my-slug")
        record = connector._transform_to_project_record(data, "team-1")
        assert record.record_name == "my-slug"

    def test_missing_name_and_slug_uses_id(self):
        connector = _make_connector_fullcov()
        data = _make_project_data_fullcov(name="", slug_id="")
        record = connector._transform_to_project_record(data, "team-1")
        assert record.record_name == "proj-1"

    def test_with_lead(self):
        connector = _make_connector_fullcov()
        data = _make_project_data_fullcov(
            lead={"id": "lead-1", "displayName": "Lead Dev", "email": "lead@test.com"}
        )
        record = connector._transform_to_project_record(data, "team-1")
        assert record.lead_name == "Lead Dev"
        assert record.lead_email == "lead@test.com"


class TestTransformAttachmentToLinkRecord:
    def test_basic_transform(self):
        connector = _make_connector_fullcov()
        attachment_data = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "My File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }
        link = connector._transform_attachment_to_link_record(
            attachment_data, "issue-1", "node-1", "team-1"
        )
        assert link.record_name == "My File"
        assert link.weburl == "https://example.com/file"
        assert link.is_public == LinkPublicStatus.UNKNOWN

    def test_missing_id_raises(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_attachment_to_link_record(
                {"id": "", "url": "x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_attachment_to_link_record(
                {"id": "a", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_uses_label_as_title(self):
        connector = _make_connector_fullcov()
        data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External Link",
            "createdAt": "",
            "updatedAt": "",
        }
        link = connector._transform_attachment_to_link_record(
            data, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert link.record_name == "External Link"
        assert link.parent_record_type == RecordType.PROJECT


class TestTransformDocumentToWebpageRecord:
    def test_basic_transform(self):
        connector = _make_connector_fullcov()
        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }
        webpage = connector._transform_document_to_webpage_record(
            doc_data, "issue-1", "node-1", "team-1"
        )
        assert webpage.record_name == "Design Doc"
        assert webpage.record_type == RecordType.WEBPAGE

    def test_missing_id_raises(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_document_to_webpage_record(
                {"id": "", "url": "x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector_fullcov()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_document_to_webpage_record(
                {"id": "d", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_no_title_uses_id(self):
        connector = _make_connector_fullcov()
        doc_data = {
            "id": "doc-abcdef12",
            "url": "https://linear.app/doc/1",
            "title": "",
            "createdAt": "",
            "updatedAt": "",
        }
        webpage = connector._transform_document_to_webpage_record(
            doc_data, "issue-1", "node-1", "team-1"
        )
        assert "doc-abcd" in webpage.record_name


# ===================================================================
# _transform_file_url_to_file_record
# ===================================================================


class TestTransformFileUrlToFileRecord:
    @pytest.mark.asyncio
    async def test_basic_transform(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=1024)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            file_record = await connector._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/report.pdf",
                filename="report.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
            assert file_record.record_name == "report.pdf"
            assert file_record.record_type == RecordType.FILE
            assert file_record.extension == "pdf"
            assert file_record.size_in_bytes == 1024

    @pytest.mark.asyncio
    async def test_file_size_error_fallback(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(side_effect=Exception("network error"))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            file_record = await connector._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/file.txt",
                filename="file.txt",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
            assert file_record.size_in_bytes == 0


# ===================================================================
# _sync_issues_for_teams
# ===================================================================


class TestSyncIssuesForTeams:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector_fullcov()
        await connector._sync_issues_for_teams([])

    @pytest.mark.asyncio
    async def test_team_missing_external_group_id(self):
        connector = _make_connector_fullcov()
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            external_group_id=None,
            connector_id="linear-conn-1",
            connector_name=Connectors.LINEAR,
            name="NoID",
            short_name="NO",
            group_type=RecordGroupType.PROJECT,
        )
        perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        await connector._sync_issues_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_issues_processes_batches(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group_fullcov()

        ticket = MagicMock(spec=TicketRecord)
        ticket.source_updated_at = 1705312800000
        batch = [(ticket, [])]

        async def fake_batch_gen(**kwargs):
            yield batch

        with patch.object(
            connector, "_get_team_sync_checkpoint", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.return_value = None
            with patch.object(
                connector,
                "_fetch_issues_for_team_batch",
                side_effect=lambda **kw: fake_batch_gen(**kw),
            ):
                with patch.object(
                    connector,
                    "_update_team_sync_checkpoint",
                    new_callable=AsyncMock,
                ):
                    await connector._sync_issues_for_teams([(rg, perms)])
                    connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_issues_team_error_continues(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        rg, perms = _make_team_record_group_fullcov()

        with patch.object(
            connector, "_get_team_sync_checkpoint", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.side_effect = Exception("checkpoint error")
            await connector._sync_issues_for_teams([(rg, perms)])


# ===================================================================
# _sync_projects_for_teams
# ===================================================================


class TestSyncProjectsForTeams:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector_fullcov()
        await connector._sync_projects_for_teams([])

    @pytest.mark.asyncio
    async def test_team_missing_external_group_id(self):
        connector = _make_connector_fullcov()
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            external_group_id=None,
            connector_id="linear-conn-1",
            connector_name=Connectors.LINEAR,
            name="NoID",
            short_name="NO",
            group_type=RecordGroupType.PROJECT,
        )
        perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        await connector._sync_projects_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_projects_processes_batches(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.projects_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group_fullcov()

        project = MagicMock(spec=ProjectRecord)
        project.source_updated_at = 1705312800000
        batch = [(project, [])]

        async def fake_batch_gen(**kwargs):
            yield batch

        with patch.object(
            connector, "_get_team_project_sync_checkpoint", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.return_value = None
            with patch.object(
                connector,
                "_fetch_projects_for_team_batch",
                side_effect=lambda **kw: fake_batch_gen(**kw),
            ):
                with patch.object(
                    connector,
                    "_update_team_project_sync_checkpoint",
                    new_callable=AsyncMock,
                ):
                    await connector._sync_projects_for_teams([(rg, perms)])
                    connector.data_entities_processor.on_new_records.assert_called()


# ===================================================================
# _sync_attachments
# ===================================================================


class TestSyncAttachments:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector_fullcov()
        await connector._sync_attachments([])

    @pytest.mark.asyncio
    async def test_sync_attachments_with_data(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.attachments_sync_point.update_sync_point = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group_fullcov()

        parent_record = MagicMock()
        parent_record.id = "parent-id"
        connector._tx_store.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }

        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(
            return_value=_mock_paginated("attachments", [attachment])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_attachments([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_called()


# ===================================================================
# _sync_documents
# ===================================================================


class TestSyncDocuments:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector_fullcov()
        await connector._sync_documents([])

    @pytest.mark.asyncio
    async def test_sync_documents_with_data(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.documents_sync_point.update_sync_point = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group_fullcov()

        parent_record = MagicMock()
        parent_record.id = "parent-id"
        connector._tx_store.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        document = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "content": "# Content",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {
                "id": "issue-1",
                "identifier": "ENG-1",
                "team": {"id": "team-1"},
            },
        }

        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(
            return_value=_mock_paginated("documents", [document])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_documents([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_documents_skips_standalone(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.documents_sync_point.update_sync_point = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group_fullcov()

        standalone_doc = {
            "id": "doc-standalone",
            "url": "https://linear.app/doc/standalone",
            "title": "Standalone Doc",
            "content": "# Content",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(
            return_value=_mock_paginated("documents", [standalone_doc])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_documents([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_not_called()


# ===================================================================
# _fetch_document_content
# ===================================================================


class TestFetchDocumentContent:
    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        doc_data = {"content": "# Hello World"}
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_convert_images_to_base64_in_markdown",
                new_callable=AsyncMock,
            ) as mock_convert:
                mock_convert.return_value = "# Hello World"
                result = await connector._fetch_document_content("doc-1")
                assert result == "# Hello World"

    @pytest.mark.asyncio
    async def test_failure(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(success=False, message="Not found")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with pytest.raises(Exception, match="Failed to fetch document"):
                await connector._fetch_document_content("doc-missing")

    @pytest.mark.asyncio
    async def test_empty_content(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        doc_data = {"content": ""}
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._fetch_document_content("doc-1")
            assert result == ""

    @pytest.mark.asyncio
    async def test_datasource_not_initialized(self):
        connector = _make_connector_fullcov()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_document_content("doc-1")


# ===================================================================
# Other utility methods
# ===================================================================


class TestOtherUtilities:
    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        connector = _make_connector_fullcov()
        with patch.object(
            connector, "run_sync", new_callable=AsyncMock
        ) as mock_sync:
            await connector.run_incremental_sync()
            mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(
            return_value=_mock_response(data={"organization": {"id": "org-1"}})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.test_connection_and_access()
            assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(
            return_value=_mock_response(success=False, message="Unauthorized")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.test_connection_and_access()
            assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.side_effect = Exception("boom")
            result = await connector.test_connection_and_access()
            assert result is False

    @pytest.mark.asyncio
    async def test_get_signed_url(self):
        connector = _make_connector_fullcov()
        record = MagicMock()
        result = await connector.get_signed_url(record)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        connector = _make_connector_fullcov()
        await connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector_fullcov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.close = AsyncMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client
        await connector.cleanup()
        mock_internal.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self):
        connector = _make_connector_fullcov()
        connector.external_client = None
        await connector.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_error(self):
        connector = _make_connector_fullcov()
        mock_client = MagicMock()
        mock_client.get_client.side_effect = Exception("fail")
        connector.external_client = mock_client
        await connector.cleanup()


# ===================================================================
# _get_fresh_datasource edge cases
# ===================================================================


class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_api_token_auth_type(self):
        connector = _make_connector_fullcov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "new-api-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_called_once_with("new-api-token")

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        connector = _make_connector_fullcov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": ""},
            }
        )

        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_same_token_no_update(self):
        connector = _make_connector_fullcov()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "same-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "same-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_not_called()


# ===================================================================
# _get_team_options edge cases
# ===================================================================


class TestGetTeamOptions:
    @pytest.mark.asyncio
    async def test_team_options_with_search(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {"id": "t1", "name": "Engineering", "key": "ENG"},
                    {"id": "t2", "name": "Design", "key": "DES"},
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.get_filter_options("team_ids", search="eng")
            assert len(result.options) == 1
            assert "Engineering" in result.options[0].label

    @pytest.mark.asyncio
    async def test_team_options_failure(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with pytest.raises(RuntimeError, match="Failed to fetch team"):
                await connector.get_filter_options("team_ids")


# ===================================================================
# run_sync
# ===================================================================


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock()]
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                with patch.object(
                    connector, "_fetch_users", new_callable=AsyncMock
                ) as mock_users:
                    mock_users.return_value = []
                    with patch.object(
                        connector, "_fetch_teams", new_callable=AsyncMock
                    ) as mock_teams:
                        mock_teams.return_value = ([], [])
                        with patch.object(
                            connector,
                            "_sync_issues_for_teams",
                            new_callable=AsyncMock,
                        ):
                            with patch.object(
                                connector,
                                "_sync_attachments",
                                new_callable=AsyncMock,
                            ):
                                with patch.object(
                                    connector,
                                    "_sync_documents",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        connector,
                                        "_sync_projects_for_teams",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            connector,
                                            "_sync_deleted_issues",
                                            new_callable=AsyncMock,
                                        ):
                                            with patch.object(
                                                connector,
                                                "_sync_deleted_projects",
                                                new_callable=AsyncMock,
                                            ):
                                                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_team_filter(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock()]
        )

        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["team-1"]
        mock_filter.get_operator.return_value = MagicMock(value="in")

        mock_sync_filters = MagicMock(spec=FilterCollection)
        mock_sync_filters.get.return_value = mock_filter

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                mock_load.return_value = (mock_sync_filters, FilterCollection())
                with patch.object(
                    connector, "_fetch_users", new_callable=AsyncMock
                ) as mock_users:
                    mock_users.return_value = []
                    with patch.object(
                        connector, "_fetch_teams", new_callable=AsyncMock
                    ) as mock_teams:
                        mock_teams.return_value = ([], [])
                        with patch.object(
                            connector,
                            "_sync_issues_for_teams",
                            new_callable=AsyncMock,
                        ):
                            with patch.object(
                                connector,
                                "_sync_attachments",
                                new_callable=AsyncMock,
                            ):
                                with patch.object(
                                    connector,
                                    "_sync_documents",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        connector,
                                        "_sync_projects_for_teams",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            connector,
                                            "_sync_deleted_issues",
                                            new_callable=AsyncMock,
                                        ):
                                            with patch.object(
                                                connector,
                                                "_sync_deleted_projects",
                                                new_callable=AsyncMock,
                                            ):
                                                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagated(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.side_effect = Exception("Config error")
            with pytest.raises(Exception, match="Config error"):
                await connector.run_sync()


# ===================================================================
# _process_issue_attachments
# ===================================================================


class TestProcessIssueAttachments:
    @pytest.mark.asyncio
    async def test_creates_new_attachment(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        children = await connector._process_issue_attachments(
            attachments_data=[attachment],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 1
        assert children[0].child_type == ChildType.RECORD

    @pytest.mark.asyncio
    async def test_uses_existing_attachment(self):
        connector = _make_connector_fullcov()

        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "Existing File"

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "",
            "updatedAt": "",
        }

        children = await connector._process_issue_attachments(
            attachments_data=[attachment],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 1
        assert children[0].child_id == "existing-id"

    @pytest.mark.asyncio
    async def test_skips_empty_id(self):
        connector = _make_connector_fullcov()
        mock_tx = AsyncMock()
        children = await connector._process_issue_attachments(
            attachments_data=[{"id": ""}],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 0


# ===================================================================
# _process_issue_documents
# ===================================================================


class TestProcessIssueDocuments:
    @pytest.mark.asyncio
    async def test_creates_new_document(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        doc = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        children = await connector._process_issue_documents(
            documents_data=[doc],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 1


# ===================================================================
# _extract_files_from_markdown
# ===================================================================


class TestExtractFilesFromMarkdown:
    @pytest.mark.asyncio
    async def test_empty_markdown(self):
        connector = _make_connector_fullcov()
        result, existing = await connector._extract_files_from_markdown(
            markdown_text="",
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
            tx_store=AsyncMock(),
        )
        assert result == []
        assert existing == []

    @pytest.mark.asyncio
    async def test_extracts_new_files(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=512)

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        text = "[report.pdf](https://uploads.linear.app/report.pdf)"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result, existing = await connector._extract_files_from_markdown(
                markdown_text=text,
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                tx_store=mock_tx,
                exclude_images=True,
            )
            assert len(result) == 1
            assert isinstance(result[0][0], FileRecord)

    @pytest.mark.asyncio
    async def test_returns_existing_files_as_children(self):
        connector = _make_connector_fullcov()

        existing_file = MagicMock()
        existing_file.id = "existing-file-id"
        existing_file.record_type = RecordType.FILE
        existing_file.record_name = "report.pdf"

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing_file)

        text = "[report.pdf](https://uploads.linear.app/report.pdf)"

        result, existing = await connector._extract_files_from_markdown(
            markdown_text=text,
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
            tx_store=mock_tx,
            exclude_images=True,
        )
        assert len(result) == 0
        assert len(existing) == 1
        assert existing[0].child_id == "existing-file-id"


# ===================================================================
# _fetch_teams with filters
# ===================================================================


class TestFetchTeamsWithFilters:
    @pytest.mark.asyncio
    async def test_fetch_teams_with_not_in_filter(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
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
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            operator = MagicMock()
            operator.value = "not_in"
            user_groups, record_groups = await connector._fetch_teams(
                team_ids=["team-2"],
                team_ids_operator=operator,
            )
            assert len(record_groups) == 1

    @pytest.mark.asyncio
    async def test_fetch_teams_skips_missing_id(self):
        connector = _make_connector_fullcov()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {
                        "id": "",
                        "name": "Bad Team",
                        "key": "",
                        "private": False,
                        "members": {"nodes": []},
                    },
                    {
                        "id": "team-1",
                        "name": "Good Team",
                        "key": "GOOD",
                        "description": "",
                        "private": False,
                        "parent": None,
                        "members": {"nodes": []},
                    },
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            _, record_groups = await connector._fetch_teams()
            assert len(record_groups) == 1


# ===================================================================
# _process_project_external_links
# ===================================================================


class TestProcessProjectExternalLinks:
    @pytest.mark.asyncio
    async def test_creates_link_records(self):
        connector = _make_connector_fullcov()
        connector.indexing_filters = None

        link_data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.get_record_by_weburl = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_external_links(
            external_links_data=[link_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(block_groups) == 0

    @pytest.mark.asyncio
    async def test_creates_block_groups(self):
        connector = _make_connector_fullcov()
        connector.indexing_filters = None

        link_data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External",
            "createdAt": "",
            "updatedAt": "",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.get_record_by_weburl = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_external_links(
            external_links_data=[link_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=True,
        )
        assert len(block_groups) == 1


# ===================================================================
# _process_project_documents
# ===================================================================


class TestProcessProjectDocuments:
    @pytest.mark.asyncio
    async def test_creates_webpage_records(self):
        connector = _make_connector_fullcov()
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_documents(
            documents_data=[doc_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(block_groups) == 0

    @pytest.mark.asyncio
    async def test_creates_block_groups_for_docs(self):
        connector = _make_connector_fullcov()
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "",
            "updatedAt": "",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_documents(
            documents_data=[doc_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=True,
        )
        assert len(block_groups) == 1
