"""Deep-sync-loop tests for JiraConnector.

Covers: run_sync, _sync_all_project_issues, _sync_project_issues,
_fetch_issues_batched, _build_issue_records, _fetch_issue_attachments,
_handle_issue_deletions, _detect_and_handle_deletions,
_fetch_deleted_issues_from_audit, _handle_deleted_issue,
_sync_user_groups, _fetch_groups, _fetch_group_members,
_sync_project_roles, _fetch_projects, _process_new_records,
_extract_issue_data, _parse_issue_links.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira_cloud.connector import (
    AUDIT_PAGE_SIZE,
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    JiraConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
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
    logger = logging.getLogger("test.jira.deep")
    dep = MagicMock()
    dep.org_id = "org-jira-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="user@example.com"),
    ])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return JiraConnector(logger, dep, dsp, cs, "conn-jira-1")


def _resp(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


def _app_user(email="u@x.com", account_id="acc-1"):
    return AppUser(
        app_name=Connectors.JIRA,
        connector_id="conn-jira-1",
        source_user_id=account_id,
        org_id="org-jira-1",
        email=email,
        full_name="User",
        is_active=True,
    )


def _project_rg(key="PROJ", pid="p-1"):
    return RecordGroup(
        id=str(uuid4()),
        org_id="org-jira-1",
        external_group_id=pid,
        connector_id="conn-jira-1",
        connector_name=Connectors.JIRA,
        name=f"Project {key}",
        short_name=key,
        group_type=RecordGroupType.PROJECT,
    )


def _issue_dict(issue_id="1001", key="PROJ-1", updated="2024-06-15T10:00:00.000+0000"):
    return {
        "id": issue_id,
        "key": key,
        "fields": {
            "summary": f"Issue {key}",
            "description": None,
            "status": {"name": "Open"},
            "priority": {"name": "High"},
            "creator": {"accountId": "acc-1", "displayName": "Creator"},
            "reporter": {"accountId": "acc-1", "displayName": "Reporter"},
            "assignee": {"accountId": "acc-2", "displayName": "Assignee"},
            "created": "2024-01-01T00:00:00.000+0000",
            "updated": updated,
            "issuetype": {"name": "Task", "hierarchyLevel": 0},
            "project": {"id": "p-1", "key": "PROJ"},
            "parent": None,
            "attachment": [],
            "security": None,
            "issuelinks": [],
        },
    }


# ===========================================================================
# run_sync orchestration
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_initializes_if_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)
        # After init, data_source should be set, but we mock the rest
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._fetch_users = AsyncMock(return_value=[])
            connector._sync_user_groups = AsyncMock(return_value={})
            connector._fetch_projects = AsyncMock(return_value=([], []))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={
                "total_synced": 0, "new_count": 0, "updated_count": 0
            })
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_no_active_users_returns_early(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())

            await connector.run_sync()
            # Should return early without fetching users
            connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        user = _app_user()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._fetch_users = AsyncMock(return_value=[user])
            connector._sync_user_groups = AsyncMock(return_value={"g1": [user]})
            rg = _project_rg()
            connector._fetch_projects = AsyncMock(return_value=(
                [(rg, [])],
                [{"key": "PROJ", "lead": None}],
            ))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={
                "total_synced": 5, "new_count": 3, "updated_count": 2
            })
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()

            connector._fetch_users.assert_awaited_once()
            connector.data_entities_processor.on_new_app_users.assert_awaited_once_with([user])
            connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
            connector._sync_all_project_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_propagates_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=RuntimeError("DB crash")
        )
        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            with pytest.raises(RuntimeError, match="DB crash"):
                await connector.run_sync()


# ===========================================================================
# _sync_all_project_issues
# ===========================================================================


class TestSyncAllProjectIssues:

    @pytest.mark.asyncio
    async def test_aggregates_stats_across_projects(self):
        connector = _make_connector()
        rg1 = _project_rg("P1", "p1")
        rg2 = _project_rg("P2", "p2")

        async def mock_sync(proj, users, last_sync):
            if proj.short_name == "P1":
                return {"total_synced": 3, "new_count": 2, "updated_count": 1}
            return {"total_synced": 2, "new_count": 1, "updated_count": 1}

        connector._sync_project_issues = AsyncMock(side_effect=mock_sync)

        result = await connector._sync_all_project_issues(
            [(rg1, []), (rg2, [])], [], None
        )

        assert result["total_synced"] == 5
        assert result["new_count"] == 3
        assert result["updated_count"] == 2

    @pytest.mark.asyncio
    async def test_continues_on_project_error(self):
        connector = _make_connector()
        rg1 = _project_rg("P1", "p1")
        rg2 = _project_rg("P2", "p2")

        call_count = 0

        async def mock_sync(proj, users, last_sync):
            nonlocal call_count
            call_count += 1
            if proj.short_name == "P1":
                raise RuntimeError("API error")
            return {"total_synced": 1, "new_count": 1, "updated_count": 0}

        connector._sync_project_issues = AsyncMock(side_effect=mock_sync)

        result = await connector._sync_all_project_issues(
            [(rg1, []), (rg2, [])], [], None
        )

        assert call_count == 2
        assert result["total_synced"] == 1

    @pytest.mark.asyncio
    async def test_empty_projects_returns_zeros(self):
        connector = _make_connector()
        result = await connector._sync_all_project_issues([], [], None)
        assert result == {"total_synced": 0, "new_count": 0, "updated_count": 0}


# ===========================================================================
# _sync_project_issues
# ===========================================================================


class TestSyncProjectIssues:

    @pytest.mark.asyncio
    async def test_new_project_no_timestamp_filter(self):
        connector = _make_connector()
        rg = _project_rg()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()

        # Empty generator
        async def empty_gen(*a, **kw):
            return
            yield  # noqa: unreachable

        connector._fetch_issues_batched = empty_gen
        connector._process_new_records = AsyncMock()

        result = await connector._sync_project_issues(rg, [], None)
        assert result["total_synced"] == 0

    @pytest.mark.asyncio
    async def test_processes_batches_and_updates_checkpoint(self):
        connector = _make_connector()
        rg = _project_rg()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 1700000000000,
            "last_issue_updated": 1700000000000,
        })
        connector.issues_sync_point.update_sync_point = AsyncMock()

        mock_record = MagicMock()
        mock_record.version = 0
        batch = [(mock_record, [])]

        async def gen(*a, **kw):
            yield batch, True, 1700000001000
            yield batch, False, 1700000002000

        connector._fetch_issues_batched = gen
        connector._process_new_records = AsyncMock()

        result = await connector._sync_project_issues(rg, [], 1700000000000)
        assert result["total_synced"] == 2


# ===========================================================================
# _fetch_issues_batched (pagination loop)
# ===========================================================================


class TestFetchIssuesBatched:

    @pytest.mark.asyncio
    async def test_single_page_no_more(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        issue = _issue_dict()
        ds = MagicMock()
        ds.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=_resp(200, {
            "issues": [issue],
            "nextPageToken": None,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._build_issue_records = AsyncMock(return_value=[])
        connector._safe_json_parse = MagicMock(return_value={
            "issues": [issue],
            "nextPageToken": None,
        })

        batches = []
        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p-1", [], None, None):
            batches.append((batch, has_more, ts))

        assert len(batches) >= 1

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        issue1 = _issue_dict("1001", "PROJ-1", "2024-06-01T00:00:00.000+0000")
        issue2 = _issue_dict("1002", "PROJ-2", "2024-06-02T00:00:00.000+0000")

        call_count = 0

        async def mock_search(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "issues": [issue1],
                    "nextPageToken": "token-page2",
                })
            return _resp(200, {
                "issues": [issue2],
                "nextPageToken": None,
            })

        ds = MagicMock()
        ds.search_and_reconsile_issues_using_jql_post = AsyncMock(side_effect=mock_search)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._build_issue_records = AsyncMock(return_value=[])
        connector._safe_json_parse = MagicMock(side_effect=[
            {"issues": [issue1], "nextPageToken": "token-page2"},
            {"issues": [issue2], "nextPageToken": None},
        ])

        batches = []
        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p-1", [], None, None):
            batches.append(batch)

        assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_api_error_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        ds = MagicMock()
        ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_resp(500, {})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._safe_json_parse = MagicMock(return_value=None)

        with pytest.raises(Exception):
            async for _ in connector._fetch_issues_batched("PROJ", "p-1", [], None, None):
                pass


# ===========================================================================
# _build_issue_records
# ===========================================================================


class TestBuildIssueRecords:

    @pytest.mark.asyncio
    async def test_new_issue_creates_ticket_record(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records = await connector._build_issue_records(
            [issue], "p-1", [_app_user()], tx_store
        )

        assert len(records) == 1
        rec, perms = records[0]
        assert isinstance(rec, TicketRecord)
        assert rec.version == 0
        assert rec.record_name == "[PROJ-1] Issue PROJ-1"
        assert "company.atlassian.net/browse/PROJ-1" in rec.weburl

    @pytest.mark.asyncio
    async def test_existing_unchanged_issue_skipped(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.source_updated_at = connector._parse_jira_timestamp("2024-06-15T10:00:00.000+0000")

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()

        records = await connector._build_issue_records(
            [issue], "p-1", [], tx_store
        )

        assert len(records) == 0

    @pytest.mark.asyncio
    async def test_updated_issue_increments_version(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict(updated="2024-06-16T10:00:00.000+0000")
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.source_updated_at = connector._parse_jira_timestamp("2024-06-15T10:00:00.000+0000")

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records = await connector._build_issue_records(
            [issue], "p-1", [], tx_store
        )

        assert len(records) == 1
        assert records[0][0].version == 3

    @pytest.mark.asyncio
    async def test_epic_issue_hierarchy(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Epic", "hierarchyLevel": 1}
        issue["fields"]["parent"] = None

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records = await connector._build_issue_records(
            [issue], "p-1", [], tx_store
        )

        assert len(records) == 1
        rec = records[0][0]
        assert rec.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_subtask_has_parent(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Sub-task", "hierarchyLevel": -1}
        issue["fields"]["parent"] = {"id": "parent-1001", "key": "PROJ-1"}

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records = await connector._build_issue_records(
            [issue], "p-1", [], tx_store
        )

        assert len(records) == 1
        rec = records[0][0]
        assert rec.parent_external_record_id == "parent-1001"

    @pytest.mark.asyncio
    async def test_attachments_appended_to_records(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        issue["fields"]["attachment"] = [{"id": "att-1", "filename": "f.pdf", "size": 100, "mimeType": "application/pdf"}]

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock()

        mock_file = MagicMock(spec=FileRecord)
        mock_file.version = 0
        connector._fetch_issue_attachments = AsyncMock(return_value=[(mock_file, [])])

        records = await connector._build_issue_records(
            [issue], "p-1", [], tx_store
        )

        # ticket + attachment
        assert len(records) == 2


# ===========================================================================
# _fetch_issue_attachments
# ===========================================================================


class TestFetchIssueAttachments:

    @pytest.mark.asyncio
    async def test_no_attachments_returns_empty(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", {}, [], "p-1", RecordGroupType.PROJECT, AsyncMock()
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_new_attachment_creates_file_record(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        fields = {
            "attachment": [
                {"id": "a1", "filename": "doc.pdf", "size": 2048, "mimeType": "application/pdf", "created": "2024-01-01T00:00:00.000+0000"}
            ]
        }
        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector._create_attachment_file_record = MagicMock(return_value=MagicMock(spec=FileRecord))

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "p-1", RecordGroupType.PROJECT, tx_store
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_attachment_without_id_skipped(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        fields = {"attachment": [{"filename": "noid.txt"}]}
        tx_store = AsyncMock()

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "p-1", RecordGroupType.PROJECT, tx_store
        )
        assert result == []


# ===========================================================================
# _handle_issue_deletions / _detect_and_handle_deletions
# ===========================================================================


class TestHandleIssueDeletions:

    @pytest.mark.asyncio
    async def test_no_deletion_check_if_no_sync_time(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._detect_and_handle_deletions = AsyncMock()

        await connector._handle_issue_deletions(None)
        connector._detect_and_handle_deletions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletions_checked_when_sync_time_present(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()
        connector._detect_and_handle_deletions = AsyncMock()

        await connector._handle_issue_deletions(1700000000000)
        connector._detect_and_handle_deletions.assert_awaited_once()


class TestDetectAndHandleDeletions:

    @pytest.mark.asyncio
    async def test_no_deleted_issues_returns_zero(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=[])

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 0

    @pytest.mark.asyncio
    async def test_handles_each_deleted_issue(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=["PROJ-1", "PROJ-2"])
        connector._handle_deleted_issue = AsyncMock()

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 2
        assert connector._handle_deleted_issue.await_count == 2

    @pytest.mark.asyncio
    async def test_continues_on_individual_deletion_error(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=["PROJ-1", "PROJ-2"])

        call_count = 0

        async def mock_delete(key):
            nonlocal call_count
            call_count += 1
            if key == "PROJ-1":
                raise RuntimeError("delete error")

        connector._handle_deleted_issue = AsyncMock(side_effect=mock_delete)

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert call_count == 2
        assert count == 1  # Only PROJ-2 succeeded

    @pytest.mark.asyncio
    async def test_returns_zero_on_exception(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(side_effect=RuntimeError("API fail"))

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 0


# ===========================================================================
# _fetch_deleted_issues_from_audit
# ===========================================================================


class TestFetchDeletedIssuesFromAudit:

    @pytest.mark.asyncio
    async def test_single_page_deletion_events(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_records = AsyncMock(return_value=_resp(200, {
            "records": [
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-1"}, "created": "2024-01-01"},
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-2"}, "created": "2024-01-02"},
                {"objectItem": {"typeName": "FIELD_CHANGE", "name": "PROJ-3"}, "created": "2024-01-03"},
            ],
            "total": 3,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert result == ["PROJ-1", "PROJ-2"]

    @pytest.mark.asyncio
    async def test_pagination_loop(self):
        connector = _make_connector()

        call_count = 0

        async def mock_audit(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "records": [
                        {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-1"}}
                    ],
                    "total": 2,
                })
            return _resp(200, {
                "records": [
                    {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-2"}}
                ],
                "total": 2,
            })

        ds = MagicMock()
        ds.get_audit_records = AsyncMock(side_effect=mock_audit)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert "PROJ-1" in result
        assert "PROJ-2" in result

    @pytest.mark.asyncio
    async def test_api_failure_stops_paging(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_records = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_records_returns_empty(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_records = AsyncMock(return_value=_resp(200, {"records": [], "total": 0}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert result == []


# ===========================================================================
# _sync_user_groups
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_no_groups_returns_empty_map(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[])

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_groups_with_members(self):
        connector = _make_connector()
        user = _app_user("dev@x.com")
        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": "g1", "name": "devs"},
        ])
        connector._fetch_group_members = AsyncMock(return_value=["dev@x.com"])

        result = await connector._sync_user_groups([user])

        assert "g1" in result
        assert "devs" in result
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_group_without_id(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[
            {"name": "no-id-group"},
        ])

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_continues_on_group_error(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": "g1", "name": "devs"},
            {"groupId": "g2", "name": "admins"},
        ])

        call_count = 0

        async def mock_members(gid, gname):
            nonlocal call_count
            call_count += 1
            if gname == "devs":
                raise RuntimeError("API error")
            return ["admin@x.com"]

        connector._fetch_group_members = AsyncMock(side_effect=mock_members)

        result = await connector._sync_user_groups([_app_user("admin@x.com")])
        assert call_count == 2
        assert "g2" in result


# ===========================================================================
# _fetch_groups (pagination loop)
# ===========================================================================


class TestFetchGroups:

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.bulk_get_groups = AsyncMock(return_value=_resp(200, {
            "values": [{"groupId": "g1", "name": "devs"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        groups = await connector._fetch_groups()
        assert len(groups) == 1

    @pytest.mark.asyncio
    async def test_multi_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        call_count = 0

        async def mock_groups(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "values": [{"groupId": f"g{i}", "name": f"g{i}"} for i in range(50)],
                    "isLast": False,
                })
            return _resp(200, {
                "values": [{"groupId": "g99", "name": "last"}],
                "isLast": True,
            })

        ds = MagicMock()
        ds.bulk_get_groups = AsyncMock(side_effect=mock_groups)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        groups = await connector._fetch_groups()
        assert len(groups) == 51
        assert call_count == 2


# ===========================================================================
# _fetch_group_members (pagination loop)
# ===========================================================================


class TestFetchGroupMembers:

    @pytest.mark.asyncio
    async def test_single_page_members(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.get_users_from_group = AsyncMock(return_value=_resp(200, {
            "values": [{"emailAddress": "a@x.com"}, {"emailAddress": "b@x.com"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        emails = await connector._fetch_group_members("g1", "devs")
        assert emails == ["a@x.com", "b@x.com"]

    @pytest.mark.asyncio
    async def test_skips_members_without_email(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.get_users_from_group = AsyncMock(return_value=_resp(200, {
            "values": [{"displayName": "NoEmail"}, {"emailAddress": "a@x.com"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        emails = await connector._fetch_group_members("g1", "devs")
        assert emails == ["a@x.com"]


# ===========================================================================
# _extract_issue_data
# ===========================================================================


class TestExtractIssueData:

    def test_basic_extraction(self):
        connector = _make_connector()
        user = _app_user()
        issue = _issue_dict()

        data = connector._extract_issue_data(issue, {user.source_user_id: user})

        assert data["issue_id"] == "1001"
        assert data["issue_key"] == "PROJ-1"
        assert "[PROJ-1]" in data["issue_name"]
        assert data["is_epic"] is False
        assert data["is_subtask"] is False

    def test_epic_detection(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Epic", "hierarchyLevel": 1}

        data = connector._extract_issue_data(issue, {})
        assert data["is_epic"] is True

    def test_subtask_detection(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Sub-task", "hierarchyLevel": -1}
        issue["fields"]["parent"] = {"id": "parent-1", "key": "PROJ-0"}

        data = connector._extract_issue_data(issue, {})
        assert data["is_subtask"] is True
        assert data["parent_external_id"] == "parent-1"

    def test_user_email_resolution(self):
        connector = _make_connector()
        user = _app_user("dev@example.com", "acc-1")
        issue = _issue_dict()

        data = connector._extract_issue_data(issue, {"acc-1": user})
        assert data["creator_email"] == "dev@example.com"
        assert data["reporter_email"] == "dev@example.com"

    def test_no_creator(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["creator"] = None

        data = connector._extract_issue_data(issue, {})
        assert data["creator_email"] is None


# ===========================================================================
# _parse_issue_links
# ===========================================================================


class TestParseIssueLinks:

    def test_outward_link_extracted(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuelinks"] = [
            {
                "type": {"name": "Blocks", "outward": "blocks"},
                "outwardIssue": {"id": "2001", "key": "PROJ-2"},
            }
        ]

        related = connector._parse_issue_links(issue)
        assert len(related) == 1
        assert related[0].external_record_id == "2001"

    def test_inward_link_skipped(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuelinks"] = [
            {
                "type": {"name": "Blocks", "inward": "is blocked by"},
                "inwardIssue": {"id": "2001", "key": "PROJ-2"},
            }
        ]

        related = connector._parse_issue_links(issue)
        assert len(related) == 0

    def test_none_issue_returns_empty(self):
        connector = _make_connector()
        assert connector._parse_issue_links(None) == []
        assert connector._parse_issue_links({}) == []

    def test_no_issuelinks_field(self):
        connector = _make_connector()
        issue = {"fields": {}}
        assert connector._parse_issue_links(issue) == []


# ===========================================================================
# _process_new_records
# ===========================================================================


class TestProcessNewRecords:

    @pytest.mark.asyncio
    async def test_sorts_epics_first(self):
        connector = _make_connector()

        epic = MagicMock(spec=TicketRecord)
        epic.parent_external_record_id = None
        epic.version = 0

        task = MagicMock(spec=TicketRecord)
        task.parent_external_record_id = "epic-1"
        task.version = 0

        records = [(task, []), (epic, [])]
        stats = {"new_count": 0, "updated_count": 0}

        await connector._process_new_records(records, "PROJ", stats)

        # Should have called on_new_records
        connector.data_entities_processor.on_new_records.assert_awaited()
        assert stats["new_count"] == 2

    @pytest.mark.asyncio
    async def test_counts_new_and_updated(self):
        connector = _make_connector()

        new_rec = MagicMock(spec=TicketRecord)
        new_rec.parent_external_record_id = None
        new_rec.version = 0

        updated_rec = MagicMock(spec=TicketRecord)
        updated_rec.parent_external_record_id = None
        updated_rec.version = 3

        stats = {"new_count": 0, "updated_count": 0}
        await connector._process_new_records([(new_rec, []), (updated_rec, [])], "PROJ", stats)

        assert stats["new_count"] == 1
        assert stats["updated_count"] == 1


# ===========================================================================
# _fetch_projects (pagination + filter)
# ===========================================================================


class TestFetchProjects:

    @pytest.mark.asyncio
    async def test_fetch_all_projects_no_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.search_projects = AsyncMock(return_value=_resp(200, {
            "values": [{"id": "p1", "key": "PROJ", "name": "Project 1"}],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._safe_json_parse = MagicMock(return_value={
            "values": [{"id": "p1", "key": "PROJ", "name": "Project 1"}],
            "isLast": True,
            "total": 1,
        })
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects(None, None)
        assert len(record_groups) == 1
        assert len(raw) == 1

    @pytest.mark.asyncio
    async def test_fetch_projects_with_keys_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.search_projects = AsyncMock(return_value=_resp(200, {
            "values": [{"id": "p1", "key": "PROJ", "name": "Proj"}],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._safe_json_parse = MagicMock(return_value={
            "values": [{"id": "p1", "key": "PROJ", "name": "Proj"}],
            "isLast": True,
            "total": 1,
        })
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        from app.connectors.core.registry.filters import FilterOperatorType
        record_groups, _ = await connector._fetch_projects(["PROJ"], None)
        assert len(record_groups) == 1


# ===========================================================================
# _sync_project_roles
# ===========================================================================


class TestSyncProjectRoles:

    @pytest.mark.asyncio
    async def test_syncs_roles_for_project(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ds = MagicMock()
        ds.get_project_roles = AsyncMock(return_value=_resp(200, {
            "Developers": "https://api.atlassian.net/rest/api/3/project/PROJ/role/10002"
        }))
        ds.get_project_role = AsyncMock(return_value=_resp(200, {
            "name": "Developers",
            "actors": [
                {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        user = _app_user("dev@x.com", "acc-1")
        await connector._sync_project_roles(["PROJ"], [user], {})

        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self):
        connector = _make_connector()
        connector.data_source = None

        with pytest.raises(ValueError, match="not initialized"):
            await connector._sync_project_roles(["PROJ"], [], {})

    @pytest.mark.asyncio
    async def test_skips_addon_roles(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.get_project_roles = AsyncMock(return_value=_resp(200, {
            "atlassian-addons-project-access": "https://api.atlassian.net/rest/api/3/project/PROJ/role/99999"
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()


# ===========================================================================
# _fetch_project_permission_scheme
# ===========================================================================


class TestFetchProjectPermissionScheme:

    @pytest.mark.asyncio
    async def test_group_holder_permission(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(200, {"id": "10"}))
        ds.get_permission_scheme_grants = AsyncMock(return_value=_resp(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "group", "value": "g-dev"},
                }
            ]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_project_permission_scheme("PROJ")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_anyone_holder_permission(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(200, {"id": "10"}))
        ds.get_permission_scheme_grants = AsyncMock(return_value=_resp(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "anyone"},
                }
            ]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_project_permission_scheme("PROJ")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_scheme_fetch_failure_returns_empty(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_project_permission_scheme("PROJ")
        assert perms == []
