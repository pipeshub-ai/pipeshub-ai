"""Comprehensive workflow tests for Linear connector.

These tests exercise full sync workflows covering initialization, user/team sync,
issue sync, attachment/document sync, project sync, deletion sync, file extraction,
and error handling. Each test covers many code paths to maximize statement coverage.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus, RecordRelations
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    LinearConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    LinkRecord,
    MimeTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector():
    """Build a LinearConnector with all dependencies mocked."""
    logger = logging.getLogger("test.linear.workflow")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="alice@test.com"),
        MagicMock(email="bob@test.com"),
    ])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_new_app_roles = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)
    mock_tx_store.delete_records_and_relations = AsyncMock()
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])

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
    return connector, data_entities_processor, data_store_provider, config_service, mock_tx_store


def _mock_response(success=True, data=None, message=""):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.message = message
    return resp


def _mock_paginated_response(nodes, has_next=False, end_cursor=None):
    return _mock_response(data={
        list(nodes.keys())[0]: {
            "nodes": list(nodes.values())[0],
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        }
    })


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestLinearInit:

    @pytest.mark.asyncio
    async def test_init_success(self):
        connector, *_ = _make_connector()
        with patch("app.connectors.sources.linear.connector.LinearClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = MagicMock()
            org_resp = _mock_response(data={
                "organization": {"id": "org-1", "name": "TestOrg", "urlKey": "testorg"}
            })
            mock_ds.organization = AsyncMock(return_value=org_resp)

            with patch("app.connectors.sources.linear.connector.LinearDataSource", return_value=mock_ds):
                result = await connector.init()
                assert result is True
                assert connector.organization_name == "TestOrg"
                assert connector.organization_url_key == "testorg"

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector, *_ = _make_connector()
        with patch("app.connectors.sources.linear.connector.LinearClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(side_effect=Exception("Auth failed"))
            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_init_org_failure(self):
        connector, *_ = _make_connector()
        with patch("app.connectors.sources.linear.connector.LinearClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(return_value=MagicMock())
            mock_ds = MagicMock()
            mock_ds.organization = AsyncMock(return_value=_mock_response(success=False, message="fail"))
            with patch("app.connectors.sources.linear.connector.LinearDataSource", return_value=mock_ds):
                result = await connector.init()
                assert result is False

    @pytest.mark.asyncio
    async def test_init_empty_org_data(self):
        connector, *_ = _make_connector()
        with patch("app.connectors.sources.linear.connector.LinearClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(return_value=MagicMock())
            mock_ds = MagicMock()
            mock_ds.organization = AsyncMock(return_value=_mock_response(data={"organization": {}}))
            with patch("app.connectors.sources.linear.connector.LinearDataSource", return_value=mock_ds):
                result = await connector.init()
                assert result is False


# ---------------------------------------------------------------------------
# Get Fresh Datasource
# ---------------------------------------------------------------------------


class TestGetFreshDatasource:

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_oauth(self):
        connector, _, _, cs, _ = _make_connector()
        mock_client = MagicMock()
        internal = MagicMock()
        internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = internal
        connector.external_client = mock_client

        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "new-token"},
        })

        with patch("app.connectors.sources.linear.connector.LinearDataSource") as mock_ds:
            await connector._get_fresh_datasource()
            internal.set_token.assert_called_with("new-token")

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_api_token(self):
        connector, _, _, cs, _ = _make_connector()
        mock_client = MagicMock()
        internal = MagicMock()
        internal.get_token.return_value = "token"
        mock_client.get_client.return_value = internal
        connector.external_client = mock_client

        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": "token"},
        })

        with patch("app.connectors.sources.linear.connector.LinearDataSource"):
            await connector._get_fresh_datasource()
            internal.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_no_client_raises(self):
        connector, *_ = _make_connector()
        connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_no_config_raises(self):
        connector, _, _, cs, _ = _make_connector()
        connector.external_client = MagicMock()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_no_token_raises(self):
        connector, _, _, cs, _ = _make_connector()
        connector.external_client = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {},
        })
        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()


# ---------------------------------------------------------------------------
# Fetch Users
# ---------------------------------------------------------------------------


class TestFetchUsers:

    @pytest.mark.asyncio
    async def test_fetch_users_pagination(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        users_page1 = [
            {"id": "u1", "email": "alice@test.com", "name": "Alice", "active": True, "updatedAt": "2024-01-01T00:00:00Z"},
            {"id": "u2", "email": "bob@test.com", "name": "Bob", "active": True},
        ]
        users_page2 = [
            {"id": "u3", "email": None, "name": "NoEmail", "active": True},  # skipped
            {"id": "u4", "email": "inactive@test.com", "name": "Inactive", "active": False},  # skipped
        ]

        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(side_effect=[
            _mock_paginated_response({"users": users_page1}, has_next=True, end_cursor="c1"),
            _mock_paginated_response({"users": users_page2}, has_next=False),
        ])

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector._fetch_users()
            assert len(result) == 2  # Alice and Bob
            assert all(isinstance(u, AppUser) for u in result)

    @pytest.mark.asyncio
    async def test_fetch_users_no_datasource_raises(self):
        connector, *_ = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_users()


# ---------------------------------------------------------------------------
# Fetch Teams
# ---------------------------------------------------------------------------


class TestFetchTeams:

    @pytest.mark.asyncio
    async def test_fetch_teams_with_filters_and_members(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "myorg"

        teams = [{
            "id": "team-1",
            "name": "Engineering",
            "key": "ENG",
            "description": "The engineering team",
            "private": True,
            "parent": {"id": "parent-team-1", "name": "Parent"},
            "members": {"nodes": [
                {"email": "alice@test.com"},
                {"email": "unknown@test.com"},
            ]}
        }, {
            "id": "team-2",
            "name": "Marketing",
            "key": "MKT",
            "description": None,
            "private": False,
            "parent": None,
            "members": {"nodes": []}
        }]

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_paginated_response({"teams": teams}))

        user_map = {"alice@test.com": MagicMock(email="alice@test.com")}

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            user_groups, record_groups = await connector._fetch_teams(
                team_ids=["team-1", "team-2"],
                team_ids_operator=None,
                user_email_map=user_map,
            )

            assert len(user_groups) == 2
            assert len(record_groups) == 2

            # ENG team is private -> GROUP permission
            _, eng_perms = record_groups[0]
            assert eng_perms[0].entity_type == EntityType.GROUP

            # MKT team is public -> ORG permission
            _, mkt_perms = record_groups[1]
            assert mkt_perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_fetch_teams_exclude_filter(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_paginated_response({"teams": []}))

        op = MagicMock()
        op.value = "not_in"

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            user_groups, record_groups = await connector._fetch_teams(
                team_ids=["team-x"],
                team_ids_operator=op,
            )
            # Verify the filter was built with "nin"
            call_args = mock_ds.teams.call_args
            assert call_args.kwargs.get("filter") == {"id": {"nin": ["team-x"]}}


# ---------------------------------------------------------------------------
# Transform Issue to TicketRecord
# ---------------------------------------------------------------------------


class TestTransformIssue:

    def test_transform_basic_issue(self):
        connector, *_ = _make_connector()
        connector.organization_url_key = "myorg"

        issue = {
            "id": "issue-1",
            "identifier": "ENG-123",
            "title": "Fix bug",
            "priority": 2,
            "state": {"name": "In Progress", "type": "started"},
            "labels": {"nodes": [{"name": "Bug"}]},
            "assignee": {"email": "alice@test.com", "name": "Alice"},
            "creator": {"email": "bob@test.com", "name": "Bob"},
            "parent": None,
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-06-01T00:00:00.000Z",
            "url": "https://linear.app/myorg/issue/ENG-123",
            "description": "",
            "comments": {"nodes": []},
            "relations": {"nodes": [
                {
                    "type": "blocks",
                    "relatedIssue": {"id": "issue-2"},
                }
            ]},
        }

        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert isinstance(ticket, TicketRecord)
        assert ticket.record_name == "Fix bug"
        assert ticket.external_record_id == "issue-1"
        assert ticket.external_record_group_id == "team-1"
        assert ticket.version == 0
        assert ticket.weburl == "https://linear.app/myorg/issue/ENG-123"
        assert ticket.assignee_email == "alice@test.com"
        assert ticket.creator_email == "bob@test.com"

    def test_transform_issue_with_existing_record(self):
        connector, *_ = _make_connector()

        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1000  # different from issue

        issue = {
            "id": "issue-2",
            "identifier": "ENG-456",
            "title": "Updated",
            "priority": None,
            "state": None,
            "labels": {},
            "assignee": None,
            "creator": None,
            "parent": {"id": "parent-issue"},
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-06-01T00:00:00.000Z",
            "url": None,
            "relations": None,
        }

        ticket = connector._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.id == "existing-id"
        assert ticket.version == 4
        assert ticket.parent_external_record_id == "parent-issue"

    def test_transform_issue_missing_id_raises(self):
        connector, *_ = _make_connector()
        with pytest.raises(ValueError, match="missing required"):
            connector._transform_issue_to_ticket_record({"id": ""}, "team-1")

    def test_transform_issue_missing_team_raises(self):
        connector, *_ = _make_connector()
        with pytest.raises(ValueError, match="team_id is required"):
            connector._transform_issue_to_ticket_record({"id": "x"}, "")

    def test_transform_issue_priority_mapping(self):
        connector, *_ = _make_connector()
        base = {"id": "i", "identifier": "X-1", "title": "T", "state": None, "labels": {},
                "assignee": None, "creator": None, "parent": None, "createdAt": "", "updatedAt": "", "url": None, "relations": None}

        for p in [0, 1, 2, 3, 4]:
            issue = {**base, "priority": p}
            ticket = connector._transform_issue_to_ticket_record(issue, "t")
            assert ticket is not None


# ---------------------------------------------------------------------------
# Full Sync Workflow
# ---------------------------------------------------------------------------


class TestLinearRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_full_workflow(self):
        """Test the entire run_sync method end-to-end."""
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        # Mock filter loading
        with patch("app.connectors.sources.linear.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):

            # Mock _fetch_users
            users = [AppUser(
                app_name=Connectors.LINEAR, connector_id="linear-conn-1",
                source_user_id="u1", org_id="org-1", email="alice@test.com",
                full_name="Alice", is_active=True
            )]
            connector._fetch_users = AsyncMock(return_value=users)

            # Mock _fetch_teams
            team_rg = RecordGroup(
                id=str(uuid4()), org_id="org-1", external_group_id="team-1",
                connector_id="linear-conn-1", connector_name=Connectors.LINEAR,
                name="Engineering", short_name="ENG", group_type=RecordGroupType.PROJECT,
            )
            team_ug = AppUserGroup(
                id=str(uuid4()), org_id="org-1", source_user_group_id="team-1",
                connector_id="linear-conn-1", app_name=Connectors.LINEAR, name="Engineering",
            )
            team_perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]

            connector._fetch_teams = AsyncMock(return_value=(
                [(team_ug, users)],
                [(team_rg, team_perms)],
            ))

            # Mock issue/attachment/document/project/deletion syncs
            connector._sync_issues_for_teams = AsyncMock()
            connector._sync_attachments = AsyncMock()
            connector._sync_documents = AsyncMock()
            connector._sync_projects_for_teams = AsyncMock()
            connector._sync_deleted_issues = AsyncMock()
            connector._sync_deleted_projects = AsyncMock()

            await connector.run_sync()

            dep.on_new_app_users.assert_called_once_with(users)
            dep.on_new_user_groups.assert_called_once()
            dep.on_new_record_groups.assert_called_once()
            connector._sync_issues_for_teams.assert_called_once()
            connector._sync_attachments.assert_called_once()
            connector._sync_documents.assert_called_once()
            connector._sync_projects_for_teams.assert_called_once()
            connector._sync_deleted_issues.assert_called_once()
            connector._sync_deleted_projects.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_no_users_returns_early(self):
        connector, dep, *_ = _make_connector()
        dep.get_all_active_users = AsyncMock(return_value=[])
        connector.data_source = MagicMock()

        with patch("app.connectors.sources.linear.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            await connector.run_sync()
            dep.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_sync_inits_if_no_datasource(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = None

        with patch("app.connectors.sources.linear.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            connector.init = AsyncMock()
            connector._fetch_users = AsyncMock(return_value=[])
            connector._fetch_teams = AsyncMock(return_value=([], []))
            connector._sync_issues_for_teams = AsyncMock()
            connector._sync_attachments = AsyncMock()
            connector._sync_documents = AsyncMock()
            connector._sync_projects_for_teams = AsyncMock()
            connector._sync_deleted_issues = AsyncMock()
            connector._sync_deleted_projects = AsyncMock()

            await connector.run_sync()
            connector.init.assert_called_once()


# ---------------------------------------------------------------------------
# Issue Sync for Teams
# ---------------------------------------------------------------------------


class TestSyncIssuesForTeams:

    @pytest.mark.asyncio
    async def test_sync_issues_processes_batches(self):
        connector, dep, *_ = _make_connector()
        connector.indexing_filters = FilterCollection()
        connector.sync_filters = FilterCollection()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"
        team_rg.short_name = "ENG"
        team_rg.name = "Engineering"

        # Mock the team sync checkpoint
        connector._get_team_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_sync_checkpoint = AsyncMock()

        # Create mock ticket
        mock_ticket = MagicMock(spec=TicketRecord)
        mock_ticket.source_updated_at = 1000

        async def fake_fetch(*args, **kwargs):
            yield [(mock_ticket, [])]

        connector._fetch_issues_for_team_batch = fake_fetch

        await connector._sync_issues_for_teams([(team_rg, [])])
        dep.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_issues_empty_teams(self):
        connector, dep, *_ = _make_connector()
        await connector._sync_issues_for_teams([])
        dep.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_issues_error_continues(self):
        connector, dep, *_ = _make_connector()
        connector._get_team_sync_checkpoint = AsyncMock(side_effect=Exception("db error"))

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"
        team_rg.short_name = "ENG"
        team_rg.name = "Engineering"

        # Should not raise
        await connector._sync_issues_for_teams([(team_rg, [])])


# ---------------------------------------------------------------------------
# Attachment Sync
# ---------------------------------------------------------------------------


class TestSyncAttachments:

    @pytest.mark.asyncio
    async def test_sync_attachments_processes_records(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.indexing_filters = FilterCollection()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"

        connector._get_attachments_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_attachments_sync_checkpoint = AsyncMock()

        # Mock parent record
        parent_record = MagicMock()
        parent_record.id = "parent-id"
        tx.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])

        mock_ds = MagicMock()
        attachments = [{
            "id": "att-1",
            "title": "Design Doc",
            "url": "https://example.com/doc",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-06-01T00:00:00Z",
        }]
        mock_ds.attachments = AsyncMock(return_value=_mock_paginated_response({"attachments": attachments}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            connector._transform_attachment_to_link_record = MagicMock(return_value=MagicMock(
                weburl="https://example.com/doc",
                source_updated_at=1000,
                indexing_status=None,
            ))

            await connector._sync_attachments([(team_rg, [])])
            dep.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_attachments_empty_teams(self):
        connector, dep, *_ = _make_connector()
        await connector._sync_attachments([])
        dep.on_new_records.assert_not_called()


# ---------------------------------------------------------------------------
# Document Sync
# ---------------------------------------------------------------------------


class TestSyncDocuments:

    @pytest.mark.asyncio
    async def test_sync_documents_processes_records(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.indexing_filters = FilterCollection()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"

        connector._get_documents_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_documents_sync_checkpoint = AsyncMock()

        # Mock parent record
        parent_record = MagicMock()
        parent_record.id = "parent-id"
        tx.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])

        mock_ds = MagicMock()
        documents = [{
            "id": "doc-1",
            "title": "Spec",
            "content": "# Overview",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}},
            "project": None,
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-06-01T00:00:00Z",
        }]
        mock_ds.documents = AsyncMock(return_value=_mock_paginated_response({"documents": documents}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            connector._transform_document_to_webpage_record = MagicMock(return_value=MagicMock(
                source_updated_at=1000,
                indexing_status=None,
            ))

            await connector._sync_documents([(team_rg, [])])
            dep.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_documents_skips_standalone(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.indexing_filters = FilterCollection()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"

        connector._get_documents_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_documents_sync_checkpoint = AsyncMock()

        mock_ds = MagicMock()
        documents = [{
            "id": "doc-standalone",
            "title": "Standalone",
            "content": "text",
            "issue": None,
            "project": None,
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-06-01T00:00:00Z",
        }]
        mock_ds.documents = AsyncMock(return_value=_mock_paginated_response({"documents": documents}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._sync_documents([(team_rg, [])])
            dep.on_new_records.assert_not_called()


# ---------------------------------------------------------------------------
# Project Sync
# ---------------------------------------------------------------------------


class TestSyncProjects:

    @pytest.mark.asyncio
    async def test_sync_projects_processes_batch(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.indexing_filters = FilterCollection()
        connector.sync_filters = FilterCollection()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"
        team_rg.short_name = "ENG"
        team_rg.name = "Engineering"

        connector._get_team_project_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_project_sync_checkpoint = AsyncMock()

        mock_project = MagicMock(spec=ProjectRecord)
        mock_project.source_updated_at = 2000
        mock_project.indexing_status = None

        async def fake_fetch(*args, **kwargs):
            yield [(mock_project, [])]

        connector._fetch_projects_for_team_batch = fake_fetch

        await connector._sync_projects_for_teams([(team_rg, [])])
        dep.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_projects_empty_teams(self):
        connector, dep, *_ = _make_connector()
        await connector._sync_projects_for_teams([])
        dep.on_new_records.assert_not_called()


# ---------------------------------------------------------------------------
# Deletion Sync
# ---------------------------------------------------------------------------


class TestDeletionSync:

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_initial_sync(self):
        connector, *_ = _make_connector()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_deletion_sync_checkpoint = AsyncMock()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"

        await connector._sync_deleted_issues([(team_rg, [])])
        connector._update_deletion_sync_checkpoint.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_finds_trashed(self):
        connector, dep, *_ = _make_connector()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=1000)
        connector._update_deletion_sync_checkpoint = AsyncMock()
        connector._mark_record_and_children_deleted = AsyncMock()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"

        trashed_issues = [{
            "id": "issue-trash-1",
            "identifier": "ENG-99",
            "trashed": True,
            "archivedAt": "2024-06-01T00:00:00.000Z",
        }]

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_mock_paginated_response({"issues": trashed_issues}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._sync_deleted_issues([(team_rg, [])])
            connector._mark_record_and_children_deleted.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        connector, *_ = _make_connector()
        await connector._sync_deleted_issues([])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_initial_sync(self):
        connector, *_ = _make_connector()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_deletion_sync_checkpoint = AsyncMock()

        team_rg = MagicMock()
        team_rg.external_group_id = "team-1"

        await connector._sync_deleted_projects([(team_rg, [])])
        connector._update_deletion_sync_checkpoint.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_empty_teams(self):
        connector, *_ = _make_connector()
        await connector._sync_deleted_projects([])


# ---------------------------------------------------------------------------
# File URL Extraction
# ---------------------------------------------------------------------------


class TestFileExtraction:

    def test_extract_file_urls_no_text(self):
        connector, *_ = _make_connector()
        assert connector._extract_file_urls_from_markdown("") == []
        assert connector._extract_file_urls_from_markdown(None) == []

    def test_extract_file_urls_with_linear_uploads(self):
        connector, *_ = _make_connector()
        md = "[Design Doc](https://uploads.linear.app/abc/doc.pdf) and ![img](https://uploads.linear.app/abc/img.png)"
        files = connector._extract_file_urls_from_markdown(md)
        assert len(files) == 2

    def test_extract_file_urls_exclude_images(self):
        connector, *_ = _make_connector()
        md = "[Design Doc](https://uploads.linear.app/abc/doc.pdf) and ![img](https://uploads.linear.app/abc/img.png)"
        files = connector._extract_file_urls_from_markdown(md, exclude_images=True)
        assert len(files) == 1
        assert "Design Doc" in files[0]["filename"]

    def test_extract_file_urls_non_linear_ignored(self):
        connector, *_ = _make_connector()
        md = "[File](https://example.com/file.pdf)"
        files = connector._extract_file_urls_from_markdown(md)
        assert len(files) == 0

    def test_get_mime_type_from_url(self):
        connector, *_ = _make_connector()
        assert "pdf" in connector._get_mime_type_from_url("https://example.com/file.pdf").lower()
        assert connector._get_mime_type_from_url("https://example.com/file", "doc.xlsx") is not None
        # Unknown extension
        result = connector._get_mime_type_from_url("https://example.com/file.xyz")
        assert result is not None


# ---------------------------------------------------------------------------
# Filter Options
# ---------------------------------------------------------------------------


class TestFilterOptions:

    @pytest.mark.asyncio
    async def test_get_filter_options_teams(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        teams = [{"id": "t1", "name": "Eng", "key": "ENG"}]
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_paginated_response({"teams": teams}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector.get_filter_options("team_ids")
            assert result.success is True
            assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_get_filter_options_unknown_raises(self):
        connector, *_ = _make_connector()
        with pytest.raises(ValueError, match="Unknown"):
            await connector.get_filter_options("unknown_filter")

    @pytest.mark.asyncio
    async def test_get_team_options_with_search(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        teams = [
            {"id": "t1", "name": "Engineering", "key": "ENG"},
            {"id": "t2", "name": "Marketing", "key": "MKT"},
        ]
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_paginated_response({"teams": teams}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector._get_team_options(search="eng")
            assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_get_team_options_failure(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_response(success=False, message="fail"))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with pytest.raises(RuntimeError, match="Failed"):
                await connector._get_team_options()


# ---------------------------------------------------------------------------
# Date Filter Application
# ---------------------------------------------------------------------------


class TestDateFilters:

    def test_apply_date_filters_with_checkpoint(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()

        linear_filter = {}
        connector._linear_datetime_from_timestamp = MagicMock(return_value="2024-01-01T00:00:00Z")

        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=1000)
        assert "updatedAt" in linear_filter

    def test_apply_date_filters_no_checkpoint_no_filter(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()

        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=None)
        assert "updatedAt" not in linear_filter

    def test_apply_date_filters_none_sync_filters(self):
        connector, *_ = _make_connector()
        connector.sync_filters = None

        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=None)
        assert "updatedAt" not in linear_filter


# ---------------------------------------------------------------------------
# Parse Linear Datetime
# ---------------------------------------------------------------------------


class TestParseLinearDatetime:

    def test_parse_valid_datetime(self):
        connector, *_ = _make_connector()
        result = connector._parse_linear_datetime("2024-01-01T00:00:00.000Z")
        assert result is not None
        assert isinstance(result, int)

    def test_parse_empty_string(self):
        connector, *_ = _make_connector()
        assert connector._parse_linear_datetime("") is None

    def test_parse_invalid_string(self):
        connector, *_ = _make_connector()
        assert connector._parse_linear_datetime("not-a-date") is None

    def test_parse_none(self):
        connector, *_ = _make_connector()
        assert connector._parse_linear_datetime(None) is None
