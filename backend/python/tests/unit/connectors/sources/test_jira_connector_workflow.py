"""Comprehensive workflow tests for Jira Cloud connector.

These tests exercise full sync workflows covering initialization, user/group sync,
project sync, issue sync, deletion handling, permission schemes, ADF parsing,
and error handling. Each test covers many code paths to maximize statement coverage.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.atlassian.jira_cloud.connector import (
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    ISSUE_SEARCH_FIELDS,
    JiraConnector,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.jira.workflow")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-jira-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.withdraw_team_app_edge = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_records_deleted_cascade = AsyncMock(return_value={
        "success": True, "deleted_records": [], "failed_records": [],
        "total_requested": 0, "successfully_deleted": 0, "failed_count": 0,
    })
    data_entities_processor.on_new_app_roles = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="active@example.com"),
    ])
    data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
    data_entities_processor.get_placeholder_records = AsyncMock(return_value=[])
    data_entities_processor.get_record_by_issue_key = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()
    mock_tx_store.get_sync_point = AsyncMock(return_value=None)  # nothing stored yet

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service, mock_tx_store


def _make_connector():
    logger, dep, dsp, cs, tx = _make_mock_deps()
    connector = JiraConnector(logger, dep, dsp, cs, "conn-jira-1", "team", "test-user-id")
    # The role list kept for security levels (roles_for_levels): nothing kept yet.
    connector.issues_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.issues_sync_point.update_sync_point = AsyncMock()
    return connector, dep, dsp, cs, tx


def _make_mock_response(status=200, data=None, text_val=""):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value=text_val)
    return resp


# ===========================================================================
# Jira Connector Init
# ===========================================================================


class TestJiraInit:

    @pytest.mark.asyncio
    async def test_init_oauth(self):
        connector, dep, dsp, cs, tx = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_site_url.return_value = "https://test.atlassian.net"
            mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)

            with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
                result = await connector.init()
                assert result is True
                assert connector.site_url == "https://test.atlassian.net"

    @pytest.mark.asyncio
    async def test_init_api_token(self):
        connector, dep, dsp, cs, tx = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_site_url.return_value = "https://test.atlassian.net"
            mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)

            with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
                result = await connector.init()
                assert result is True
                assert connector.site_url == "https://test.atlassian.net"

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector, dep, dsp, cs, tx = _make_connector()

        # init() raises so the router surfaces the real reason, not a generic message.
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(side_effect=Exception("fail"))
            with pytest.raises(ConnectorInitError, match="fail"):
                await connector.init()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("scope", "withdrawn"), [("team", True), ("personal", False)])
    async def test_an_init_that_fails_still_withdraws_the_org_team_from_the_gate(self, scope: str, withdrawn: bool) -> None:
        """Earlier releases let the org's All team through a team connector's gate for projects open to anyone. The
        edge goes first in init, before Jira is reached, so a connector whose init keeps failing does not keep it."""
        connector, dep, *_ = _make_connector()
        connector.scope = scope
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(side_effect=Exception("site unreachable"))
            with pytest.raises(ConnectorInitError):
                await connector.init()
        assert dep.withdraw_team_app_edge.await_args_list == ([call("conn-jira-1")] if withdrawn else [])

    @pytest.mark.asyncio
    async def test_a_withdrawal_that_fails_in_init_leaves_the_init_error_jiras(self) -> None:
        """A store that cannot take the withdrawal yet is logged, not raised: init still reports what Jira answered,
        and the sync withdraws the edge again, raising."""
        connector, dep, *_ = _make_connector()
        dep.withdraw_team_app_edge = AsyncMock(side_effect=ConnectionError("graph unavailable"))
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(side_effect=Exception("site unreachable"))
            with pytest.raises(ConnectorInitError, match="site unreachable"):
                await connector.init()
        connector.data_source = MagicMock()
        with pytest.raises(ConnectionError):
            await connector.run_sync()


# ===========================================================================
# Get Fresh Datasource
# ===========================================================================


class TestJiraFreshDatasource:

    @pytest.mark.asyncio
    async def test_api_token_returns_existing(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.external_client = MagicMock()

        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
            ds = await connector._get_fresh_datasource()
            assert ds is not None

    @pytest.mark.asyncio
    async def test_oauth_updates_token(self):
        connector, dep, dsp, cs, tx = _make_connector()
        mock_client = MagicMock()
        internal = MagicMock()
        internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = internal
        connector.external_client = mock_client

        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "new-token"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
            await connector._get_fresh_datasource()
            internal.set_token.assert_called_with("new-token")

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        connector, *_ = _make_connector()
        connector.external_client = None
        with pytest.raises(HTTPException) as exc_info:
            await connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409


# ===========================================================================
# User Fetching
# ===========================================================================


class TestJiraFetchUsers:

    @pytest.mark.asyncio
    async def test_fetch_users_pagination(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        page1 = [
            {"accountId": "a1", "accountType": "atlassian", "emailAddress": "alice@test.com", "displayName": "Alice", "active": True},
            {"accountId": "a2", "accountType": "atlassian", "emailAddress": None, "displayName": "NoEmail", "active": True},
        ]

        mock_ds = MagicMock()
        empty = _make_mock_response()
        empty.json = MagicMock(return_value=[])  # only an empty page ends the list
        mock_ds.get_all_users = AsyncMock(side_effect=[_make_mock_response(data=page1), empty])

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector._fetch_users()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_users_no_datasource(self):
        connector, *_ = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError):
            await connector._fetch_users()


# ===========================================================================
# Group Fetching & Sync
# ===========================================================================


class TestJiraGroupSync:

    @pytest.mark.asyncio
    async def test_sync_user_groups(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = MagicMock()

        users = [AppUser(
            app_name=Connectors.JIRA, connector_id="conn-jira-1",
            source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
            full_name="Alice", is_active=True
        )]

        connector._fetch_groups = AsyncMock(return_value=([
            {"groupId": "g1", "name": "jira-software-users"},
        ], False))
        # _fetch_group_members now returns (account_ids, ok); "a1" matches the user's source_user_id.
        connector._fetch_group_members = AsyncMock(return_value=(["a1"], True))

        result = await connector._sync_user_groups(users)
        assert "g1" in result
        assert "jira-software-users" not in result  # by id only: a name resolves through _group_ids_by_name
        assert result["g1"] == users  # membership resolved from accountId
        dep.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_groups_pagination(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(return_value=_make_mock_response(data={
            "values": [{"groupId": "g1", "name": "group1"}],
            "isLast": True,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result, fetch_failed = await connector._fetch_groups()
            assert len(result) == 1
            assert fetch_failed is False

    @pytest.mark.asyncio
    async def test_fetch_group_members(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(return_value=_make_mock_response(data={
            "values": [{"accountId": "acc-1", "emailAddress": "alice@test.com"}],
            "isLast": True,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            # Now returns (account_ids, ok); ok=False lets the caller skip rather than persist
            # a truncated membership.
            result, ok = await connector._fetch_group_members("g1", "group1")
            assert "acc-1" in result
            assert ok is True


# ===========================================================================
# Project Fetching
# ===========================================================================


class TestJiraProjectFetching:

    @pytest.mark.asyncio
    async def test_fetch_projects_all(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        projects = [{"id": "p1", "name": "Project One", "key": "PROJ", "url": "https://test.atlassian.net/project/PROJ", "description": "A project"}]

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": projects, "isLast": True, "total": 1,
        }))

        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._safe_json_parse = MagicMock(return_value={"values": projects, "isLast": True, "total": 1})
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects()
        assert len(record_groups) == 1
        assert len(raw) == 1

    @pytest.mark.asyncio
    async def test_fetch_projects_with_keys_filter(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        projects = [{"id": "p1", "name": "Proj", "key": "PRJ", "url": None, "description": None}]

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": projects, "isLast": True, "total": 1,
        }))

        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._safe_json_parse = MagicMock(return_value={"values": projects, "isLast": True, "total": 1})
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects(project_keys=["PRJ"])
        assert len(record_groups) == 1

# ===========================================================================
# Permission Scheme
# ===========================================================================


class TestPermissionScheme:

    @pytest.mark.asyncio
    async def test_fetch_permission_scheme_all_types(self):
        connector, *_ = _make_connector()
        connector._user_read_unfinished = True  # no user map: the grant's own email is only used after a cut-short user read
        connector._licensed_account_ids = {"a1"}  # a licensed account the cut-short read did not show
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(data={"id": 1}))

        grants = [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "g1"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "applicationRole", "parameter": "jira-software", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "a1", "value": None, "user": {"emailAddress": "alice@test.com"}}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "anyone", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10001", "value": None, "projectRole": {"name": "Developers", "id": "10001"}}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "sd.customer.portal.only", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "groupCustomField", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "unknownType", "value": None, "parameter": None}},
            {"permission": "OTHER_PERM", "holder": {"type": "group", "value": "g2"}},  # not BROWSE_PROJECTS
        ]

        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(data={
            "permissions": grants,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            app_roles = {"jira-software": [{"groupId": "app-g1", "name": "SW Users"}]}
            perms = await connector._fetch_project_permission_scheme("PROJ", app_roles)

            entity_types = {p.entity_type for p in perms}
            assert EntityType.GROUP in entity_types
            assert EntityType.USER in entity_types
            assert EntityType.ORG in entity_types
            assert EntityType.ROLE in entity_types

    @pytest.mark.asyncio
    async def test_application_role_no_mapping_skips(self):
        """When mapping is empty (not due to 403), unresolvable role is skipped."""
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(data={"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(data={
            "permissions": [
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "applicationRole", "parameter": "unknown-role", "value": None}},
            ],
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            perms = await connector._fetch_project_permission_scheme("PROJ", {})
            assert len(perms) == 0


# ===========================================================================
# Deletion Handling
# ===========================================================================


class TestDeletionHandling:

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_not_found_in_db(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(status=404))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            dep.get_record_by_issue_key = AsyncMock(return_value=None)
            await connector._handle_deleted_issue("PROJ-1")
            # Should not crash, just log warning

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_still_exists(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(status=200, data={"key": "PROJ-1"}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._handle_deleted_issue("PROJ-1")
            # Issue still exists, should not delete

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_cascade_deletes(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()
        dep.on_records_deleted_cascade = AsyncMock(return_value={
            "success": True, "deleted_records": [], "failed_records": [],
            "total_requested": 1, "successfully_deleted": 3, "failed_count": 0,
        })

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(404))

        issue_record = MagicMock()
        issue_record.id = "internal-1"
        issue_record.external_record_id = "ext-1"
        dep.get_record_by_issue_key = AsyncMock(return_value=issue_record)
        dep.get_records_by_parent = AsyncMock(return_value=[])  # no restricted comment records

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._handle_deleted_issue("PROJ-1")

        # Attachment-only delete: traverses ATTACHMENT edges (deleting the issue's
        # own files) but leaves PARENT_CHILD children alive.
        dep.on_records_deleted_cascade.assert_called_once_with(
            ["internal-1"], connector.connector_id, cascade_children=False,
        )

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_skips_delete_when_record_absent(self):
        # Distinct from test_handle_deleted_issue_not_found_in_db above: asserts that no
        # attachment cascade is triggered at all when the record is missing.
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()
        dep.on_records_deleted_cascade = AsyncMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(404))

        dep.get_record_by_issue_key = AsyncMock(return_value=None)

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._handle_deleted_issue("PROJ-2")

        dep.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_detect_and_handle_deletions(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        # _fetch_deleted_issues_from_audit now returns (keys, ok).
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=(["PROJ-1"], True))
        connector._handle_deleted_issue = AsyncMock(return_value=(1, 0))

        # _detect_and_handle_deletions returns (checkpoint_ms, success).
        _checkpoint_ms, success = await connector._detect_and_handle_deletions(1000)
        assert success is True
        connector._handle_deleted_issue.assert_called_once_with("PROJ-1")

    @pytest.mark.asyncio
    async def test_detect_no_deletions(self):
        connector, *_ = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=([], True))
        _checkpoint_ms, success = await connector._detect_and_handle_deletions(1000)
        assert success is True

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response(data={
            "records": [
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-1"}, "created": "2024-01-01"},
                {"objectItem": {"typeName": "OTHER_ACTION", "name": "PROJ-2"}, "created": "2024-01-01"},
            ],
            "total": 2,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            # Now returns (issue_keys, ok); ok is False only when a page failed to read.
            result, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z")
            assert result == ["PROJ-1"]
            assert ok is True


# ===========================================================================
# Sync Checkpoints
# ===========================================================================


class TestSyncCheckpoints:

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        result = await connector._get_issues_sync_checkpoint()
        assert result == 1000

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_none(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await connector._get_issues_sync_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_update_issues_sync_checkpoint(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        await connector._update_issues_sync_checkpoint({"total_synced": 5}, 1)
        connector.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_issues_sync_checkpoint_no_sync(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        await connector._update_issues_sync_checkpoint({"total_synced": 0}, 0)
        connector.issues_sync_point.update_sync_point.assert_not_called()


# ===========================================================================
# Full Sync Workflow
# ===========================================================================


class TestJiraFullSync:

    @pytest.mark.asyncio
    async def test_run_sync_full_workflow(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):

            users = [AppUser(
                app_name=Connectors.JIRA, connector_id="conn-jira-1",
                source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
                full_name="Alice", is_active=True
            )]
            connector._fetch_users = AsyncMock(return_value=users)
            connector._keep_licensed_users = AsyncMock(side_effect=lambda found, _mapping: found)  # all licensed
            connector._sync_user_groups = AsyncMock(return_value={})

            rg = RecordGroup(
                id=str(uuid4()), org_id="org-jira-1", external_group_id="p1",
                connector_id="conn-jira-1", connector_name=Connectors.JIRA,
                name="Project", short_name="PRJ", group_type=RecordGroupType.PROJECT,
            )
            connector._fetch_projects = AsyncMock(return_value=(
                [(rg, [])],
                [{"key": "PRJ", "lead": {"accountId": "a1", "displayName": "Alice"}}],
            ))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={
                "total_synced": 5, "new_count": 3, "updated_count": 2,
            })
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()

            dep.on_new_app_users.assert_called_once()
            dep.on_new_record_groups.assert_called_once()
            connector._sync_project_roles.assert_called_once()
            connector._sync_project_lead_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_project_open_to_anyone_reaches_only_linked_jira_accounts(self) -> None:
        """Browse held by `anyone` is an ORG grant, which reaches only the accounts linked to the connector: people
        without an account in Jira read nothing through it, even on a site whose signed-out visitors read the project.
        The sync never lets the org's All team through the gate, and withdraws an edge an earlier release left."""
        connector, dep, *_ = _make_connector()
        connector.data_source = MagicMock()
        # Earlier releases searched signed out and, when the search read the project, opened the gate to the org.
        connector._read_anonymously = AsyncMock(return_value=True)
        dep.open_app_to_org_team = AsyncMock(return_value=True)
        dep.org_team_app_edge_exists = AsyncMock(return_value=False)
        project = RecordGroup(
            id=str(uuid4()), org_id="org-jira-1", external_group_id="p1", connector_id="conn-jira-1",
            connector_name=Connectors.JIRA, name="Public", short_name="PUB", group_type=RecordGroupType.PROJECT,
        )
        anyone = Permission(entity_type=EntityType.ORG, external_id="anyone_authenticated", type=PermissionType.READ)
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            connector._fetch_users = AsyncMock(return_value=[AppUser(
                app_name=Connectors.JIRA, connector_id="conn-jira-1", source_user_id="a1", org_id="org-jira-1",
                email="alice@test.com", full_name="Alice", is_active=True)])
            connector._keep_licensed_users = AsyncMock(side_effect=lambda found, _mapping: found)
            connector._sync_user_groups = AsyncMock(return_value={})
            connector._fetch_projects = AsyncMock(return_value=([(project, [anyone])], [{"key": "PUB"}]))
            connector._stored_projects = AsyncMock(return_value=[("p1", "PUB")])
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={"total_synced": 1, "new_count": 1, "updated_count": 0})
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()
            connector.notify = AsyncMock()
            await connector.run_sync()
        dep.on_new_record_groups.assert_awaited_once_with([(project, [anyone])])
        assert [name for name, *_ in dep.mock_calls if "team" in name] == ["withdraw_team_app_edge"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("licensed", "listed", "user_read_refused", "linked"), [
        (set(), True, False, False),
        ({"a1"}, True, False, True),
        ({"other"}, False, False, False),
        ({"other"}, False, True, False),
        ({"a1"}, False, True, True),
        (None, True, False, True),
        (None, False, True, False),
    ], ids=["no_product_access", "product_access", "not_listed_and_no_product_access",
            "user_read_refused_and_no_product_access", "user_read_refused_and_product_access",
            "product_access_unread_and_listed_active", "product_access_unread_and_user_read_refused"])
    async def test_the_configuring_user_reads_through_the_jira_account_only_while_jira_lets_it_in(
        self, licensed: set[str] | None, listed: bool, user_read_refused: bool, linked: bool,
    ) -> None:
        """The user who configured the connector reads what its Jira account reads (``authenticatedAs``), and that link
        passes the connector gate, ORG grants included. It stays only while the sync confirms Jira lets the account in:
        product access read in full that includes it, or, product access unread, a complete user list showing it
        active. A product-access read without it, or nothing that can tell, removes the link (fail closed)."""
        connector, dep, *_ = _make_connector()
        connector.data_source = MagicMock()
        connector._authenticated_jira_account_id, connector._authenticated_jira_email = "a1", "sync@test.com"
        dep.link_authenticator_to_source_user = AsyncMock()
        dep.unlink_authenticator = AsyncMock()
        dep.withdraw_app_user_access = AsyncMock()
        me = AppUser(app_name=Connectors.JIRA, connector_id="conn-jira-1", source_user_id="a1", org_id="org-jira-1",
                     email="sync@test.com", full_name="Sync", is_active=True)

        async def fetch(**_kwargs: object) -> list[AppUser]:
            # /users/search answered (listing the account active, or not), or was refused on its first page.
            connector._user_bulk_forbidden = user_read_refused
            connector._listed_account_ids = {"a1"} if listed else ({"other"} if not user_read_refused else set())
            return [me] if listed else []

        async def keep(found: list[AppUser], _mapping: object) -> list[AppUser]:
            connector._licensed_account_ids = licensed
            return found if licensed is None else [u for u in found if u.source_user_id in licensed]

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            connector._fetch_users = AsyncMock(side_effect=fetch)
            connector._keep_licensed_users = AsyncMock(side_effect=keep)
            connector._sync_user_groups = AsyncMock(return_value={})
            connector._fetch_projects = AsyncMock(return_value=([], []))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={"total_synced": 0, "new_count": 0, "updated_count": 0})
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()
            connector.notify = AsyncMock()
            await connector.run_sync()
        assert dep.link_authenticator_to_source_user.await_count == (1 if linked else 0)
        assert dep.unlink_authenticator.await_args_list == ([] if linked else [call("conn-jira-1")])

    @pytest.mark.asyncio
    async def test_run_sync_no_users_returns(self):
        connector, dep, *_ = _make_connector()
        dep.get_all_active_users = AsyncMock(return_value=[])
        connector.data_source = MagicMock()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            await connector.run_sync()
            dep.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_sync_raises_when_not_initialized(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = None
        connector.init = AsyncMock()
        connector.notify = AsyncMock()

        with pytest.raises(RuntimeError, match="not initialized"):
            await connector.run_sync()
        connector.init.assert_not_awaited()
        connector.notify.assert_not_awaited()


# ===========================================================================
# Project Roles
# ===========================================================================


class TestProjectRoles:

    @pytest.mark.asyncio
    async def test_sync_project_roles(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = MagicMock()

        users = [AppUser(
            app_name=Connectors.JIRA, connector_id="conn-jira-1",
            source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
            full_name="Alice", is_active=True
        )]

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(data={
            "Developers": "https://api.atlassian.com/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(data={
            "name": "Developers",
            "actors": [
                {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "a1", "emailAddress": "alice@test.com"}},
                {"type": "atlassian-group-role-actor", "name": "jira-users", "groupId": "g1"},
            ]
        }))

        groups_map = {"g1": users, "jira-users": users}

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._sync_project_roles(["PRJ"], users, groups_map)
            dep.on_new_app_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_project_lead_roles(self):
        connector, dep, *_ = _make_connector()

        users = [AppUser(
            app_name=Connectors.JIRA, connector_id="conn-jira-1",
            source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
            full_name="Alice", is_active=True
        )]

        raw_projects = [
            {"key": "PRJ", "lead": {"accountId": "a1", "displayName": "Alice"}},
            {"key": "PRJ2", "lead": None},  # No lead
        ]

        await connector._sync_project_lead_roles(raw_projects, users)
        dep.on_new_app_roles.assert_called_once()
        call_args = dep.on_new_app_roles.call_args[0][0]
        assert len(call_args) == 2  # Both projects get roles


# ===========================================================================
# Filter Options
# ===========================================================================


class TestJiraFilterOptions:

    @pytest.mark.asyncio
    async def test_get_filter_options_projects(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": [{"key": "PROJ", "name": "My Project"}],
            "isLast": True,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            connector._safe_json_parse = MagicMock(return_value={
                "values": [{"key": "PROJ", "name": "My Project"}],
                "isLast": True,
            })
            result = await connector.get_filter_options("project_keys")
            assert result.success is True
            assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_get_filter_options_unknown_raises(self):
        connector, *_ = _make_connector()
        with pytest.raises(ValueError, match="Unsupported"):
            await connector.get_filter_options("unknown")


# ===========================================================================
# Safe JSON Parse
# ===========================================================================


class TestSafeJsonParse:

    def test_parse_valid_response(self):
        connector, *_ = _make_connector()
        resp = MagicMock()
        resp.json.return_value = {"key": "value"}
        result = connector._safe_json_parse(resp, "test")
        assert result == {"key": "value"}

    def test_parse_invalid_json(self):
        connector, *_ = _make_connector()
        resp = MagicMock()
        resp.json.side_effect = Exception("bad json")
        resp.text.return_value = "not json"
        result = connector._safe_json_parse(resp, "test")
        assert result is None


# ===========================================================================
# Timestamp Parsing
# ===========================================================================


class TestJiraTimestampParsing:

    def test_parse_jira_timestamp(self):
        connector, *_ = _make_connector()
        result = connector._parse_jira_timestamp("2024-01-01T00:00:00.000+0000")
        assert isinstance(result, int)
        assert result > 0

    def test_parse_jira_timestamp_none(self):
        connector, *_ = _make_connector()
        assert connector._parse_jira_timestamp(None) == 0

    def test_parse_jira_timestamp_invalid(self):
        connector, *_ = _make_connector()
        # Should handle gracefully
        result = connector._parse_jira_timestamp("not-a-date")
        assert isinstance(result, int)
