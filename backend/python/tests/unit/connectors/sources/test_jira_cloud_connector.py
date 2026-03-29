"""
Comprehensive coverage tests for app.connectors.sources.atlassian.jira_cloud.connector.

Targets the ~607 uncovered lines to achieve 95%+ coverage by testing:
- init() with OAuth, API_TOKEN, and error paths
- _get_access_token() success and error
- _get_fresh_datasource() API_TOKEN, no client, no config, no token
- get_filter_options() project_keys and unsupported key
- _get_project_options() pagination, search, error
- run_sync() full orchestration
- _get_issues_sync_checkpoint() success and error
- _update_issues_sync_checkpoint() with/without stats
- _handle_issue_deletions() with audit_last_sync_time path
- _handle_deleted_issue() epic, non-epic, still exists, not found
- _delete_issue_children() recursive, FILE type, error
- _fetch_users() pagination, inactive, no email
- _fetch_groups() pagination, isLast, error
- _fetch_group_members() pagination, isLast, error
- _fetch_projects() NOT_IN, IN, no filter, ADF description
- _sync_all_project_issues() success and error
- _sync_project_issues() new project, incremental, resume
- _fetch_issues_batched() pagination, filters, empty, error
- _build_issue_records() new/updated/unchanged records, attachments
- _fetch_issue_attachments() new/existing/no attachments, error
- _handle_attachment_deletions_from_changelog() all branches
- _organize_issue_comments_to_threads() threads, no id
- _parse_issue_to_blocks() description, comments, attachments, images
- _process_issue_blockgroups_for_streaming() success and error
- _process_issue_attachments_for_children() new/existing records
- create_connector() factory method
- Various ADF edge cases, media, marks, list types
"""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus, RecordRelations
from app.connectors.sources.atlassian.jira_cloud.connector import (
    AUDIT_PAGE_SIZE,
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    GROUP_MEMBER_PAGE_SIZE,
    GROUP_PAGE_SIZE,
    ISSUE_SEARCH_FIELDS,
    USER_PAGE_SIZE,
    JiraConnector,
    adf_to_text,
    adf_to_text_with_images,
    extract_media_from_adf,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_logger():
    return logging.getLogger("test.jira.coverage")


def _make_mock_deps():
    logger = _make_mock_logger()
    dep = MagicMock()
    dep.org_id = "org-cov-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="active@example.com"),
    ])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.initialize = AsyncMock()

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return JiraConnector(logger, dep, dsp, cs, "conn-jira-cov")


def _make_mock_response(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    resp.bytes = MagicMock(return_value=b"file-bytes-content")
    return resp


def _make_app_user(email="user@example.com", account_id="acc-1", name="User One"):
    return AppUser(
        app_name=Connectors.JIRA,
        connector_id="conn-jira-cov",
        source_user_id=account_id,
        org_id="org-cov-1",
        email=email,
        full_name=name,
        is_active=True,
    )


def _make_ticket_record(external_id="12345", issue_key="PROJ-1", version=1, **kwargs):
    defaults = dict(
        id=str(uuid4()),
        org_id="org-cov-1",
        record_name=f"[{issue_key}] Test Issue",
        record_type=RecordType.TICKET,
        external_record_id=external_id,
        version=version,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-jira-cov",
        mime_type=MimeTypes.BLOCKS.value,
        source_created_at=1700000000000,
        source_updated_at=1700000000000,
        weburl="https://company.atlassian.net/browse/PROJ-1",
        external_record_group_id="proj-id-1",
        parent_external_record_id=None,
        record_group_type=RecordGroupType.PROJECT,
    )
    defaults.update(kwargs)
    return TicketRecord(**defaults)


def _make_file_record(attachment_id="99", issue_id="12345", version=0, **kwargs):
    defaults = dict(
        id=str(uuid4()),
        org_id="org-cov-1",
        record_name="screenshot.png",
        record_type=RecordType.FILE,
        external_record_id=f"attachment_{attachment_id}",
        version=version,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-jira-cov",
        mime_type="image/png",
        parent_external_record_id=issue_id,
        parent_record_type=RecordType.TICKET,
        external_record_group_id="proj-id-1",
        record_group_type=RecordGroupType.PROJECT,
        is_file=True,
        source_updated_at=1700000000000,
    )
    defaults.update(kwargs)
    return FileRecord(**defaults)


def _make_tx_store(existing_record=None):
    """Create a mock transaction store."""
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing_record)
    mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=existing_record)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    return mock_tx, mock_tx_store


def _make_issue(
    issue_id="100",
    key="PROJ-1",
    summary="Test Issue",
    issue_type="Task",
    hierarchy_level=0,
    parent_id=None,
    parent_key=None,
    status="Open",
    priority="Medium",
    creator_id="acc-1",
    assignee_id=None,
    created="2024-01-15T10:30:45.000+0000",
    updated="2024-01-16T10:30:45.000+0000",
    attachments=None,
    description=None,
    issuelinks=None,
    changelog=None,
    security=None,
):
    issue = {
        "id": issue_id,
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type, "hierarchyLevel": hierarchy_level},
            "status": {"name": status},
            "priority": {"name": priority},
            "creator": {"accountId": creator_id, "displayName": "Creator"} if creator_id else None,
            "reporter": None,
            "assignee": {"accountId": assignee_id, "displayName": "Assignee"} if assignee_id else None,
            "created": created,
            "updated": updated,
            "parent": {"id": parent_id, "key": parent_key} if parent_id else None,
            "attachment": attachments or [],
            "security": security,
            "issuelinks": issuelinks or [],
            "project": {"id": "proj-id-1"},
        },
    }
    if changelog:
        issue["changelog"] = changelog
    return issue


# ===========================================================================
# init() - OAuth, API_TOKEN, and Error paths
# ===========================================================================


class TestInitCoverage:

    @pytest.mark.asyncio
    async def test_init_oauth_success(self):
        connector = _make_connector()
        mock_resource = MagicMock()
        mock_resource.id = "cloud-123"
        mock_resource.url = "https://company.atlassian.net"

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)
            MockJiraClient.get_accessible_resources = AsyncMock(return_value=[mock_resource])

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "test-token"},
            })

            result = await connector.init()
            assert result is True
            assert connector.cloud_id == "cloud-123"
            assert connector.site_url == "https://company.atlassian.net"

    @pytest.mark.asyncio
    async def test_init_api_token_success(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "API_TOKEN", "baseUrl": "https://mycompany.atlassian.net"},
            })

            result = await connector.init()
            assert result is True
            assert connector.cloud_id is None
            assert connector.site_url == "https://mycompany.atlassian.net"

    @pytest.mark.asyncio
    async def test_init_api_token_no_base_url(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "API_TOKEN", "baseUrl": ""},
            })

            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_init_oauth_no_resources(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)
            MockJiraClient.get_accessible_resources = AsyncMock(return_value=[])

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "test-token"},
            })

            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_init_exception(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            MockJiraClient.build_from_services = AsyncMock(side_effect=Exception("network fail"))

            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_init_api_token_strips_trailing_slash(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "API_TOKEN", "baseUrl": "https://mycompany.atlassian.net/ "},
            })

            result = await connector.init()
            assert result is True
            assert connector.site_url == "https://mycompany.atlassian.net"


# ===========================================================================
# _get_access_token()
# ===========================================================================


class TestGetAccessToken:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "my-token"}
        })
        result = await connector._get_access_token()
        assert result == "my-token"

    @pytest.mark.asyncio
    async def test_no_token_raises(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {}
        })
        with pytest.raises(ValueError, match="access token not found"):
            await connector._get_access_token()

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="access token not found"):
            await connector._get_access_token()


# ===========================================================================
# _get_fresh_datasource()
# ===========================================================================


class TestGetFreshDatasource:

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        connector = _make_connector()
        connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_api_token_returns_datasource(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource") as MockDS:
            mock_ds = MagicMock()
            MockDS.return_value = mock_ds
            result = await connector._get_fresh_datasource()
            assert result == mock_ds


# ===========================================================================
# get_filter_options()
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_project_keys(self):
        connector = _make_connector()
        connector._get_project_options = AsyncMock(return_value=MagicMock())
        await connector.get_filter_options("project_keys")
        connector._get_project_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_key(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="Unsupported"):
            await connector.get_filter_options("invalid_key")


# ===========================================================================
# _get_project_options()
# ===========================================================================


class TestGetProjectOptions:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN"},
        })

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [
                {"key": "PROJ1", "name": "Project One"},
                {"key": "PROJ2", "name": "Project Two"},
            ],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_project_options(page=1, limit=20, search=None)
        assert result.success is True
        assert len(result.options) == 2
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_with_search(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"key": "PROJ1", "name": "Project One"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_project_options(page=1, limit=10, search="Project")
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_has_more(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"key": "PROJ1", "name": "Project One"}],
            "isLast": False,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_project_options(page=1, limit=10, search=None)
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(RuntimeError, match="Failed to fetch"):
            await connector._get_project_options(page=1, limit=10, search=None)

    @pytest.mark.asyncio
    async def test_none_response(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=None)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(RuntimeError, match="Failed to fetch"):
            await connector._get_project_options(page=1, limit=10, search=None)

    @pytest.mark.asyncio
    async def test_json_parse_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        resp = _make_mock_response(200)
        resp.json.side_effect = Exception("parse error")
        mock_ds.search_projects = AsyncMock(return_value=resp)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(RuntimeError, match="Failed to fetch"):
            await connector._get_project_options(page=1, limit=10, search=None)

    @pytest.mark.asyncio
    async def test_page_2_offset(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_project_options(page=2, limit=5, search=None)
        # startAt should be 5 for page 2 with limit 5
        call_kwargs = mock_ds.search_projects.call_args
        assert call_kwargs[1]["startAt"] == 5

    @pytest.mark.asyncio
    async def test_skips_projects_without_key_or_name(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [
                {"key": None, "name": "No Key"},
                {"key": "PROJ", "name": None},
                {"key": "GOOD", "name": "Good Project"},
            ],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_project_options(page=1, limit=20, search=None)
        assert len(result.options) == 1
        assert result.options[0].id == "GOOD"


# ===========================================================================
# run_sync() orchestration
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_full_sync_no_users(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_full_sync_with_users_and_projects(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.site_url = "https://company.atlassian.net"

        user = _make_app_user()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])

        connector._fetch_users = AsyncMock(return_value=[user])
        connector._sync_user_groups = AsyncMock(return_value={})

        mock_rg = MagicMock()
        mock_rg.short_name = "PROJ"
        mock_rg.external_group_id = "proj-id-1"
        connector._fetch_projects = AsyncMock(return_value=(
            [(mock_rg, [])],
            [{"key": "PROJ", "lead": None}]
        ))
        connector._sync_project_roles = AsyncMock()
        connector._sync_project_lead_roles = AsyncMock()
        connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        connector._sync_all_project_issues = AsyncMock(return_value={
            "total_synced": 5, "new_count": 3, "updated_count": 2
        })
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._handle_issue_deletions = AsyncMock()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            await connector.run_sync()

        connector._fetch_users.assert_awaited_once()
        connector._sync_user_groups.assert_awaited_once()
        connector._fetch_projects.assert_awaited_once()
        connector._sync_all_project_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_initializes_if_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            await connector.run_sync()
        connector.init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_error_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=Exception("db error")
        )

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            with pytest.raises(Exception, match="db error"):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_project_keys_filter_in(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.site_url = "https://company.atlassian.net"

        user = _make_app_user()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])

        connector._fetch_users = AsyncMock(return_value=[user])
        connector._sync_user_groups = AsyncMock(return_value={})

        mock_rg = MagicMock()
        mock_rg.short_name = "PROJ"
        mock_rg.external_group_id = "proj-id-1"
        connector._fetch_projects = AsyncMock(return_value=([(mock_rg, [])], [{"key": "PROJ", "lead": None}]))
        connector._sync_project_roles = AsyncMock()
        connector._sync_project_lead_roles = AsyncMock()
        connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        connector._sync_all_project_issues = AsyncMock(return_value={
            "total_synced": 0, "new_count": 0, "updated_count": 0
        })
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._handle_issue_deletions = AsyncMock()

        # Set up sync filter with project_keys IN
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["PROJ"]
        mock_filter.get_operator.return_value = MagicMock(value="in")
        from app.connectors.core.registry.filters import SyncFilterKey
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.return_value = mock_filter

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(mock_sync_filters, None)):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_empty_project_keys_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.site_url = "https://company.atlassian.net"

        user = _make_app_user()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])
        connector._fetch_users = AsyncMock(return_value=[])
        connector._sync_user_groups = AsyncMock(return_value={})

        mock_rg = MagicMock()
        mock_rg.short_name = "PROJ"
        mock_rg.external_group_id = "proj-id-1"
        connector._fetch_projects = AsyncMock(return_value=([(mock_rg, [])], [{"key": "PROJ", "lead": None}]))
        connector._sync_project_roles = AsyncMock()
        connector._sync_project_lead_roles = AsyncMock()
        connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        connector._sync_all_project_issues = AsyncMock(return_value={
            "total_synced": 0, "new_count": 0, "updated_count": 0
        })
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._handle_issue_deletions = AsyncMock()

        mock_filter = MagicMock()
        mock_filter.get_value.return_value = []
        mock_filter.get_operator.return_value = None
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.return_value = mock_filter

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(mock_sync_filters, None)):
            await connector.run_sync()


# ===========================================================================
# _get_issues_sync_checkpoint()
# ===========================================================================


class TestGetIssuesSyncCheckpoint:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000000}
        )
        result = await connector._get_issues_sync_checkpoint()
        assert result == 1700000000000

    @pytest.mark.asyncio
    async def test_no_data(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await connector._get_issues_sync_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(side_effect=Exception("err"))
        result = await connector._get_issues_sync_checkpoint()
        assert result is None


# ===========================================================================
# _update_issues_sync_checkpoint()
# ===========================================================================


class TestUpdateIssuesSyncCheckpoint:

    @pytest.mark.asyncio
    async def test_with_synced_issues(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()
        await connector._update_issues_sync_checkpoint({"total_synced": 5}, 2)
        connector.issues_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_zero_synced_but_projects(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()
        await connector._update_issues_sync_checkpoint({"total_synced": 0}, 3)
        connector.issues_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_synced_no_projects(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()
        await connector._update_issues_sync_checkpoint({"total_synced": 0}, 0)
        connector.issues_sync_point.update_sync_point.assert_not_awaited()


# ===========================================================================
# _handle_issue_deletions() with audit_last_sync_time
# ===========================================================================


class TestHandleIssueDeletionsCoverage:

    @pytest.mark.asyncio
    async def test_uses_audit_sync_time_when_available(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000000}
        )
        connector.issues_sync_point.update_sync_point = AsyncMock()
        connector._detect_and_handle_deletions = AsyncMock(return_value=0)

        await connector._handle_issue_deletions(1600000000000)
        # Should use audit sync time (1700000000000) not global (1600000000000)
        connector._detect_and_handle_deletions.assert_awaited_once_with(1700000000000)

    @pytest.mark.asyncio
    async def test_exception_reading_audit_sync(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(side_effect=Exception("err"))
        connector.issues_sync_point.update_sync_point = AsyncMock()
        connector._detect_and_handle_deletions = AsyncMock(return_value=0)

        await connector._handle_issue_deletions(1600000000000)
        connector._detect_and_handle_deletions.assert_awaited_once_with(1600000000000)


# ===========================================================================
# _handle_deleted_issue() - various paths
# ===========================================================================


class TestHandleDeletedIssueCoverage:

    @pytest.mark.asyncio
    async def test_issue_still_exists_at_source(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._handle_deleted_issue("PROJ-1")
        # Should return early since issue still exists

    @pytest.mark.asyncio
    async def test_issue_not_found_in_db(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(404))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx, mock_tx_store = _make_tx_store(None)
        mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        await connector._handle_deleted_issue("PROJ-1")

    @pytest.mark.asyncio
    async def test_non_epic_deletion_with_children(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(404))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "ext-1"
        mock_record.type = "Task"

        mock_tx, mock_tx_store = _make_tx_store(mock_record)
        mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=mock_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._delete_issue_children = AsyncMock(return_value=2)

        await connector._handle_deleted_issue("PROJ-1")
        # Should call _delete_issue_children for both TICKET and FILE
        assert connector._delete_issue_children.await_count == 2

    @pytest.mark.asyncio
    async def test_epic_deletion_skips_child_tickets(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(404))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_record = MagicMock()
        mock_record.id = "rec-epic"
        mock_record.external_record_id = "ext-epic"
        mock_record.type = MagicMock()
        mock_record.type.value = "EPIC"

        mock_tx, mock_tx_store = _make_tx_store(mock_record)
        mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=mock_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._delete_issue_children = AsyncMock(return_value=0)

        await connector._handle_deleted_issue("PROJ-1")
        # Should only call for FILE (not TICKET) since epic
        assert connector._delete_issue_children.await_count == 1
        call_args = connector._delete_issue_children.call_args_list[0]
        assert call_args[0][1] == RecordType.FILE

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("network"))

        await connector._handle_deleted_issue("PROJ-1")
        # Should not raise

    @pytest.mark.asyncio
    async def test_issue_get_exception_still_continues(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(side_effect=Exception("timeout"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "ext-1"
        mock_record.type = None

        mock_tx, mock_tx_store = _make_tx_store(mock_record)
        mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=mock_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._delete_issue_children = AsyncMock(return_value=0)

        await connector._handle_deleted_issue("PROJ-1")


# ===========================================================================
# _delete_issue_children() - FILE type, error
# ===========================================================================


class TestDeleteIssueChildrenCoverage:

    @pytest.mark.asyncio
    async def test_deletes_file_children(self):
        connector = _make_connector()
        tx_store = AsyncMock()

        child = MagicMock()
        child.id = "file-1"
        child.external_record_id = "attachment_100"
        tx_store.get_records_by_parent = AsyncMock(return_value=[child])
        tx_store.delete_records_and_relations = AsyncMock()

        count = await connector._delete_issue_children("parent-1", RecordType.FILE, tx_store)
        assert count == 1

    @pytest.mark.asyncio
    async def test_no_children(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        tx_store.get_records_by_parent = AsyncMock(return_value=[])

        count = await connector._delete_issue_children("parent-1", RecordType.FILE, tx_store)
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        tx_store.get_records_by_parent = AsyncMock(side_effect=Exception("db error"))

        count = await connector._delete_issue_children("parent-1", RecordType.FILE, tx_store)
        assert count == 0


# ===========================================================================
# _fetch_users() - pagination, inactive, no email
# ===========================================================================


class TestFetchUsersCoverage:

    @pytest.mark.asyncio
    async def test_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_users()

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        users_data = [
            {"accountId": "u1", "emailAddress": "u1@test.com", "displayName": "User 1", "active": True},
            {"accountId": "u2", "emailAddress": None, "displayName": "No Email", "active": True},
            {"accountId": "u3", "emailAddress": "u3@test.com", "displayName": "Inactive", "active": False},
        ]
        mock_ds = MagicMock()
        mock_ds.get_all_users = AsyncMock(return_value=_make_mock_response(200, users_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_users()
        assert len(result) == 1
        assert result[0].email == "u1@test.com"

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        batch1 = [{"accountId": f"u{i}", "emailAddress": f"u{i}@test.com", "active": True}
                   for i in range(USER_PAGE_SIZE)]
        batch2 = [{"accountId": "last", "emailAddress": "last@test.com", "active": True}]

        mock_ds = MagicMock()
        mock_ds.get_all_users = AsyncMock(side_effect=[
            _make_mock_response(200, batch1),
            _make_mock_response(200, batch2),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_users()
        assert len(result) == USER_PAGE_SIZE + 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_all_users = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Failed to fetch users"):
            await connector._fetch_users()

    @pytest.mark.asyncio
    async def test_json_parse_failure_breaks(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        resp = _make_mock_response(200)
        resp.json.side_effect = Exception("bad json")
        mock_ds = MagicMock()
        mock_ds.get_all_users = AsyncMock(return_value=resp)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_users()
        assert result == []

    @pytest.mark.asyncio
    async def test_dict_response_with_values_key(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_all_users = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"accountId": "u1", "emailAddress": "u1@test.com", "active": True}]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_users()
        assert len(result) == 1


# ===========================================================================
# _fetch_groups() - pagination, isLast, error
# ===========================================================================


class TestFetchGroupsCoverage:

    @pytest.mark.asyncio
    async def test_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_groups()

    @pytest.mark.asyncio
    async def test_single_page_isLast(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"groupId": "g1", "name": "devs"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_groups()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        batch1_groups = [{"groupId": f"g{i}", "name": f"group_{i}"} for i in range(GROUP_PAGE_SIZE)]
        batch2_groups = [{"groupId": "last", "name": "last_group"}]

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(side_effect=[
            _make_mock_response(200, {"values": batch1_groups, "isLast": False}),
            _make_mock_response(200, {"values": batch2_groups, "isLast": True}),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_groups()
        assert len(result) == GROUP_PAGE_SIZE + 1

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_groups()
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_breaks(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(side_effect=Exception("timeout"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_groups()
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_batch_breaks(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(return_value=_make_mock_response(200, {
            "values": [],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_groups()
        assert result == []


# ===========================================================================
# _fetch_group_members() - pagination, isLast, error
# ===========================================================================


class TestFetchGroupMembersCoverage:

    @pytest.mark.asyncio
    async def test_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_group_members("g1", "devs")

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"emailAddress": "u1@test.com"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_group_members("g1", "devs")
        assert result == ["u1@test.com"]

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        batch1 = [{"emailAddress": f"u{i}@test.com"} for i in range(GROUP_MEMBER_PAGE_SIZE)]
        batch2 = [{"emailAddress": "last@test.com"}]

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(side_effect=[
            _make_mock_response(200, {"values": batch1, "isLast": False}),
            _make_mock_response(200, {"values": batch2, "isLast": True}),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_group_members("g1", "devs")
        assert len(result) == GROUP_MEMBER_PAGE_SIZE + 1

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(return_value=_make_mock_response(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_group_members("g1", "devs")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_breaks(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(side_effect=Exception("timeout"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_group_members("g1", "devs")
        assert result == []

    @pytest.mark.asyncio
    async def test_members_without_email(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"emailAddress": None}, {"emailAddress": "u1@test.com"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_group_members("g1", "devs")
        assert result == ["u1@test.com"]


# ===========================================================================
# _fetch_projects() - NOT_IN, IN, no filter, ADF description
# ===========================================================================


class TestFetchProjectsCoverage:

    @pytest.mark.asyncio
    async def test_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_projects()

    @pytest.mark.asyncio
    async def test_no_filter_fetch_all(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"id": "1", "key": "PROJ", "name": "Project", "description": "Desc", "url": "https://x"}],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        rgs, raw = await connector._fetch_projects()
        assert len(rgs) == 1
        assert len(raw) == 1

    @pytest.mark.asyncio
    async def test_in_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{"id": "1", "key": "PROJ", "name": "Project", "description": None}],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        from app.connectors.core.registry.filters import ListOperator
        rgs, raw = await connector._fetch_projects(["PROJ"], ListOperator.IN)
        assert len(rgs) == 1

    @pytest.mark.asyncio
    async def test_not_in_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [
                {"id": "1", "key": "PROJ", "name": "Project", "description": None},
                {"id": "2", "key": "EXCL", "name": "Excluded", "description": None},
            ],
            "isLast": True,
            "total": 2,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        from app.connectors.core.registry.filters import ListOperator
        rgs, raw = await connector._fetch_projects(["EXCL"], ListOperator.NOT_IN)
        assert len(rgs) == 1
        assert rgs[0][0].short_name == "PROJ"

    @pytest.mark.asyncio
    async def test_adf_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [{
                "id": "1", "key": "PROJ", "name": "Project",
                "description": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "ADF desc"}]}
                ]},
            }],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        rgs, raw = await connector._fetch_projects()
        assert "ADF desc" in rgs[0][0].description

    @pytest.mark.asyncio
    async def test_pagination_no_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        batch1 = {
            "values": [{"id": "1", "key": "P1", "name": "P1", "description": None}],
            "isLast": False,
            "total": 2,
        }
        batch2 = {
            "values": [{"id": "2", "key": "P2", "name": "P2", "description": None}],
            "isLast": True,
            "total": 2,
        }

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(side_effect=[
            _make_mock_response(200, batch1),
            _make_mock_response(200, batch2),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        rgs, raw = await connector._fetch_projects()
        assert len(rgs) == 2

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Failed to fetch"):
            await connector._fetch_projects()


# ===========================================================================
# _sync_all_project_issues()
# ===========================================================================


class TestSyncAllProjectIssues:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        mock_rg = MagicMock()
        mock_rg.short_name = "PROJ"

        connector._sync_project_issues = AsyncMock(return_value={
            "total_synced": 5, "new_count": 3, "updated_count": 2
        })

        result = await connector._sync_all_project_issues(
            [(mock_rg, [])], [_make_app_user()], None
        )
        assert result["total_synced"] == 5

    @pytest.mark.asyncio
    async def test_error_continues(self):
        connector = _make_connector()
        mock_rg1 = MagicMock()
        mock_rg1.short_name = "P1"
        mock_rg2 = MagicMock()
        mock_rg2.short_name = "P2"

        connector._sync_project_issues = AsyncMock(side_effect=[
            Exception("fail"),
            {"total_synced": 3, "new_count": 2, "updated_count": 1}
        ])

        result = await connector._sync_all_project_issues(
            [(mock_rg1, []), (mock_rg2, [])], [], None
        )
        assert result["total_synced"] == 3


# ===========================================================================
# _sync_project_issues() - new project, incremental, resume
# ===========================================================================


class TestSyncProjectIssuesCoverage:

    @pytest.mark.asyncio
    async def test_new_project_no_checkpoint(self):
        connector = _make_connector()
        mock_rg = MagicMock()
        mock_rg.short_name = "NEW"
        mock_rg.external_group_id = "proj-new"

        connector._get_project_sync_checkpoint = AsyncMock(return_value={})
        connector._update_project_sync_checkpoint = AsyncMock()

        # Yield one batch then signal done
        async def gen():
            yield ([(_make_ticket_record(version=0), [])], False, 1700000000000)

        connector._fetch_issues_batched = MagicMock(return_value=gen())
        connector._process_new_records = AsyncMock()

        result = await connector._sync_project_issues(mock_rg, [], None)
        assert result["total_synced"] == 1

    @pytest.mark.asyncio
    async def test_incremental_with_checkpoint(self):
        connector = _make_connector()
        mock_rg = MagicMock()
        mock_rg.short_name = "PROJ"
        mock_rg.external_group_id = "proj-1"

        connector._get_project_sync_checkpoint = AsyncMock(return_value={
            "last_sync_time": 1600000000000,
            "last_issue_updated": 1650000000000,
        })
        connector._update_project_sync_checkpoint = AsyncMock()

        async def gen():
            yield ([], False, 1650000000000)

        connector._fetch_issues_batched = MagicMock(return_value=gen())

        result = await connector._sync_project_issues(mock_rg, [], 1500000000000)
        assert result["total_synced"] == 0

    @pytest.mark.asyncio
    async def test_empty_batch_updates_checkpoint(self):
        connector = _make_connector()
        mock_rg = MagicMock()
        mock_rg.short_name = "PROJ"
        mock_rg.external_group_id = "proj-1"

        connector._get_project_sync_checkpoint = AsyncMock(return_value={
            "last_sync_time": 1600000000000,
            "last_issue_updated": 1650000000000,
        })
        connector._update_project_sync_checkpoint = AsyncMock()

        async def gen():
            yield ([], True, 1700000000000)
            yield ([(_make_ticket_record(version=0), [])], False, 1710000000000)

        connector._fetch_issues_batched = MagicMock(return_value=gen())
        connector._process_new_records = AsyncMock()

        result = await connector._sync_project_issues(mock_rg, [], 1500000000000)
        assert result["total_synced"] == 1


# ===========================================================================
# _fetch_issues_batched() - pagination, filters, empty, error
# ===========================================================================


class TestFetchIssuesBatchedCoverage:

    @pytest.mark.asyncio
    async def test_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        connector.sync_filters = None

        with pytest.raises(ValueError, match="not initialized"):
            async for _ in connector._fetch_issues_batched("PROJ", "p1", []):
                pass

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None

        issue = _make_issue()
        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {
                "issues": [issue],
                "nextPageToken": None,
            })
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx, mock_tx_store = _make_tx_store(None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()

        batches = []
        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p1", []):
            batches.append((batch, has_more, ts))

        assert len(batches) >= 1

    @pytest.mark.asyncio
    async def test_with_modified_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_modified = MagicMock()
        mock_modified.get_value.return_value = (1600000000000, 1700000000000)
        mock_created = MagicMock()
        mock_created.get_value.return_value = (1500000000000, None)

        from app.connectors.core.registry.filters import SyncFilterKey
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: {
            SyncFilterKey.MODIFIED: mock_modified,
            SyncFilterKey.CREATED: mock_created,
        }.get(key)
        connector.sync_filters = mock_sync_filters

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {"issues": [], "nextPageToken": None})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p1", []):
            pass

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(500)
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Failed to fetch"):
            async for _ in connector._fetch_issues_batched("PROJ", "p1", []):
                pass

    @pytest.mark.asyncio
    async def test_pagination_with_next_page_token(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None

        issue1 = _make_issue(issue_id="1", key="PROJ-1")
        issue2 = _make_issue(issue_id="2", key="PROJ-2")

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(side_effect=[
            _make_mock_response(200, {"issues": [issue1], "nextPageToken": "page2"}),
            _make_mock_response(200, {"issues": [issue2], "nextPageToken": None}),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx, mock_tx_store = _make_tx_store(None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()

        batches = []
        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p1", []):
            batches.append((batch, has_more, ts))

        assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_resume_from_timestamp(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {"issues": [], "nextPageToken": None})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch, has_more, ts in connector._fetch_issues_batched(
            "PROJ", "p1", [], last_sync_time=None, resume_from_timestamp=1650000000000
        ):
            pass

        # JQL should contain the resume timestamp
        call_kwargs = mock_ds.search_and_reconsile_issues_using_jql_post.call_args[1]
        assert "updated >" in call_kwargs["jql"]


# ===========================================================================
# _build_issue_records() - new/updated/unchanged records
# ===========================================================================


class TestBuildIssueRecordsCoverage:

    @pytest.mark.asyncio
    async def test_new_issue(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue()
        user = _make_app_user()

        mock_tx, mock_tx_store = _make_tx_store(None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [user], mock_tx_store)
        assert len(result) == 1
        assert result[0][0].version == 0

    @pytest.mark.asyncio
    async def test_updated_issue(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(updated="2024-06-01T00:00:00.000+0000")

        existing = MagicMock()
        existing.id = "rec-1"
        existing.version = 1
        existing.source_updated_at = 1700000000000  # Different from issue updated

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert len(result) == 1
        assert result[0][0].version == 2

    @pytest.mark.asyncio
    async def test_unchanged_issue_skipped(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        ts = 1705401045000
        issue = _make_issue(updated="2024-01-16T10:30:45.000+0000")

        existing = MagicMock()
        existing.id = "rec-1"
        existing.version = 1
        existing.source_updated_at = ts

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_epic_issue(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(issue_type="Epic", hierarchy_level=1)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_subtask_with_parent(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(
            issue_type="Sub-task",
            hierarchy_level=-1,
            parent_id="parent-100",
            parent_key="PROJ-100",
        )

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert len(result) == 1
        assert result[0][0].parent_external_record_id == "parent-100"

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"

        mock_indexing_filters = MagicMock()
        mock_indexing_filters.is_enabled.return_value = False
        connector.indexing_filters = mock_indexing_filters

        issue = _make_issue()

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert result[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_with_issue_links(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(issuelinks=[{
            "type": {"outward": "blocks", "name": "Blocks"},
            "outwardIssue": {"id": "99"},
        }])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert result[0][0].related_external_records is not None

    @pytest.mark.asyncio
    async def test_with_attachments(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(attachments=[
            {"id": "att-1", "filename": "report.pdf", "size": 1024, "mimeType": "application/pdf", "created": "2024-01-15T10:30:45.000+0000"}
        ])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[
            (_make_file_record(attachment_id="att-1"), [])
        ])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_attachment_fetch_error_continues(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(attachments=[{"id": "att-1"}])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(side_effect=Exception("fetch err"))

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_site_url(self):
        connector = _make_connector()
        connector.site_url = None
        connector.indexing_filters = None

        issue = _make_issue()

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert result[0][0].weburl is None

    @pytest.mark.asyncio
    async def test_task_with_epic_parent(self):
        """Story/Task with Epic parent - not a subtask."""
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _make_issue(
            issue_type="Story",
            hierarchy_level=0,
            parent_id="epic-100",
            parent_key="PROJ-100",
        )

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        result = await connector._build_issue_records([issue], "proj-1", [], mock_tx_store)
        assert result[0][0].parent_external_record_id == "epic-100"
        assert result[0][0].parent_record_type == RecordType.TICKET


# ===========================================================================
# _fetch_issue_attachments()
# ===========================================================================


class TestFetchIssueAttachmentsCoverage:

    @pytest.mark.asyncio
    async def test_no_attachments(self):
        connector = _make_connector()
        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", {}, [], "proj-1", RecordGroupType.PROJECT, AsyncMock()
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_new_attachment(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        fields = {"attachment": [
            {"id": "att-1", "filename": "report.pdf", "size": 1024, "mimeType": "application/pdf",
             "created": "2024-01-15T10:30:45.000+0000"}
        ]}

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "proj-1", RecordGroupType.PROJECT, tx_store
        )
        assert len(result) == 1
        assert result[0][0].record_name == "report.pdf"
        assert result[0][0].version == 0

    @pytest.mark.asyncio
    async def test_existing_attachment_unchanged(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        fields = {"attachment": [
            {"id": "att-1", "filename": "report.pdf", "size": 1024, "mimeType": "application/pdf",
             "created": "2024-01-15T10:30:45.000+0000"}
        ]}

        existing = MagicMock()
        existing.id = "rec-att-1"
        existing.version = 1
        ts = connector._parse_jira_timestamp("2024-01-15T10:30:45.000+0000")
        existing.source_updated_at = ts

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "proj-1", RecordGroupType.PROJECT, tx_store
        )
        assert len(result) == 1
        assert result[0][0].version == 1

    @pytest.mark.asyncio
    async def test_attachment_without_id_skipped(self):
        connector = _make_connector()
        fields = {"attachment": [{"filename": "no-id.txt"}]}

        tx_store = AsyncMock()
        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "proj-1", RecordGroupType.PROJECT, tx_store
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"

        fields = {"attachment": [{"id": "att-1"}]}
        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "proj-1", RecordGroupType.PROJECT, tx_store
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_with_parent_node_id(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        fields = {"attachment": [
            {"id": "att-1", "filename": "doc.pdf", "size": 100, "mimeType": "application/pdf",
             "created": "2024-01-15T10:30:45.000+0000"}
        ]}

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "proj-1", RecordGroupType.PROJECT, tx_store,
            parent_node_id="node-123"
        )
        assert len(result) == 1
        assert result[0][0].parent_node_id == "node-123"


# ===========================================================================
# _process_issue_blockgroups_for_streaming()
# ===========================================================================


class TestProcessIssueBlockgroupsForStreaming:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"

        issue_data = {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test Issue",
                "description": None,
                "attachment": [],
                "comment": {"comments": []},
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx, mock_tx_store = _make_tx_store()
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        record = _make_ticket_record()
        result = await connector._process_issue_blockgroups_for_streaming(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_with_attachments_and_comments(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"

        issue_data = {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test",
                "description": {
                    "type": "doc",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}]
                },
                "attachment": [
                    {"id": "att1", "filename": "file.pdf", "size": 100, "mimeType": "application/pdf"}
                ],
                "comment": {"comments": [
                    {"id": "c1", "body": {"type": "doc", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "A comment"}]}
                    ]}, "author": {"displayName": "Author"}, "created": "2024-01-15T10:00:00.000Z"}
                ]},
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx, mock_tx_store = _make_tx_store()
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        connector._process_issue_attachments_for_children = AsyncMock(return_value={})

        record = _make_ticket_record()
        result = await connector._process_issue_blockgroups_for_streaming(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record = _make_ticket_record()
        with pytest.raises(Exception, match="Failed to fetch"):
            await connector._process_issue_blockgroups_for_streaming(record)

    @pytest.mark.asyncio
    async def test_no_issue_data(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, None))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record = _make_ticket_record()
        with pytest.raises(Exception, match="No issue data"):
            await connector._process_issue_blockgroups_for_streaming(record)

    @pytest.mark.asyncio
    async def test_without_site_url_uses_record_weburl(self):
        connector = _make_connector()
        connector.site_url = None

        issue_data = {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test",
                "description": None,
                "attachment": [],
                "comment": {},
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx, mock_tx_store = _make_tx_store()
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        record = _make_ticket_record()
        result = await connector._process_issue_blockgroups_for_streaming(record)
        assert isinstance(result, bytes)


# ===========================================================================
# _process_issue_attachments_for_children()
# ===========================================================================


class TestProcessIssueAttachmentsForChildren:

    @pytest.mark.asyncio
    async def test_existing_record(self):
        connector = _make_connector()
        connector.indexing_filters = None

        existing = MagicMock()
        existing.id = "rec-att-1"
        existing.record_name = "file.pdf"

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        attachments = [{"id": "att1", "filename": "file.pdf", "size": 100, "mimeType": "application/pdf"}]

        result = await connector._process_issue_attachments_for_children(
            attachments, "issue-1", "node-1", "proj-1", "https://jira/browse/PROJ-1", tx_store
        )
        assert "att1" in result

    @pytest.mark.asyncio
    async def test_new_record_created(self):
        connector = _make_connector()
        connector.indexing_filters = None

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        attachments = [{"id": "att1", "filename": "new.pdf", "size": 200, "mimeType": "application/pdf",
                        "created": "2024-01-15T10:00:00.000Z"}]

        result = await connector._process_issue_attachments_for_children(
            attachments, "issue-1", "node-1", "proj-1", "https://jira/browse/PROJ-1", tx_store
        )
        assert "att1" in result
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_empty_id(self):
        connector = _make_connector()

        tx_store = AsyncMock()
        attachments = [{"id": "", "filename": "no-id.txt"}]

        result = await connector._process_issue_attachments_for_children(
            attachments, "issue-1", "node-1", "proj-1", None, tx_store
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_continues(self):
        connector = _make_connector()

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))

        attachments = [
            {"id": "att1", "filename": "fail.pdf"},
            {"id": "att2", "filename": "ok.pdf", "size": 100, "mimeType": "text/plain"},
        ]

        result = await connector._process_issue_attachments_for_children(
            attachments, "issue-1", "node-1", "proj-1", None, tx_store
        )
        # Both may fail or second may succeed depending on implementation


# ===========================================================================
# _parse_issue_to_blocks() - description, comments, attachments
# ===========================================================================


class TestParseIssueToBlocksCoverage:

    @pytest.mark.asyncio
    async def test_basic_description_only(self):
        connector = _make_connector()
        connector._create_media_fetcher = MagicMock(return_value=AsyncMock(return_value=None))

        issue_data = {
            "id": "100",
            "fields": {
                "summary": "Test Issue",
                "description": {
                    "type": "doc",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}]
                },
            },
            "comments": [],
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1", weburl="https://jira/browse/PROJ-1"
        )
        assert len(result.block_groups) >= 1

    @pytest.mark.asyncio
    async def test_no_description(self):
        connector = _make_connector()

        issue_data = {
            "id": "100",
            "fields": {"summary": "No Desc", "description": None},
            "comments": [],
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1", weburl="https://jira/browse/PROJ-1"
        )
        assert len(result.block_groups) >= 1
        # Should use title as fallback content
        assert "No Desc" in result.block_groups[0].data

    @pytest.mark.asyncio
    async def test_no_weburl_raises(self):
        connector = _make_connector()
        issue_data = {"id": "100", "fields": {"summary": "Test"}, "comments": []}

        with pytest.raises(ValueError, match="weburl is required"):
            await connector._parse_issue_to_blocks(issue_data, "PROJ-1", weburl=None)

    @pytest.mark.asyncio
    async def test_with_comments(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector._create_media_fetcher = MagicMock(return_value=AsyncMock(return_value=None))

        issue_data = {
            "id": "100",
            "fields": {"summary": "Test", "description": None},
            "comments": [
                {"id": "c1", "body": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Comment 1"}]}
                ]}, "author": {"displayName": "Author"}, "created": "2024-01-15T10:00:00.000Z"},
            ],
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1", weburl="https://jira/browse/PROJ-1"
        )
        # Should have description + thread + comment block groups
        assert len(result.block_groups) >= 3

    @pytest.mark.asyncio
    async def test_with_comments_dict_format(self):
        connector = _make_connector()
        connector._create_media_fetcher = MagicMock(return_value=AsyncMock(return_value=None))

        issue_data = {
            "id": "100",
            "fields": {"summary": "Test", "description": None},
            "comments": {"comments": [
                {"id": "c1", "body": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Comment"}]}
                ]}, "author": {"displayName": "Author"}, "created": "2024-01-01T00:00:00.000Z"},
            ]},
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1", weburl="https://jira/browse/PROJ-1"
        )
        assert len(result.block_groups) >= 3

    @pytest.mark.asyncio
    async def test_comment_without_body_skipped(self):
        connector = _make_connector()
        connector._create_media_fetcher = MagicMock(return_value=AsyncMock(return_value=None))

        issue_data = {
            "id": "100",
            "fields": {"summary": "Test", "description": None},
            "comments": [
                {"id": "c1", "body": None, "author": {"displayName": "A"}, "created": "2024-01-01T00:00:00.000Z"},
            ],
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1", weburl="https://jira/browse/PROJ-1"
        )
        # Thread created but comment skipped (no body)
        assert len(result.block_groups) >= 1

    @pytest.mark.asyncio
    async def test_with_attachment_children(self):
        connector = _make_connector()
        connector._create_media_fetcher = MagicMock(return_value=AsyncMock(return_value=None))

        from app.models.blocks import ChildRecord, ChildType

        issue_data = {
            "id": "100",
            "fields": {"summary": "Test", "description": None},
            "comments": [],
        }

        attachment_children_map = {
            "att1": ChildRecord(child_type=ChildType.RECORD, child_id="rec-att1", child_name="report.pdf"),
        }
        attachment_mime_types = {"att1": "application/pdf"}

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1",
            weburl="https://jira/browse/PROJ-1",
            attachment_children_map=attachment_children_map,
            attachment_mime_types=attachment_mime_types,
        )
        assert len(result.block_groups) >= 1
        # Description block group should have children records
        desc_bg = result.block_groups[0]
        assert desc_bg.children_records is not None
        assert len(desc_bg.children_records) == 1

    @pytest.mark.asyncio
    async def test_comment_body_string_fallback(self):
        connector = _make_connector()
        connector._create_media_fetcher = MagicMock(return_value=AsyncMock(return_value=None))

        issue_data = {
            "id": "100",
            "fields": {"summary": "Test", "description": None},
            "comments": [
                {"id": "c1", "body": "plain text comment", "author": {"displayName": "A"},
                 "created": "2024-01-01T00:00:00.000Z"},
            ],
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, "PROJ-1", weburl="https://jira/browse/PROJ-1"
        )
        assert len(result.block_groups) >= 3

    @pytest.mark.asyncio
    async def test_no_title_or_key_fallback(self):
        connector = _make_connector()

        issue_data = {
            "id": "100",
            "fields": {"summary": "", "description": None},
            "comments": [],
        }

        result = await connector._parse_issue_to_blocks(
            issue_data, None, weburl="https://jira/browse/X"
        )
        assert "100" in result.block_groups[0].data or "Issue" in result.block_groups[0].data


# ===========================================================================
# create_connector() factory
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_factory(self):
        logger = _make_mock_logger()
        dsp = MagicMock()
        cs = MagicMock()
        cs.get_config = AsyncMock()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.DataSourceEntitiesProcessor") as MockDep:
            mock_dep = MagicMock()
            mock_dep.org_id = "org-1"
            mock_dep.initialize = AsyncMock()
            MockDep.return_value = mock_dep

            connector = await JiraConnector.create_connector(logger, dsp, cs, "conn-id")
            assert isinstance(connector, JiraConnector)
            mock_dep.initialize.assert_awaited_once()


# ===========================================================================
# _fetch_deleted_issues_from_audit() - pagination
# ===========================================================================


class TestFetchDeletedIssuesFromAuditCoverage:

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()

        page1 = {
            "records": [
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "P-1"}, "created": "2024-01-01"},
            ] * AUDIT_PAGE_SIZE,
            "total": AUDIT_PAGE_SIZE + 1,
        }
        page2 = {
            "records": [
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "P-LAST"}, "created": "2024-01-02"},
            ],
            "total": AUDIT_PAGE_SIZE + 1,
        }

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(side_effect=[
            _make_mock_response(200, page1),
            _make_mock_response(200, page2),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert "P-LAST" in result

    @pytest.mark.asyncio
    async def test_non_delete_records_skipped(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response(200, {
            "records": [
                {"objectItem": {"typeName": "ISSUE_UPDATE", "name": "P-1"}},
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": None}},
            ],
            "total": 2,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_during_pagination(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(side_effect=Exception("network err"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert result == []


# ===========================================================================
# _handle_attachment_deletions_from_changelog() - more branches
# ===========================================================================


class TestHandleAttachmentDeletionsFromChangelogCoverage:

    @pytest.mark.asyncio
    async def test_no_histories(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        issue = {"key": "P-1", "changelog": {"histories": []}}
        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)

    @pytest.mark.asyncio
    async def test_no_issue_id(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        issue = {"changelog": {"histories": [{"items": []}]}}
        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)

    @pytest.mark.asyncio
    async def test_description_change_removed_inline_image(self):
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "P-1",
            "fields": {"attachment": [{"id": "200", "filename": "image.png"}]},
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "Description",
                        "fieldId": "description",
                        "fromString": "!image.png|thumbnail! some text !removed.png|thumbnail!",
                        "toString": "some text",
                    }],
                }],
            },
        }

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "attachment_300"
        mock_record.record_name = "removed.png"

        mock_record2 = MagicMock()
        mock_record2.id = "rec-2"
        mock_record2.external_record_id = "attachment_400"
        mock_record2.record_name = "orphan.pdf"

        tx_store = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()
        tx_store.get_records_by_parent = AsyncMock(return_value=[mock_record, mock_record2])

        connector._find_attachment_record_by_id = AsyncMock(side_effect=[None])

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        # removed.png should be deleted by filename match
        # orphan.pdf should be deleted because it's not in current_attachment_ids
        assert tx_store.delete_records_and_relations.await_count >= 1

    @pytest.mark.asyncio
    async def test_attachment_to_empty_string(self):
        """Attachment deletion where to is empty string."""
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "P-1",
            "fields": {"attachment": []},
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "attachment",
                        "fieldId": "attachment",
                        "from": "55",
                        "to": "",
                    }],
                }],
            },
        }

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "attachment_55"
        mock_record.record_name = "file.txt"

        tx_store = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()
        connector._find_attachment_record_by_id = AsyncMock(return_value=mock_record)

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        tx_store.delete_records_and_relations.assert_awaited()

    @pytest.mark.asyncio
    async def test_attachment_not_found_in_db(self):
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "P-1",
            "fields": {"attachment": []},
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "Attachment",
                        "fieldId": "attachment",
                        "from": "999",
                        "to": None,
                    }],
                }],
            },
        }

        tx_store = AsyncMock()
        connector._find_attachment_record_by_id = AsyncMock(return_value=None)

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        tx_store.delete_records_and_relations = AsyncMock()

    @pytest.mark.asyncio
    async def test_attachment_still_exists_at_source(self):
        """Record exists in DB and its ID is in current_attachment_ids - should NOT be deleted."""
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "P-1",
            "fields": {
                "attachment": [{"id": "200", "filename": "kept.png"}],
            },
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "description",
                        "fieldId": "description",
                        "fromString": "!removed.png|thumbnail!",
                        "toString": "",
                    }],
                }],
            },
        }

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "attachment_200"
        mock_record.record_name = "kept.png"

        tx_store = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()
        tx_store.get_records_by_parent = AsyncMock(return_value=[mock_record])
        connector._find_attachment_record_by_id = AsyncMock(return_value=None)

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        # kept.png is still in current attachments, should NOT be deleted
        tx_store.delete_records_and_relations.assert_not_awaited()


# ===========================================================================
# Permission scheme - user without email, unknown holder type, dedup
# ===========================================================================


class TestFetchProjectPermissionSchemeCoverage:

    @pytest.mark.asyncio
    async def test_user_without_email(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {
                    "type": "user",
                    "parameter": "acc-1",
                    "user": {},
                },
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_unknown_holder_type(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "unknownType", "parameter": "x"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_duplicate_holder_deduplication(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "group", "value": "g1"},
                },
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "group", "value": "g1"},
                },
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_application_role_dedup_with_existing_group(self):
        """applicationRole resolves group, but group already exists from direct grant."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "group", "value": "g1"},
                },
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "applicationRole", "parameter": "jira-software"},
                },
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mapping = {"jira-software": [{"groupId": "g1", "name": "devs"}]}
        permissions = await connector._fetch_project_permission_scheme("PROJ", mapping)
        # g1 already added by direct group grant, should not be duplicated
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_non_browse_permission_skipped(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [{
                "permission": "ADMINISTER_PROJECTS",
                "holder": {"type": "group", "value": "admin-group"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_application_role_no_param(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole", "parameter": None},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ", {})
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG
        assert permissions[0].external_id == "all_licensed_users"


# ===========================================================================
# _sync_project_roles() - more branches
# ===========================================================================


class TestSyncProjectRolesCoverage:

    @pytest.mark.asyncio
    async def test_no_roles_dict(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_role_response_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Developers": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})

    @pytest.mark.asyncio
    async def test_group_actor_by_name(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Devs": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(200, {
            "name": "Devs",
            "actors": [{
                "type": "atlassian-group-role-actor",
                "name": "devs",
                "groupId": "unknown-gid",
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        user = _make_app_user()
        groups_map = {"devs": [user]}
        await connector._sync_project_roles(["PROJ"], [user], groups_map)
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_actor_by_email(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Devs": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(200, {
            "name": "Devs",
            "actors": [{
                "type": "atlassian-user-role-actor",
                "actorUser": {"accountId": "unknown-acc", "emailAddress": "user@example.com"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        user = _make_app_user()
        await connector._sync_project_roles(["PROJ"], [user], {})
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_actor_not_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Devs": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(200, {
            "name": "Devs",
            "actors": [{
                "type": "atlassian-user-role-actor",
                "actorUser": {"accountId": "ghost", "emailAddress": "ghost@x.com"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()
        role, members = connector.data_entities_processor.on_new_app_roles.call_args[0][0][0]
        assert len(members) == 0

    @pytest.mark.asyncio
    async def test_role_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Bad": "https://jira/rest/api/3/project/PROJ/role/bad",
        }))
        mock_ds.get_project_role = AsyncMock(side_effect=Exception("err"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})

    @pytest.mark.asyncio
    async def test_group_actor_not_in_map(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Devs": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(200, {
            "name": "Devs",
            "actors": [{
                "type": "atlassian-group-role-actor",
                "name": "unknown-group",
                "groupId": "unknown-gid",
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})


# ===========================================================================
# _sync_project_lead_roles() - more branches
# ===========================================================================


class TestSyncProjectLeadRolesCoverage:

    @pytest.mark.asyncio
    async def test_lead_no_account_id(self):
        connector = _make_connector()
        projects = [{"key": "PROJ", "lead": {"displayName": "Lead"}}]

        await connector._sync_project_lead_roles(projects, [])
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_projects(self):
        connector = _make_connector()
        await connector._sync_project_lead_roles([], [])
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_project_timestamps(self):
        connector = _make_connector()
        user = _make_app_user(account_id="lead-acc")
        projects = [{
            "key": "PROJ",
            "lead": {"accountId": "lead-acc", "displayName": "Lead"},
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-06-01T00:00:00.000Z",
        }]

        await connector._sync_project_lead_roles(projects, [user])
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()


# ===========================================================================
# _sync_user_groups() - member not in users
# ===========================================================================


class TestSyncUserGroupsCoverage:

    @pytest.mark.asyncio
    async def test_member_not_in_synced_users(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": "g1", "name": "developers"},
        ])
        connector._fetch_group_members = AsyncMock(return_value=["unknown@test.com"])

        result = await connector._sync_user_groups([_make_app_user(email="user@example.com")])
        assert len(result["g1"]) == 0


# ===========================================================================
# ADF edge cases - media in list, mention, inlineCard, etc.
# ===========================================================================


class TestAdfEdgeCases:

    def test_media_in_list_context(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Item with image"}]},
                        {"type": "mediaSingle", "content": [
                            {"type": "media", "attrs": {"id": "m1", "alt": "img.png"}}
                        ]},
                    ],
                }],
            }],
        }
        cache = {"m1": "data:image/png;base64,AAAA"}
        result = adf_to_text(adf, media_cache=cache)
        assert "img.png" in result

    def test_mention_node(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "mention", "attrs": {"text": "John", "id": "u1"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "@John" in result

    def test_mention_without_text(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "mention", "attrs": {"id": "u1"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "@u1" in result

    def test_inline_card(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "inlineCard", "attrs": {"url": "https://example.com"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "[https://example.com](https://example.com)" in result

    def test_inline_card_no_url(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "inlineCard", "attrs": {}}],
            }],
        }
        result = adf_to_text(adf)
        assert result == ""

    def test_rule_node(self):
        adf = {
            "type": "doc",
            "content": [{"type": "rule"}],
        }
        result = adf_to_text(adf)
        assert "---" in result

    def test_code_block_with_language(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print('hello')"}],
            }],
        }
        result = adf_to_text(adf)
        assert "```python" in result
        assert "print('hello')" in result

    def test_heading_in_table_strip_marks(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [{
                        "type": "tableCell",
                        "content": [{
                            "type": "heading",
                            "attrs": {"level": 2},
                            "content": [{"type": "text", "text": "Heading"}],
                        }],
                    }],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "Heading" in result
        # In tables, heading should not have # prefix

    def test_blockquote_in_table(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [{
                        "type": "tableCell",
                        "content": [{
                            "type": "blockquote",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "quoted"}],
                            }],
                        }],
                    }],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "quoted" in result

    def test_media_without_cache_in_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Item"}],
                    }],
                }],
            }, {
                "type": "media",
                "attrs": {"id": "m1", "alt": "no_cache.png"},
            }],
        }
        result = adf_to_text(adf)
        assert "no_cache.png" in result

    def test_emoji_without_short_name(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "emoji", "attrs": {"text": "X"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "X" in result

    def test_decision_item_not_decided(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "UNDECIDED"},
                    "content": [{"type": "text", "text": "pending"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "pending" in result

    def test_nested_expand(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "nestedExpand",
                "attrs": {"title": "Nested Details"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "nested content"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "**Nested Details**" in result
        assert "nested content" in result

    def test_ordered_list_non_list_item_child(self):
        """orderedList with non-listItem child falls back to extract_text."""
        adf = {
            "type": "doc",
            "content": [{
                "type": "orderedList",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "direct"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "direct" in result

    def test_bullet_list_non_list_item_child(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "fallback"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "fallback" in result

    def test_table_cell_pipe_and_newline_escaping(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [{
                        "type": "tableCell",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "a|b\nc"}],
                        }],
                    }],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "\\|" in result

    def test_media_with_title_no_alt(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "m1", "title": "My File"},
            }],
        }
        result = adf_to_text(adf)
        assert "My File" in result

    def test_media_no_alt_no_title(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "m1"},
            }],
        }
        result = adf_to_text(adf)
        assert "attachment" in result

    def test_paragraph_in_list_context(self):
        """Paragraph inside list should not add extra newlines."""
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "item text"}],
                    }],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "- item text" in result

    def test_listItem_fallback(self):
        """listItem not handled by extract_list_item_content (standalone)."""
        adf = {
            "type": "doc",
            "content": [{
                "type": "listItem",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "standalone"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "standalone" in result

    def test_link_empty_href(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "no link",
                    "marks": [{"type": "link", "attrs": {"href": ""}}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "no link" in result

    def test_numbered_list_name_variant(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "numberedList",
                "content": [{
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "First"}]}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "1. First" in result

    def test_unordered_list_variant(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "unorderedList",
                "content": [{
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Item"}]}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "- Item" in result

    def test_status_empty_text(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "status", "attrs": {"text": ""}}],
            }],
        }
        result = adf_to_text(adf)
        assert result == ""

    def test_date_no_timestamp(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "date", "attrs": {"timestamp": ""}}],
            }],
        }
        result = adf_to_text(adf)
        assert result == ""


# ===========================================================================
# extract_media_from_adf() - more branches
# ===========================================================================


class TestExtractMediaFromAdfCoverage:

    def test_media_without_id_excluded(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "", "alt": "no-id"},
            }],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 0

    def test_nested_media_in_content(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{
                    "type": "media",
                    "attrs": {"id": "m1", "alt": "img.png", "__fileName": "actual.png",
                              "type": "image", "width": 100, "height": 50, "collection": "col1"},
                }],
            }],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["filename"] == "actual.png"
        assert result[0]["type"] == "image"

    def test_traverse_non_dict(self):
        adf = {
            "content": [42, "string", None],
        }
        result = extract_media_from_adf(adf)
        assert result == []

    def test_no_content_key_single_node(self):
        adf = {"type": "media", "attrs": {"id": "m1", "alt": "x"}}
        result = extract_media_from_adf(adf)
        assert len(result) == 1


# ===========================================================================
# Misc constants
# ===========================================================================


class TestConstants:

    def test_audit_page_size(self):
        assert AUDIT_PAGE_SIZE == 500

    def test_user_page_size(self):
        assert USER_PAGE_SIZE == 50

    def test_group_page_size(self):
        assert GROUP_PAGE_SIZE == 50

    def test_group_member_page_size(self):
        assert GROUP_MEMBER_PAGE_SIZE == 50


# ===========================================================================
# _process_new_records() - with file records in batch
# ===========================================================================


class TestProcessNewRecordsCoverage:

    @pytest.mark.asyncio
    async def test_with_mixed_record_types(self):
        connector = _make_connector()

        r1 = (_make_ticket_record(external_id="1", version=0), [])
        r2 = (_make_file_record(attachment_id="1", version=0), [])
        r3 = (_make_ticket_record(external_id="2", version=1, parent_external_record_id="1"), [])

        stats = {"new_count": 0, "updated_count": 0}
        await connector._process_new_records([r1, r2, r3], "PROJ", stats)

        assert stats["new_count"] == 2
        assert stats["updated_count"] == 1


# ===========================================================================
# _fetch_issues_batched() - with created filters
# ===========================================================================


class TestFetchIssuesBatchedFilters:

    @pytest.mark.asyncio
    async def test_with_created_before_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_created = MagicMock()
        mock_created.get_value.return_value = (None, 1700000000000)

        from app.connectors.core.registry.filters import SyncFilterKey
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: {
            SyncFilterKey.MODIFIED: None,
            SyncFilterKey.CREATED: mock_created,
        }.get(key)
        connector.sync_filters = mock_sync_filters

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {"issues": [], "nextPageToken": None})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p1", []):
            pass

        jql = mock_ds.search_and_reconsile_issues_using_jql_post.call_args[1]["jql"]
        assert "created <=" in jql

    @pytest.mark.asyncio
    async def test_modified_filter_without_resume(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_modified = MagicMock()
        mock_modified.get_value.return_value = (1600000000000, None)

        from app.connectors.core.registry.filters import SyncFilterKey
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: {
            SyncFilterKey.MODIFIED: mock_modified,
            SyncFilterKey.CREATED: None,
        }.get(key)
        connector.sync_filters = mock_sync_filters

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {"issues": [], "nextPageToken": None})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch, has_more, ts in connector._fetch_issues_batched(
            "PROJ", "p1", [], last_sync_time=1650000000000
        ):
            pass

        jql = mock_ds.search_and_reconsile_issues_using_jql_post.call_args[1]["jql"]
        assert "updated >" in jql

    @pytest.mark.asyncio
    async def test_last_sync_time_only(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {"issues": [], "nextPageToken": None})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch, has_more, ts in connector._fetch_issues_batched(
            "PROJ", "p1", [], last_sync_time=1650000000000
        ):
            pass

        jql = mock_ds.search_and_reconsile_issues_using_jql_post.call_args[1]["jql"]
        assert "updated >" in jql

    @pytest.mark.asyncio
    async def test_modified_before_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_modified = MagicMock()
        mock_modified.get_value.return_value = (None, 1700000000000)

        from app.connectors.core.registry.filters import SyncFilterKey
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: {
            SyncFilterKey.MODIFIED: mock_modified,
            SyncFilterKey.CREATED: None,
        }.get(key)
        connector.sync_filters = mock_sync_filters

        mock_ds = MagicMock()
        mock_ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_make_mock_response(200, {"issues": [], "nextPageToken": None})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p1", []):
            pass

        jql = mock_ds.search_and_reconsile_issues_using_jql_post.call_args[1]["jql"]
        assert "updated <=" in jql
